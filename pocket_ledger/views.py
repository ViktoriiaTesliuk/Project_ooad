"""Pages for the dashboard, wallets, transaction history and monthly budget."""

from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import fx, gemini, ledger
from .auth import login_required

bp = Blueprint("ledger", __name__)


def init_app(app):
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["month_label"] = lambda month: month.strftime("%B %Y")
    app.jinja_env.filters["month_param"] = lambda month: month.strftime("%Y-%m")
    app.register_blueprint(bp)


def format_money(amount, currency=None):
    text = f"{amount:,.2f}".replace(",", " ")
    return f"{text} {currency}" if currency else text


def _month_arg(name="month", *, default):
    try:
        return ledger.parse_month(request.args.get(name), default=default)
    except ValueError:
        abort(400, description="Choose a month as YYYY-MM.")


def _id_arg(name):
    value = request.args.get(name, "")
    return int(value) if value.isdigit() else None


# --------------------------------------------------------------------------- dashboard


@bp.get("/dashboard")
@login_required
def dashboard():
    month = _month_arg(default=ledger.current_month())
    try:
        currency = ledger.parse_currency(request.args.get("currency", "UAH"))
    except ValueError:
        abort(400, description="Choose a supported display currency.")
    original = ledger.month_summary(g.user_id, month)
    needed = {item.currency for item in original.currencies}
    if original.budget:
        needed.add(original.budget.budget.currency)
    needed = (needed | {currency}) if needed - {currency} else set()
    rates = fx.load_rates(needed)
    summary, missing, unconverted_budget = fx.dashboard_summary(original, currency, rates)
    return render_template(
        "dashboard.html",
        summary=summary,
        currency=currency,
        currencies=ledger.CURRENCIES,
        rates=sorted(rates.values(), key=lambda rate: rate.currency),
        missing_currencies=missing,
        unconverted_budget=unconverted_budget,
        wallets=ledger.list_wallets(g.user_id),
        month=month,
        previous_month=ledger.previous_month(month),
        next_month=ledger.next_month(month),
    )


# --------------------------------------------------------------------------- wallets


@bp.route("/wallets", methods=["GET", "POST"])
@login_required
def wallets():
    form, errors = {"currency": "UAH", "opening_balance": "0.00"}, {}
    if request.method == "POST":
        form = request.form
        try:
            draft = ledger.parse_wallet(form)
        except ledger.ValidationError as error:
            errors = error.errors
        else:
            ledger.create_wallet(g.user_id, draft)
            flash(f"Wallet “{draft.name}” added.")
            return redirect(url_for("ledger.wallets"))
    return render_template(
        "wallets.html",
        wallets=ledger.list_wallets(g.user_id),
        currencies=ledger.CURRENCIES,
        form=form,
        errors=errors,
    ), 422 if errors else 200


@bp.route("/wallets/<int:wallet_id>/edit", methods=["GET", "POST"])
@login_required
def edit_wallet(wallet_id):
    try:
        wallet = ledger.get_wallet(g.user_id, wallet_id)
    except ledger.NotFound:
        abort(404)
    form = {
        "name": wallet.name,
        "currency": wallet.currency,
        "opening_balance": f"{wallet.opening_balance:.2f}",
    }
    errors = {}
    if request.method == "POST":
        form = request.form
        try:
            ledger.update_wallet(g.user_id, wallet_id, ledger.parse_wallet(form))
        except ledger.ValidationError as error:
            errors = error.errors
        except ledger.CurrencyLocked as error:
            errors = {"currency": str(error)}
        except ledger.NotFound:
            abort(404)
        else:
            flash("Wallet updated.")
            return redirect(url_for("ledger.wallets"))
    return render_template(
        "wallet_form.html",
        wallet=wallet,
        currencies=ledger.CURRENCIES,
        form=form,
        errors=errors,
    ), 422 if errors else 200


# --------------------------------------------------------------------------- transactions


