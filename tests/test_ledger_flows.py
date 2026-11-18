"""End-to-end flows through the Flask views and the real SQL."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pocket_ledger import fx, ledger

MONTH = date.today().replace(day=1)
TODAY = date.today().isoformat()


def post(client, url, data):
    return client.post(url, data={"csrf_token": client.csrf, **data})


def category_id(pg, name, kind):
    return pg.execute("SELECT id FROM categories WHERE name = %s AND kind = %s", (name, kind)).fetchone()[0]


def wallet_id(pg, name):
    return pg.execute("SELECT id FROM wallets WHERE name = %s", (name,)).fetchone()[0]


def add_expense(client, wallet, category, amount, description="Lunch", occurred_on=TODAY):
    return post(client, "/transactions/new", {
        "kind": "expense", "wallet_id": wallet, "category_id": category,
        "amount": amount, "occurred_on": occurred_on, "description": description,
    })


@pytest.fixture
def owner(pg, make_user, login):
    client = login(make_user())
    post(client, "/wallets", {"name": "Cash", "currency": "UAH", "opening_balance": "500"})
    return client


def test_wallet_creation_and_derived_balance(pg, owner, app):
    page = owner.get("/wallets")
    assert page.status_code == 200 and "Cash" in page.text and "500.00 UAH" in page.text

    food = category_id(pg, "Food", "expense")
    cash = wallet_id(pg, "Cash")
    assert add_expense(owner, cash, food, "120.50").status_code == 302
    post(owner, "/transactions/new", {
        "kind": "income", "wallet_id": cash, "category_id": category_id(pg, "Salary", "income"),
        "amount": "1000", "occurred_on": TODAY,
    })
    with app.app_context():
        user_id = pg.execute("SELECT user_id FROM wallets WHERE id = %s", (cash,)).fetchone()[0]
        assert ledger.get_wallet(user_id, cash).balance == Decimal("1379.50")


def test_invalid_wallet_is_rejected(owner):
    response = post(owner, "/wallets", {"name": " ", "currency": "XYZ", "opening_balance": "1.234"})
    assert response.status_code == 422
    assert "Enter a wallet name." in response.text


def test_currency_is_locked_once_a_wallet_has_transactions(pg, owner):
    cash = wallet_id(pg, "Cash")
    edit = f"/wallets/{cash}/edit"
    assert post(owner, edit, {"name": "Cash", "currency": "USD", "opening_balance": "500"}).status_code == 302
    assert pg.execute("SELECT currency FROM wallets WHERE id = %s", (cash,)).fetchone()[0] == "USD"

    add_expense(owner, cash, category_id(pg, "Food", "expense"), "10")
    response = post(owner, edit, {"name": "Cash", "currency": "EUR", "opening_balance": "500"})
    assert response.status_code == 422 and "currency can’t change" in response.text
    assert pg.execute("SELECT currency FROM wallets WHERE id = %s", (cash,)).fetchone()[0] == "USD"
    # Renaming is still allowed.
    assert post(owner, edit, {"name": "Pocket cash", "currency": "USD", "opening_balance": "500"}).status_code == 302


def test_edit_transaction_recalculates_budget_and_balance(pg, owner):
    cash = wallet_id(pg, "Cash")
    food = category_id(pg, "Food", "expense")
    month = MONTH.strftime("%Y-%m")
    post(owner, f"/budget?month={month}", {"amount": "1000", "currency": "UAH"})
    add_expense(owner, cash, food, "300")

    dashboard = owner.get(f"/dashboard?month={month}").text
    assert "700.00 UAH" in dashboard  # remaining
    assert "30.0% used" in dashboard

    record = pg.execute("SELECT id FROM transactions").fetchone()[0]
    response = post(owner, f"/transactions/{record}/edit", {
        "kind": "expense", "wallet_id": cash, "category_id": category_id(pg, "Transport", "expense"),
        "amount": "1250,00", "occurred_on": TODAY, "description": "Train tickets",
    })
    assert response.status_code == 302
    dashboard = owner.get(f"/dashboard?month={month}").text
    assert "Over budget" in dashboard and "250.00 UAH" in dashboard
    assert "Transport" in dashboard and "Food" not in dashboard.split("Spending by category")[1]
    assert "-750.00 UAH" in owner.get("/wallets").text

    assert post(owner, f"/transactions/{record}/delete", {}).status_code == 302
    assert "1 000.00 UAH" in owner.get(f"/dashboard?month={month}").text  # full limit remains


def test_budget_converts_other_currencies_or_flags_missing_rates(pg, owner, monkeypatch):
    post(owner, "/wallets", {"name": "Card", "currency": "USD", "opening_balance": "0"})
    month = MONTH.strftime("%Y-%m")
    post(owner, f"/budget?month={month}", {"amount": "100", "currency": "UAH"})
    add_expense(owner, wallet_id(pg, "Card"), category_id(pg, "Food", "expense"), "40")
    monkeypatch.setattr(fx, "load_rates", lambda _: {})
    dashboard = owner.get(f"/dashboard?month={month}").text
    assert "100.00 UAH" in dashboard and "Totals are incomplete" in dashboard
    rate = fx.Rate("USD", Decimal(40), "Monobank", datetime.now(timezone.utc), cached=True)
    monkeypatch.setattr(fx, "load_rates", lambda _: {"USD": rate})
    dashboard = owner.get(f"/dashboard?month={month}&currency=USD").text
    assert "37.50 USD" in dashboard and "Monobank (cached)" in dashboard
    assert "Totals are incomplete" not in dashboard


def test_history_filters(pg, owner):
    post(owner, "/wallets", {"name": "Card", "currency": "UAH", "opening_balance": "0"})
    cash, card = wallet_id(pg, "Cash"), wallet_id(pg, "Card")
    food, transport = category_id(pg, "Food", "expense"), category_id(pg, "Transport", "expense")
    add_expense(owner, cash, food, "1", "cash-food")
    add_expense(owner, card, transport, "2", "card-transport")
    add_expense(owner, card, food, "3", "card-food-old", occurred_on="2020-01-15")

    def listed(query):
        text = owner.get("/transactions" + query).text
        return {name for name in ("cash-food", "card-transport", "card-food-old") if name in text}

    assert listed("") == {"cash-food", "card-transport", "card-food-old"}
    assert listed(f"?wallet={card}") == {"card-transport", "card-food-old"}
    assert listed(f"?category={food}") == {"cash-food", "card-food-old"}
    assert listed("?month=2020-01") == {"card-food-old"}
    assert listed(f"?month={MONTH:%Y-%m}&wallet={card}&category={food}") == set()


def test_other_accounts_cannot_read_or_change_transactions(pg, owner, make_user, login):
    cash = wallet_id(pg, "Cash")
    food = category_id(pg, "Food", "expense")
    add_expense(owner, cash, food, "99", "private-lunch")
    record = pg.execute("SELECT id FROM transactions").fetchone()[0]

    intruder = login(make_user())
    post(intruder, "/wallets", {"name": "Mine", "currency": "UAH", "opening_balance": "0"})
    mine = wallet_id(pg, "Mine")

    assert intruder.get(f"/transactions/{record}/edit").status_code == 404
    assert "private-lunch" not in intruder.get("/transactions").text
    assert "private-lunch" not in intruder.get(f"/transactions?wallet={cash}").text
    assert intruder.get(f"/wallets/{cash}/edit").status_code == 404
    edit = {"kind": "expense", "wallet_id": mine, "category_id": food, "amount": "1", "occurred_on": TODAY}
    assert post(intruder, f"/transactions/{record}/edit", edit).status_code == 404
    assert post(intruder, f"/transactions/{record}/delete", {}).status_code == 404
    # Writing into someone else's wallet fails validation, as does the service call.
    response = post(intruder, "/transactions/new", dict(edit, wallet_id=cash))
    assert response.status_code == 422
    intruder_id = pg.execute("SELECT user_id FROM wallets WHERE id = %s", (mine,)).fetchone()[0]
    with pytest.raises(ledger.NotFound):
        ledger.create_transaction(intruder_id, ledger.TransactionDraft(cash, food, "expense", Decimal(1), MONTH))

    row = pg.execute("SELECT amount, wallet_id FROM transactions WHERE id = %s", (record,)).fetchone()
    assert row == (Decimal("99.00"), cash)


def test_post_without_csrf_token_is_rejected(pg, owner):
    response = owner.post("/wallets", data={"name": "Sneaky", "currency": "UAH"})
    assert response.status_code == 400
    assert pg.execute("SELECT count(*) FROM wallets WHERE name = 'Sneaky'").fetchone()[0] == 0
    assert owner.post("/auth/logout").status_code == 400


def test_bulk_create_is_atomic(pg, owner, app):
    cash = wallet_id(pg, "Cash")
    food = category_id(pg, "Food", "expense")
    user_id = pg.execute("SELECT user_id FROM wallets WHERE id = %s", (cash,)).fetchone()[0]
    good = ledger.TransactionDraft(cash, food, "expense", Decimal("5.00"), MONTH, "ok")
    bad = ledger.TransactionDraft(10**9, food, "expense", Decimal("5.00"), MONTH, "missing wallet")
    with app.app_context():
        with pytest.raises(ledger.NotFound):
            ledger.create_transactions(user_id, [good, bad])
        assert pg.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
        assert len(ledger.create_transactions(user_id, [good, good])) == 2


def test_pages_require_sign_in(app):
    client = app.test_client()
    for url in ("/dashboard", "/wallets", "/transactions", "/transactions/new", "/budget"):
        response = client.get(url)
        assert response.status_code == 302 and response.headers["Location"].endswith("/")


def test_form_pages_render(pg, owner):
    cash = wallet_id(pg, "Cash")
    add_expense(owner, cash, category_id(pg, "Food", "expense"), "10", "form-check")
    record = pg.execute("SELECT id FROM transactions").fetchone()[0]
    for url in ("/transactions/new?kind=income", f"/transactions/{record}/edit",
                f"/wallets/{cash}/edit", "/budget", "/dashboard?month=2020-01"):
        response = owner.get(url)
        assert response.status_code == 200, url
    assert 'value="form-check"' in owner.get(f"/transactions/{record}/edit").text
    assert "currency is fixed" in owner.get(f"/wallets/{cash}/edit").text
    assert owner.get("/dashboard?month=2026-13").status_code == 400


def test_new_transaction_without_wallet_redirects(make_user, login):
    client = login(make_user())
    response = client.get("/transactions/new")
    assert response.status_code == 302 and response.headers["Location"].endswith("/wallets")


@pytest.mark.parametrize(("kind", "name", "balance"), [
    ("expense", "Food", "400.00 UAH"), ("income", "Salary", "600.00 UAH"),
])
def test_delete_transaction_updates_balance_and_summary(pg, owner, make_user, login, kind, name, balance):
    cash = wallet_id(pg, "Cash")
    response = post(owner, "/transactions/new", {
        "kind": kind, "wallet_id": cash, "category_id": category_id(pg, name, kind),
        "amount": "100", "occurred_on": TODAY, "description": "Delete this entry",
    })
    assert response.status_code == 302
    assert balance in owner.get("/wallets").text
    record = pg.execute("SELECT id FROM transactions").fetchone()[0]
    url = f"/transactions/{record}/delete"
    assert url in owner.get("/transactions").text
    assert "Delete this entry" in owner.get(url).text
    other = login(make_user())
    assert post(other, url, {}).status_code == 404
    assert owner.post(url).status_code == 400
    assert post(owner, url, {}).status_code == 302
    assert post(owner, url, {}).status_code == 404
    assert "500.00 UAH" in owner.get("/wallets").text
    assert "Delete this entry" not in owner.get("/transactions").text
    assert "100.00" not in owner.get(f"/dashboard?month={MONTH:%Y-%m}").text


def test_move_wallet_history_keeps_receipt_references(pg, owner, make_user, login):
    cash = wallet_id(pg, "Cash")
    post(owner, "/wallets", {"name": "Destination", "currency": "UAH", "opening_balance": "500"})
    post(owner, "/wallets", {"name": "USD", "currency": "USD", "opening_balance": "0"})
    add_expense(owner, cash, category_id(pg, "Food", "expense"), "25")
    card = wallet_id(pg, "Destination")
    pg.execute("""INSERT INTO receipt_fingerprints (user_id, kind, fingerprint, transaction_id)
                  SELECT w.user_id, 'file', 'test', t.id FROM transactions t JOIN wallets w ON w.id=t.wallet_id""")
    other = login(make_user())
    url = f"/wallets/{cash}/delete"
    data = {"action": "move", "expected_count": 1}
    assert post(other, url, {**data, "target_id": card}).status_code == 404
    assert post(owner, url, {**data, "target_id": wallet_id(pg, "USD")}).status_code == 422
    assert post(owner, url, {**data, "target_id": card}).status_code == 302
    assert pg.execute("SELECT wallet_id FROM transactions").fetchone() == (card,)
    assert pg.execute("SELECT COUNT(*) FROM receipt_fingerprints").fetchone() == (1,)
    assert "475.00 UAH" in owner.get("/wallets").text


def test_wallet_history_requires_a_separate_confirmation(pg, owner):
    import re

    cash = wallet_id(pg, "Cash")
    url = f"/wallets/{cash}/delete"
    add_expense(owner, cash, category_id(pg, "Food", "expense"), "25")
    assert post(owner, url, {"action": "delete_empty", "expected_count": 0}).status_code == 422
    data = {"action": "delete_entries", "expected_count": 1}
    assert post(owner, url, data).status_code == 422
    review = post(owner, url, {"action": "review_delete", "expected_count": 1})
    token = re.search(r'name="final_token" value="([^"]+)"', review.text)[1]
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone() == (1,)
    assert post(owner, url, {**data, "final_token": token}).status_code == 302
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone() == (0,)
    assert "No wallets yet" in owner.get("/wallets").text


def test_rates_and_ai_placement(pg, owner, app, monkeypatch):
    app.config["GEMINI_API_KEY"] = "test-placeholder"
    rate = fx.Rate("USD", Decimal(40), "Monobank", datetime.now(timezone.utc))
    monkeypatch.setattr(fx, "load_rates", lambda needed: {"USD": rate})
    page = owner.get("/dashboard").text
    header = page.split('<header', 1)[1].split('</header>', 1)[0]
    assert "/exchange-rates" in header and "<nav" not in header and "<form" not in header
    assert '<aside class="sidebar"' in page
    assert "Add expenses with AI" not in page
    assert "Add expenses with AI" in owner.get("/transactions/new").text
    assert owner.get("/exchange-rates").json[0]["buy"] == "40.0000"
    assert app.test_client().get("/exchange-rates").status_code == 302
