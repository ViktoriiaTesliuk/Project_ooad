"""Wallets, transactions, monthly budgets and the dashboard summary.

This module is the single place that validates and stores ledger data. It does
not touch the Flask request, so the CSV importer and AI drafts can reuse it:

    context = ledger.load_context(user_id)
    draft = ledger.parse_transaction(raw_fields, context)   # raises ValidationError
    ledger.create_transactions(user_id, [draft, ...])       # one DB transaction

Every query is parameterized and scoped to ``user_id``. Records that belong to
another account behave exactly like records that do not exist.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from . import db

KINDS = ("expense", "income")
CURRENCIES = ("UAH", "USD", "EUR", "PLN", "GBP")
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal(10) ** 16  # NUMERIC(18, 2)
MAX_WALLET_NAME = 60
MAX_DESCRIPTION = 500
HISTORY_LIMIT = 500
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class ValidationError(ValueError):
    """Raised with a ``{field: message}`` mapping when input is invalid."""

    def __init__(self, errors):
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))
        self.errors = dict(errors)


class NotFound(LookupError):
    """The record does not exist or belongs to another account."""


class CurrencyLocked(ValueError):
    """A wallet's currency cannot change while it has transactions."""


# --------------------------------------------------------------------------- records


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    kind: str


@dataclass(frozen=True)
class Wallet:
    id: int
    name: str
    currency: str
    opening_balance: Decimal
    balance: Decimal
    transaction_count: int

    @property
    def has_transactions(self):
        return self.transaction_count > 0


@dataclass(frozen=True)
class WalletDraft:
    name: str
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True)
class TransactionDraft:
    """A validated income or expense that is ready to be stored."""

    wallet_id: int
    category_id: int
    kind: str
    amount: Decimal
    occurred_on: date
    description: str = ""


@dataclass(frozen=True)
class Transaction:
    id: int
    wallet_id: int
    wallet_name: str
    category_id: int
    category_name: str
    kind: str
    amount: Decimal
    currency: str
    occurred_on: date
    description: str

    def as_form(self):
        return {
            "kind": self.kind,
            "wallet_id": str(self.wallet_id),
            "category_id": str(self.category_id),
            "amount": f"{self.amount:.2f}",
            "occurred_on": self.occurred_on.isoformat(),
            "description": self.description,
        }


@dataclass(frozen=True)
class Budget:
    month_start: date
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class TransactionContext:
    """The wallets and categories a user's transaction may refer to."""

    wallets: dict
    categories: dict

    def find_wallet(self, name):
        """Case-insensitive wallet lookup by name, for imports and drafts."""
        wanted = name.strip().casefold()
        return next((w for w in self.wallets.values() if w.name.casefold() == wanted), None)

    def find_category(self, name, kind):
        """Case-insensitive category lookup by name within a kind."""
        wanted = name.strip().casefold()
        return next(
            (c for c in self.categories.values() if c.kind == kind and c.name.casefold() == wanted),
            None,
        )


# --------------------------------------------------------------------------- parsing


def parse_amount(value, *, positive=True):
    """Parse user input such as ``"1 234,50"`` into a two-place Decimal."""
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int) and not isinstance(value, bool):
        number = Decimal(value)
    else:
        # Strings from forms and CSV cells; floats from JSON drafts go through str().
        text = str(value if value is not None else "").strip().replace(" ", "").replace(" ", "")
        if "," in text and "." in text:
            if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+\.\d{1,2}", text):
                text = text.replace(",", "")
            elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+,\d{1,2}", text):
                text = text.replace(".", "")
            else:
                raise ValueError("Check the thousands and decimal separators.")
        text = text.replace(",", ".")
        if not text:
            raise ValueError("Enter an amount.")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError("Enter a number such as 125.50.") from None

    if not number.is_finite():
        raise ValueError("Enter a number such as 125.50.")
    if number.as_tuple().exponent < -2:
        raise ValueError("Use at most two decimal places.")
    if abs(number) >= MAX_AMOUNT:
        raise ValueError("The amount is too large.")
    if positive and number <= 0:
        raise ValueError("The amount must be greater than zero.")
    return number.quantize(CENT)


def parse_date(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value or "").strip())
        except ValueError:
            raise ValueError("Enter a date as YYYY-MM-DD.") from None
    if not 1900 <= parsed.year <= 2100:
        raise ValueError("Enter a date between 1900 and 2100.")
    return parsed


