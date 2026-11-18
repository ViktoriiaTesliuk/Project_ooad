"""Preview and atomically confirm a wallet CSV import."""

import csv
import io
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from secrets import token_urlsafe

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db, ledger
from .auth import login_required

bp = Blueprint("csv_import", __name__, url_prefix="/transactions/import")
MAX_BYTES = 128 * 1024
MAX_ROWS = 100
REQUIRED_COLUMNS = {"date", "kind", "amount", "currency", "category"}
OPTIONAL_COLUMNS = {"description"}


class ImportError(ValueError):
    pass


def _monobank_rows(text, currency):
    """Read the date, description, MCC and card-currency amount columns."""
    for delimiter in (",", ";", "\t"):
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        first = ""
        try:
            for header in reader:
                if reader.line_num > 10:
                    first = ""
                    break
                first = header[0].strip().casefold() if header else ""
                if len(header) >= 4 and "дата" in first and "операці" in first:
                    break
            else:
                continue
        except csv.Error:
            continue
        if not (len(header) >= 4 and "дата" in first and "операці" in first):
            continue
        for values in reader:
            if not values or not any(cell.strip() for cell in values):
                continue
            if len(values) != len(header):
                yield reader.line_num, {}, {"row": "The number of cells must match the header."}
                continue
            timestamp, note, mcc, raw_amount = (value.strip() for value in values[:4])
            date_part = timestamp.split(maxsplit=1)[0] if timestamp else ""
            try:
                pattern = "%Y.%m.%d" if len(date_part.split(".")[0]) == 4 else "%d.%m.%Y"
                day = datetime.strptime(date_part, pattern).date().isoformat()
            except ValueError:
                day = ""
            match = re.fullmatch(
                r"([+−-]?[0-9][0-9 .,\u00a0\u202f]*?)\s*([A-Za-z]{3})?",
                raw_amount,
            )
            amount, code = match.groups() if match else ("", None)
            amount = amount.replace("−", "-").replace("\u202f", " ").strip()
            kind = "expense" if amount.startswith("-") else "income"
            if kind == "income":
                category = "Other"
            elif mcc in {"5411", "5499", "5812", "5814"}:
                category = "Food"
            elif mcc in {"4111", "4121", "4131", "5541"}:
                category = "Transport"
            else:
                category = "Other"
            yield reader.line_num, {
                "date": day, "kind": kind, "amount": amount.lstrip("+-"),
                "currency": (code or currency).upper(), "category": category,
                "description": note,
            }, {}
        return
    raise ImportError("Use the listed columns or a standard Monobank statement.")


def _csv_rows(text, currency):
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        columns = reader.fieldnames or []
    except csv.Error:
        columns = []
    if (len(columns) != len(set(columns)) or not REQUIRED_COLUMNS.issubset(columns)
            or set(columns) - REQUIRED_COLUMNS - OPTIONAL_COLUMNS):
        yield from _monobank_rows(text, currency)
        return
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            yield reader.line_num, row, {"row": "The number of cells must match the header."}
        else:
            yield reader.line_num, row, {}


def parse_file(upload, wallet, context):
    content = upload.stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ImportError("Choose a CSV file no larger than 128 KiB.")
    if b"\x00" in content:
        raise ImportError("CSV files cannot contain NUL characters.")
    try:
        preview = []
        for number, row, errors in _csv_rows(content.decode("utf-8-sig"), wallet.currency):
            if len(preview) >= MAX_ROWS:
                raise ImportError("Import at most 100 transactions per file.")
            draft = None
            if not errors:
                kind = row["kind"].strip().lower()
                category = context.find_category(row["category"], kind)
                try:
                    draft = ledger.parse_transaction({
                        "wallet_id": wallet.id,
                        "category_id": category.id if category else None,
                        "kind": kind,
                        "amount": row["amount"],
                        "occurred_on": row["date"],
                        "description": row.get("description"),
                    }, context)
                except ledger.ValidationError as error:
                    errors.update(error.errors)
                if row["currency"].strip().upper() != wallet.currency:
                    errors["currency"] = f"Use {wallet.currency} for this wallet."
            preview.append((number, row, draft, errors))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ImportError("Upload a valid UTF-8 CSV file.") from error
    if not preview:
        raise ImportError("The file has no transactions.")
    return preview


def _entry_key(kind, occurred_on, amount, description):
    return (kind, str(occurred_on), f"{Decimal(amount):.2f}", " ".join((description or "").casefold().split()))


def already_recorded(wallet_id, drafts, *, transaction_id=None):
    """Indexes of ``drafts`` that are already in the wallet: same type, date, amount and description.

    Matching counts entries, so two identical purchases on one day in the file are only
    skipped if the wallet already has two. The caller must have checked wallet ownership.
    """
    if not drafts:
        return set()
    existing = Counter(
        _entry_key(kind, day, amount, description)
        for kind, day, amount, description in db.rows(db.execute(
            """
            SELECT kind, CAST(occurred_on AS TEXT), CAST(amount AS TEXT), description
            FROM transactions
            WHERE wallet_id = :wallet_id AND occurred_on >= :first AND occurred_on <= :last
            """,
            {"wallet_id": wallet_id, "first": min(d.occurred_on for d in drafts),
             "last": max(d.occurred_on for d in drafts)},
            transaction_id=transaction_id,
        ))
    )
    duplicates = set()
    for index, draft in enumerate(drafts):
        key = _entry_key(draft.kind, draft.occurred_on.isoformat(), draft.amount, draft.description)
        if existing[key]:
            existing[key] -= 1
            duplicates.add(index)
    return duplicates