@bp.get("/transactions")
@login_required
def transactions():
    filters = {
        "month": _month_arg(default=None),
        "wallet_id": _id_arg("wallet"),
        "category_id": _id_arg("category"),
    }
    return render_template(
        "transactions.html",
        transactions=ledger.list_transactions(g.user_id, **filters),
        wallets=ledger.list_wallets(g.user_id),
        categories=ledger.list_categories(),
        filters=filters,
    )


def _transaction_form(template_form, *, record=None, suggest=False):
    context = ledger.load_context(g.user_id)
    if not context.wallets:
        flash("Add a wallet before recording income or expenses.")
        return redirect(url_for("ledger.wallets"))

    form, errors = template_form, {}
    ai_text, ai_ready = "", False
    if suggest:
        form = request.form.to_dict()
        ai_text = form.pop("ai_text", "").strip()
        categories = {item.name: item.id for item in context.categories.values()
                      if item.kind == "expense"}
        try:
            form.update(gemini.suggest(ai_text, categories))
            ai_ready = True
        except gemini.DraftError as error:
            errors["ai_text"] = str(error)
    elif request.method == "POST":
        form = request.form
        try:
            draft = ledger.parse_transaction(form, context)
            if record is None:
                ledger.create_transaction(g.user_id, draft)
                flash("Transaction added.")
            else:
                ledger.update_transaction(g.user_id, record.id, draft)
                flash("Transaction updated.")
        except ledger.ValidationError as error:
            errors = error.errors
        except ledger.NotFound:
            abort(404)
        else:
            return redirect(url_for(
                "ledger.transactions", month=draft.occurred_on.strftime("%Y-%m")
            ))

    return render_template(
        "transaction_form.html",
        record=record,
        ai_text=ai_text,
        ai_ready=ai_ready,
        wallets=list(context.wallets.values()),
        categories=list(context.categories.values()),
        form=form,
        errors=errors,
    ), 422 if errors else 200


@bp.route("/transactions/new", methods=["GET", "POST"])
@login_required
def new_transaction():
    kind = request.args.get("kind")
    return _transaction_form({
        "kind": kind if kind in ledger.KINDS else "expense",
        "wallet_id": request.args.get("wallet", ""),
        "occurred_on": date.today().isoformat(),
    })


@bp.post("/transactions/suggest")
@login_required
def suggest_transaction():
    return _transaction_form({}, suggest=True)


@bp.route("/transactions/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(record_id):
    try:
        record = ledger.get_transaction(g.user_id, record_id)
    except ledger.NotFound:
        abort(404)
    return _transaction_form(record.as_form(), record=record)


@bp.post("/transactions/<int:record_id>/delete")
@login_required
def delete_transaction(record_id):
    try:
        record = ledger.get_transaction(g.user_id, record_id)
        ledger.delete_transaction(g.user_id, record_id)
    except ledger.NotFound:
        abort(404)
    flash("Transaction deleted.")
    return redirect(url_for("ledger.transactions", month=record.occurred_on.strftime("%Y-%m")))


# --------------------------------------------------------------------------- budget


@bp.route("/budget", methods=["GET", "POST"])
@login_required
def budget():
    month = _month_arg(default=ledger.current_month())
    current = ledger.get_budget(g.user_id, month)
    form = (
        {"amount": f"{current.amount:.2f}", "currency": current.currency}
        if current else {"currency": _default_currency()}
    )
    errors = {}
    if request.method == "POST":
        if request.form.get("action") == "remove":
            ledger.delete_budget(g.user_id, month)
            flash("Spending limit removed.")
            return redirect(url_for("ledger.dashboard", month=month.strftime("%Y-%m")))
        form = request.form
        try:
            amount, currency = ledger.parse_budget(form)
        except ledger.ValidationError as error:
            errors = error.errors
        else:
            ledger.set_budget(g.user_id, month, amount, currency)
            flash("Spending limit saved.")
            return redirect(url_for("ledger.dashboard", month=month.strftime("%Y-%m")))
    return render_template(
        "budget.html",
        month=month,
        current=current,
        currencies=ledger.CURRENCIES,
        form=form,
        errors=errors,
    ), 422 if errors else 200


def _default_currency():
    wallets = ledger.list_wallets(g.user_id)
    return wallets[0].currency if wallets else ledger.CURRENCIES[0]