def parse_month(value, *, default=None):
    """Return the first day of a ``YYYY-MM`` month, or ``default`` when blank."""
    if value is None or str(value).strip() == "":
        return default
    match = _MONTH.match(str(value).strip())
    if not match or not 1900 <= int(match[1]) <= 2100 or not 1 <= int(match[2]) <= 12:
        raise ValueError("Choose a month as YYYY-MM.")
    return date(int(match[1]), int(match[2]), 1)


def current_month():
    return datetime.now(ZoneInfo("Europe/Kyiv")).date().replace(day=1)


def next_month(month_start):
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def previous_month(month_start):
    if month_start.month == 1:
        return date(month_start.year - 1, 12, 1)
    return date(month_start.year, month_start.month - 1, 1)


def _parse_id(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def parse_currency(value):
    code = str(value or "").strip().upper()
    if code not in CURRENCIES:
        raise ValueError("Choose one of: " + ", ".join(CURRENCIES) + ".")
    return code


def parse_wallet(data):
    errors = {}
    name = str(data.get("name") or "").strip()
    if not name:
        errors["name"] = "Enter a wallet name."
    elif len(name) > MAX_WALLET_NAME:
        errors["name"] = f"Use at most {MAX_WALLET_NAME} characters."
    try:
        currency = parse_currency(data.get("currency"))
    except ValueError as error:
        errors["currency"] = str(error)
    raw_balance = data.get("opening_balance")
    try:
        opening_balance = (
            Decimal("0.00")
            if raw_balance is None or str(raw_balance).strip() == ""
            else parse_amount(raw_balance, positive=False)
        )
    except ValueError as error:
        errors["opening_balance"] = str(error)
    if errors:
        raise ValidationError(errors)
    return WalletDraft(name, currency, opening_balance)


def parse_transaction(data, context):
    """Validate raw income/expense fields against the user's wallets and categories.

    ``data`` is any mapping with ``kind``, ``wallet_id``, ``category_id``,
    ``amount``, ``occurred_on`` and optional ``description``. Values may be form
    strings or already-typed values (int, Decimal, date), so CSV rows and AI
    drafts can be passed in directly. Raises ``ValidationError``.
    """
    errors = {}
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in KINDS:
        errors["kind"] = "Choose income or expense."

    wallet_id = _parse_id(data.get("wallet_id"))
    if wallet_id not in context.wallets:
        errors["wallet_id"] = "Choose one of your wallets."

    category_id = _parse_id(data.get("category_id"))
    category = context.categories.get(category_id)
    if category is None:
        errors["category_id"] = "Choose a category."
    elif kind in KINDS and category.kind != kind:
        errors["category_id"] = f"Choose an {kind} category."

    try:
        amount = parse_amount(data.get("amount"))
    except ValueError as error:
        errors["amount"] = str(error)
    try:
        occurred_on = parse_date(data.get("occurred_on"))
    except ValueError as error:
        errors["occurred_on"] = str(error)

    description = str(data.get("description") or "").strip()
    if len(description) > MAX_DESCRIPTION:
        errors["description"] = f"Use at most {MAX_DESCRIPTION} characters."

    if errors:
        raise ValidationError(errors)
    return TransactionDraft(wallet_id, category_id, kind, amount, occurred_on, description)


# --------------------------------------------------------------------------- wallets

_WALLET_SELECT = """
    SELECT w.id, w.name, w.currency, CAST(w.opening_balance AS TEXT),
           CAST(w.opening_balance + COALESCE(SUM(
               CASE WHEN t.kind = 'income' THEN t.amount ELSE -t.amount END
           ), 0) AS TEXT),
           COUNT(t.id)
    FROM wallets AS w
    LEFT JOIN transactions AS t ON t.wallet_id = w.id
    WHERE w.user_id = :user_id {extra}
    GROUP BY w.id, w.name, w.currency, w.opening_balance
    ORDER BY lower(w.name), w.id
"""


def _wallet(row):
    return Wallet(row[0], row[1], row[2], Decimal(row[3]), Decimal(row[4]), int(row[5]))


def list_wallets(user_id):
    """The user's wallets with balances derived from opening balance + transactions."""
    result = db.execute(_WALLET_SELECT.format(extra=""), {"user_id": user_id})
    return [_wallet(row) for row in db.rows(result)]


def get_wallet(user_id, wallet_id):
    result = db.execute(
        _WALLET_SELECT.format(extra="AND w.id = :wallet_id"),
        {"user_id": user_id, "wallet_id": wallet_id},
    )
    found = db.rows(result)
    if not found:
        raise NotFound("Wallet not found.")
    return _wallet(found[0])


def create_wallet(user_id, draft, *, transaction_id=None):
    result = db.execute(
        """
        INSERT INTO wallets (user_id, name, currency, opening_balance)
        VALUES (:user_id, :name, :currency, :opening_balance)
        RETURNING id
        """,
        {
            "user_id": user_id,
            "name": draft.name,
            "currency": draft.currency,
            "opening_balance": draft.opening_balance,
        },
        transaction_id=transaction_id,
    )
    return db.rows(result)[0][0]


def update_wallet(user_id, wallet_id, draft):
    """Rename a wallet or change its opening balance.

    The currency may only change while the wallet has no transactions; the
    composite foreign key on transactions enforces the same rule in the database.
    """
    result = db.execute(
        """
        UPDATE wallets
        SET name = :name, currency = :currency, opening_balance = :opening_balance
        WHERE id = :wallet_id AND user_id = :user_id
          AND (currency = :currency
               OR NOT EXISTS (SELECT 1 FROM transactions WHERE wallet_id = :wallet_id))
        RETURNING id
        """,
        {
            "user_id": user_id,
            "wallet_id": wallet_id,
            "name": draft.name,
            "currency": draft.currency,
            "opening_balance": draft.opening_balance,
        },
    )
    if not db.rows(result):
        get_wallet(user_id, wallet_id)  # raises NotFound for missing or foreign wallets
        raise CurrencyLocked("This wallet already has transactions, so its currency can’t change.")


def delete_wallet(user_id, wallet_id, *, action, expected_count, target_id=None):
    """Move or remove wallet history and delete the wallet in one transaction."""
    with db.transaction() as transaction_id:
        options = {"user_id": user_id, "wallet_id": wallet_id, "target_id": target_id or 0}
        wallets = dict(db.rows(db.execute(
            """SELECT id, currency FROM wallets
               WHERE user_id = :user_id AND id IN (:wallet_id, :target_id)
               ORDER BY id FOR UPDATE""",
            options, transaction_id=transaction_id,
        )))
        if wallet_id not in wallets:
            raise NotFound("Wallet not found.")
        params = {"wallet_id": wallet_id}
        count = db.rows(db.execute(
            "SELECT COUNT(*) FROM transactions WHERE wallet_id = :wallet_id",
            params, transaction_id=transaction_id,
        ))[0][0]
        if count != expected_count:
            raise ValidationError({"action": "The transaction count changed. Review the wallet again."})
        if action == "move":
            if not count or target_id == wallet_id or wallets.get(target_id) != wallets[wallet_id]:
                raise ValidationError({"action": "Choose another wallet you own in the same currency."})
            db.execute(
                "UPDATE transactions SET wallet_id = :target_id WHERE wallet_id = :wallet_id",
                dict(params, target_id=target_id), transaction_id=transaction_id,
            )
        elif count and action == "delete_entries":
            db.execute(
                "DELETE FROM transactions WHERE wallet_id = :wallet_id",
                params, transaction_id=transaction_id,
            )
        elif action != "delete_empty" or count:
            raise ValidationError({"action": "Choose what to do with the wallet’s transactions."})
        db.execute(
            "DELETE FROM wallets WHERE id = :wallet_id AND user_id = :user_id",
            dict(params, user_id=user_id), transaction_id=transaction_id,
        )


# --------------------------------------------------------------------------- categories


def list_categories():
    result = db.execute("SELECT id, name, kind FROM categories ORDER BY kind, name = 'Other', name")
    return [Category(*row) for row in db.rows(result)]


def load_context(user_id):
    return TransactionContext(
        wallets={w.id: w for w in list_wallets(user_id)},
        categories={c.id: c for c in list_categories()},
    )


# --------------------------------------------------------------------------- transactions

_TRANSACTION_SELECT = """
    SELECT t.id, t.wallet_id, w.name, t.category_id, c.name, t.kind,
           CAST(t.amount AS TEXT), t.currency, CAST(t.occurred_on AS TEXT), t.description
    FROM transactions AS t
    JOIN wallets AS w ON w.id = t.wallet_id
    JOIN categories AS c ON c.id = t.category_id
    WHERE w.user_id = :user_id
"""


def _transaction(row):
    return Transaction(
        row[0], row[1], row[2], row[3], row[4], row[5],
        Decimal(row[6]), row[7], date.fromisoformat(row[8]), row[9],
    )


def _draft_params(user_id, draft):
    return {
        "user_id": user_id,
        "wallet_id": draft.wallet_id,
        "category_id": draft.category_id,
        "kind": draft.kind,
        "amount": draft.amount,
        "occurred_on": draft.occurred_on,
        "description": draft.description,
    }


def get_transaction(user_id, record_id):
    result = db.execute(
        _TRANSACTION_SELECT + " AND t.id = :record_id",
        {"user_id": user_id, "record_id": record_id},
    )
    found = db.rows(result)
    if not found:
        raise NotFound("Transaction not found.")
    return _transaction(found[0])


def list_transactions(user_id, *, month=None, wallet_id=None, category_id=None, limit=HISTORY_LIMIT):
    """Newest-first history, optionally filtered by month, wallet and category."""
    sql = _TRANSACTION_SELECT
    params = {"user_id": user_id, "limit": limit}
    if month is not None:
        sql += " AND t.occurred_on >= :month_start AND t.occurred_on < :month_end"
        params.update(month_start=month, month_end=next_month(month))
    if wallet_id is not None:
        sql += " AND t.wallet_id = :wallet_id"
        params["wallet_id"] = wallet_id
    if category_id is not None:
        sql += " AND t.category_id = :category_id"
        params["category_id"] = category_id
    sql += " ORDER BY t.occurred_on DESC, t.id DESC LIMIT :limit"
    return [_transaction(row) for row in db.rows(db.execute(sql, params))]


def create_transaction(user_id, draft, *, transaction_id=None):
    """Store one validated draft and return its ID.

    The currency is copied from the wallet inside the INSERT, and the wallet must
    belong to ``user_id``. Pass ``transaction_id`` from ``db.transaction()`` to
    group several inserts.
    """
    result = db.execute(
        """
        INSERT INTO transactions
            (wallet_id, category_id, kind, amount, currency, occurred_on, description)
        SELECT w.id, :category_id, :kind, :amount, w.currency, :occurred_on, :description
        FROM wallets AS w
        WHERE w.id = :wallet_id AND w.user_id = :user_id
        RETURNING id
        """,
        _draft_params(user_id, draft),
        transaction_id=transaction_id,
    )
    found = db.rows(result)
    if not found:
        raise NotFound("Wallet not found.")
    return found[0][0]


def create_transactions(user_id, drafts):
    """Store several drafts atomically (all or none) and return their IDs."""
    drafts = list(drafts)
    if not drafts:
        return []
    with db.transaction() as transaction_id:
        return [create_transaction(user_id, d, transaction_id=transaction_id) for d in drafts]


def update_transaction(user_id, record_id, draft):
    """Replace an income/expense. Both the old and the new wallet must be the user's."""
    params = _draft_params(user_id, draft)
    params["record_id"] = record_id
    result = db.execute(
        """
        UPDATE transactions AS t
        SET wallet_id = w.id, category_id = :category_id, kind = :kind,
            amount = :amount, currency = w.currency, occurred_on = :occurred_on,
            description = :description
        FROM wallets AS w
        WHERE t.id = :record_id
          AND t.wallet_id IN (SELECT id FROM wallets WHERE user_id = :user_id)
          AND w.id = :wallet_id AND w.user_id = :user_id
        RETURNING t.id
        """,
        params,
    )
    if not db.rows(result):
        raise NotFound("Transaction not found.")


def delete_transaction(user_id, record_id):
    result = db.execute(
        """
        DELETE FROM transactions AS t
        USING wallets AS w
        WHERE t.id = :record_id AND w.id = t.wallet_id AND w.user_id = :user_id
        RETURNING CAST(t.occurred_on AS TEXT)
        """,
        {"user_id": user_id, "record_id": record_id},
    )
    rows = db.rows(result)
    if not rows:
        raise NotFound("Transaction not found.")
    return date.fromisoformat(rows[0][0])


# --------------------------------------------------------------------------- budgets


def get_budget(user_id, month_start):
    result = db.execute(
        """
        SELECT CAST(month_start AS TEXT), CAST(amount AS TEXT), currency
        FROM monthly_budgets WHERE user_id = :user_id AND month_start = :month_start
        """,
        {"user_id": user_id, "month_start": month_start},
    )
    found = db.rows(result)
    if not found:
        return None
    month, amount, currency = found[0]
    return Budget(date.fromisoformat(month), Decimal(amount), currency)


def parse_budget(data):
    errors = {}
    try:
        amount = parse_amount(data.get("amount"))
    except ValueError as error:
        errors["amount"] = str(error)
    try:
        currency = parse_currency(data.get("currency"))
    except ValueError as error:
        errors["currency"] = str(error)
    if errors:
        raise ValidationError(errors)
    return amount, currency


def set_budget(user_id, month_start, amount, currency, *, transaction_id=None):
    db.execute(
        """
        INSERT INTO monthly_budgets (user_id, month_start, amount, currency)
        VALUES (:user_id, :month_start, :amount, :currency)
        ON CONFLICT (user_id, month_start) DO UPDATE
        SET amount = EXCLUDED.amount, currency = EXCLUDED.currency
        """,
        {"user_id": user_id, "month_start": month_start, "amount": amount, "currency": currency},
        transaction_id=transaction_id,
    )


def delete_budget(user_id, month_start):
    db.execute(
        "DELETE FROM monthly_budgets WHERE user_id = :user_id AND month_start = :month_start",
        {"user_id": user_id, "month_start": month_start},
    )


# --------------------------------------------------------------------------- dashboard


@dataclass(frozen=True)
class CategorySpend:
    category_id: int
    name: str
    amount: Decimal
    share: Decimal  # percent of this currency's expenses


@dataclass
class CurrencySummary:
    currency: str
    income: Decimal = Decimal("0.00")
    expenses: Decimal = Decimal("0.00")
    categories: list = field(default_factory=list)

    @property
    def net(self):
        return self.income - self.expenses


@dataclass(frozen=True)
class BudgetStatus:
    budget: Budget
    spent: Decimal
    remaining: Decimal
    used_percent: Decimal
    excluded_currencies: tuple  # expense currencies not counted against the limit

    @property
    def over(self):
        return self.remaining < 0


@dataclass(frozen=True)
class MonthSummary:
    month_start: date
    currencies: list
    budget: BudgetStatus | None


def summarize(month_start, totals, category_totals, budget):
    """Build the dashboard without converting currencies.

    ``totals`` rows are ``(currency, kind, amount)``; ``category_totals`` rows are
    ``(currency, category_id, name, amount)`` for expenses. Only expenses in the
    budget's own currency count against the monthly limit.
    """
    by_currency = {}

    def summary(code):
        return by_currency.setdefault(code, CurrencySummary(code))

    for currency, kind, amount in totals:
        if kind == "income":
            summary(currency).income += amount
        else:
            summary(currency).expenses += amount

    grouped = defaultdict(list)
    for currency, category_id, name, amount in category_totals:
        grouped[currency].append((category_id, name, amount))
    for currency, items in grouped.items():
        total = summary(currency).expenses
        summary(currency).categories = [
            CategorySpend(
                category_id, name, amount,
                (amount * 100 / total).quantize(Decimal("0.1")) if total else Decimal(0),
            )
            for category_id, name, amount in sorted(items, key=lambda item: (-item[2], item[1]))
        ]

    status = None
    if budget is not None:
        spent = by_currency.get(budget.currency, CurrencySummary(budget.currency)).expenses
        status = BudgetStatus(
            budget=budget,
            spent=spent,
            remaining=budget.amount - spent,
            used_percent=(spent * 100 / budget.amount).quantize(Decimal("0.1")),
            excluded_currencies=tuple(
                sorted(c for c, s in by_currency.items() if c != budget.currency and s.expenses > 0)
            ),
        )

    ordered = sorted(
        by_currency.values(),
        key=lambda s: (budget is None or s.currency != budget.currency, s.currency),
    )
    return MonthSummary(month_start, ordered, status)


def month_summary(user_id, month_start):
    params = {"user_id": user_id, "month_start": month_start, "month_end": next_month(month_start)}
    totals = db.rows(db.execute(
        """
        SELECT t.currency, t.kind, CAST(SUM(t.amount) AS TEXT)
        FROM transactions AS t
        JOIN wallets AS w ON w.id = t.wallet_id
        WHERE w.user_id = :user_id
          AND t.occurred_on >= :month_start AND t.occurred_on < :month_end
        GROUP BY t.currency, t.kind
        """,
        params,
    ))
    by_category = db.rows(db.execute(
        """
        SELECT t.currency, c.id, c.name, CAST(SUM(t.amount) AS TEXT)
        FROM transactions AS t
        JOIN wallets AS w ON w.id = t.wallet_id
        JOIN categories AS c ON c.id = t.category_id
        WHERE w.user_id = :user_id AND t.kind = 'expense'
          AND t.occurred_on >= :month_start AND t.occurred_on < :month_end
        GROUP BY t.currency, c.id, c.name
        """,
        params,
    ))
    return summarize(
        month_start,
        [(currency, kind, Decimal(amount)) for currency, kind, amount in totals],
        [(currency, cid, name, Decimal(amount)) for currency, cid, name, amount in by_category],
        get_budget(user_id, month_start),
    )