def stage(user_id, wallet, preview):
    """Stage every row; rows already in the wallet are skipped when the import is confirmed."""
    token = token_urlsafe(32)
    payload = [
        {
            "kind": draft.kind, "category_id": draft.category_id,
            "amount": str(draft.amount), "occurred_on": draft.occurred_on.isoformat(),
            "description": draft.description,
        }
        for _, _, draft, _ in preview
    ]
    db.execute(
        "DELETE FROM csv_imports WHERE user_id = :user_id AND expires_at <= now()",
        {"user_id": user_id},
    )
    db.execute(
        """
        INSERT INTO csv_imports (token, user_id, wallet_id, currency, payload)
        VALUES (:token, :user_id, :wallet_id, :currency, CAST(:payload AS JSONB))
        """,
        {"token": token, "user_id": user_id, "wallet_id": wallet.id,
         "currency": wallet.currency, "payload": json.dumps(payload)},
    )
    return token


def confirm(user_id, token):
    params = {"token": token, "user_id": user_id}
    with db.transaction() as transaction_id:
        batches = db.rows(db.execute(
            """
            SELECT wallet_id, currency, CAST(payload AS TEXT), confirmed_at IS NOT NULL,
                   expires_at <= now()
            FROM csv_imports WHERE token = :token AND user_id = :user_id
            FOR UPDATE
            """,
            params, transaction_id=transaction_id,
        ))
        if not batches:
            raise ledger.NotFound()
        wallet_id, currency, payload, confirmed, expired = batches[0]
        if confirmed:
            return 0
        if expired:
            raise ImportError("This preview has expired. Upload the file again.")
        wallets = db.rows(db.execute(
            """
            SELECT currency FROM wallets
            WHERE id = :wallet_id AND user_id = :user_id FOR UPDATE
            """,
            {"wallet_id": wallet_id, "user_id": user_id},
            transaction_id=transaction_id,
        ))
        if not wallets:
            raise ledger.NotFound()
        if wallets[0][0] != currency:
            raise ImportError("The wallet currency changed. Upload the file again.")
        context = ledger.load_context(user_id)
        drafts = [
            ledger.parse_transaction(dict(fields, wallet_id=wallet_id), context)
            for fields in json.loads(payload)
        ]
        # Check again under the wallet lock, so two previews of one file can't both be saved.
        duplicates = already_recorded(wallet_id, drafts, transaction_id=transaction_id)
        drafts = [draft for index, draft in enumerate(drafts) if index not in duplicates]
        for draft in drafts:
            ledger.create_transaction(user_id, draft, transaction_id=transaction_id)
        db.execute(
            """
            UPDATE csv_imports SET confirmed_at = now(), payload = NULL
            WHERE token = :token AND user_id = :user_id
            """,
            params, transaction_id=transaction_id,
        )
    return len(drafts)


@bp.route("", methods=["GET", "POST"])
@login_required
def preview():
    context = ledger.load_context(g.user_id)
    rows, token, error, selected, wallet, duplicates = [], None, None, "", None, set()
    if request.method == "POST":
        selected = request.form.get("wallet_id", "")
        wallet = next((w for w in context.wallets.values() if str(w.id) == selected), None)
        if wallet is None:
            abort(404)
        upload = request.files.get("csv_file")
        try:
            if not upload or not upload.filename:
                raise ImportError("Choose a CSV file.")
            rows = parse_file(upload, wallet, context)
        except ImportError as exc:
            error = str(exc)
        else:
            if not any(errors for _, _, _, errors in rows):
                found = already_recorded(wallet.id, [draft for _, _, draft, _ in rows])
                duplicates = {rows[index][0] for index in found}
                if len(duplicates) < len(rows):
                    token = stage(g.user_id, wallet, rows)
    summary = summarize(rows, duplicates)
    invalid = error or summary["invalid"]
    return render_template(
        "csv_import.html", wallets=context.wallets.values(), selected=selected,
        wallet=wallet, rows=rows, token=token, error=error, summary=summary, duplicates=duplicates,
        categories={c.id: c.name for c in context.categories.values()},
    ), 422 if invalid else 200


def summarize(rows, duplicates=frozenset()):
    """Counts and totals shown before the user confirms an import; already-recorded rows are excluded."""
    valid = [draft for number, _, draft, errors in rows
             if draft is not None and not errors and number not in duplicates]
    return {
        "rows": len(rows) - len(duplicates),
        "duplicates": len(duplicates),
        "invalid": sum(1 for *_, errors in rows if errors),
        "expenses": sum(1 for d in valid if d.kind == "expense"),
        "income": sum(1 for d in valid if d.kind == "income"),
        "expense_total": sum((d.amount for d in valid if d.kind == "expense"), Decimal("0.00")),
        "income_total": sum((d.amount for d in valid if d.kind == "income"), Decimal("0.00")),
    }


@bp.post("/confirm")
@login_required
def save():
    try:
        count = confirm(g.user_id, request.form.get("token", ""))
    except ledger.NotFound:
        abort(404)
    except (ImportError, ledger.ValidationError) as exc:
        flash(str(exc))
        return redirect(url_for("csv_import.preview"))
    flash(f"Imported {count} transaction{'' if count == 1 else 's'}." if count
          else "This batch was already imported, so nothing was added twice.")
    return redirect(url_for("ledger.transactions"))
