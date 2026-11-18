"""Guards against wrong numbers: duplicate imports, silent currency changes, UTC dates, sample data."""

import io
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import sqlparse

from pocket_ledger import demo, fx, ledger

MIGRATION_005 = Path(__file__).resolve().parents[1] / "migrations" / "005_sample_data.sql"
HEADER = b"date,kind,amount,currency,category,description\n"
LUNCH = b"2026-11-12,expense,185.00,UAH,Food,Lunch\n"
BUS = b"2026-11-13,expense,25.50,UAH,Transport,Bus\n"


def wallet(app, user, name="Card", currency="UAH"):
    with app.app_context():
        return ledger.create_wallet(user, ledger.WalletDraft(name, currency, Decimal(0)))


def preview(client, wallet_id, content):
    return client.post("/transactions/import", data={
        "csrf_token": client.csrf, "wallet_id": wallet_id, "csv_file": (io.BytesIO(content), "bank.csv")})


def confirm(client, page):
    token = re.search(r'name="token" value="([^"]+)"', page.text)[1]
    return client.post("/transactions/import/confirm", data={"csrf_token": client.csrf, "token": token})


def count(pg, user):
    return pg.execute("SELECT count(*) FROM transactions t JOIN wallets w ON w.id = t.wallet_id WHERE w.user_id = %s",
                      (user,)).fetchone()[0]


# --------------------------------------------------------------------------- CSV duplicates


def test_reimporting_the_same_statement_adds_nothing(pg, app, make_user, login):
    user = make_user()
    client, card = login(user), wallet(app, user)
    assert confirm(client, preview(client, card, HEADER + LUNCH + BUS)).status_code == 302
    assert count(pg, user) == 2

    again = preview(client, card, HEADER + LUNCH + BUS)
    assert again.status_code == 200
    assert "Everything in this file is already in" in again.text and "Confirm import" not in again.text
    assert count(pg, user) == 2


def test_overlapping_statement_imports_only_new_rows(pg, app, make_user, login):
    user = make_user()
    client, card = login(user), wallet(app, user)
    confirm(client, preview(client, card, HEADER + LUNCH))
    page = preview(client, card, HEADER + LUNCH + BUS + LUNCH)  # the file has a second, genuine lunch
    assert "1 row is already in" in page.text and "Already in this wallet · will be skipped" in page.text
    assert "Add these 2 transactions" in page.text
    assert confirm(client, page).status_code == 302
    rows = pg.execute("SELECT description FROM transactions ORDER BY description").fetchall()
    assert rows == [("Bus",), ("Lunch",), ("Lunch",)]


def test_two_previews_of_one_file_cannot_both_be_saved(pg, app, make_user, login):
    user = make_user()
    client, card = login(user), wallet(app, user)
    first, second = preview(client, card, HEADER + LUNCH + BUS), preview(client, card, HEADER + LUNCH + BUS)
    confirm(client, first)
    assert confirm(client, second).status_code == 302
    assert count(pg, user) == 2


def test_same_amount_with_another_description_is_not_a_duplicate(pg, app, make_user, login):
    user = make_user()
    client, card = login(user), wallet(app, user)
    confirm(client, preview(client, card, HEADER + LUNCH))
    page = preview(client, card, HEADER + b"2026-11-12,expense,185.00,UAH,Food,Dinner\n")
    assert "already in" not in page.text
    confirm(client, page)
    assert count(pg, user) == 2


# --------------------------------------------------------------------------- currency change on edit


def test_moving_an_entry_to_another_currency_needs_confirmation(pg, app, make_user, login):
    user = make_user()
    client = login(user)
    card, euro = wallet(app, user, "Card", "UAH"), wallet(app, user, "Euro", "EUR")
    food = pg.execute("SELECT id FROM categories WHERE name = 'Food' AND kind = 'expense'").fetchone()[0]
    with app.app_context():
        record = ledger.create_transaction(user, ledger.TransactionDraft(card, food, "expense", Decimal(185), date(2026, 11, 12), "Lunch"))
    form = {"csrf_token": client.csrf, "kind": "expense", "wallet_id": euro, "category_id": food,
            "amount": "185.00", "occurred_on": "2026-11-12", "description": "Lunch"}

    warned = client.post(f"/transactions/{record}/edit", data=form)
    assert warned.status_code == 422
    assert "from UAH to EUR" in warned.text and "isn’t converted" in warned.text
    assert 'name="confirm_currency" value="EUR"' in warned.text
    assert pg.execute("SELECT currency FROM transactions WHERE id = %s", (record,)).fetchone() == ("UAH",)

    saved = client.post(f"/transactions/{record}/edit", data={**form, "amount": "4.20", "confirm_currency": "EUR"})
    assert saved.status_code == 302
    assert pg.execute("SELECT amount, currency FROM transactions WHERE id = %s", (record,)).fetchone() == (Decimal("4.20"), "EUR")

    # Changing wallets within the same currency needs no confirmation.
    other = wallet(app, user, "Second card", "EUR")
    assert client.post(f"/transactions/{record}/edit", data={**form, "wallet_id": other, "amount": "4.20"}).status_code == 302


# --------------------------------------------------------------------------- Kyiv dates


def test_today_is_kyiv_time_not_server_utc(monkeypatch):
    class LateNightUTC(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 11, 30, 23, 30, tzinfo=UTC)  # 01:30 on 1 December in Kyiv
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(ledger, "datetime", LateNightUTC)
    assert ledger.today() == date(2026, 12, 1)
    assert ledger.current_month() == date(2026, 12, 1)


def test_new_entry_form_defaults_to_kyiv_today(pg, app, make_user, login, monkeypatch):
    user = make_user()
    client = login(user)
    wallet(app, user)
    monkeypatch.setattr(ledger, "today", lambda: date(2026, 12, 1))
    assert 'name="occurred_on" required value="2026-12-01"' in client.get("/transactions/new").text


# --------------------------------------------------------------------------- sample data


def test_sample_data_is_marked_and_removed_without_touching_own_entries(pg, app, make_user, login):
    user = make_user()
    client = login(user)
    client.post("/demo", data={"csrf_token": client.csrf})
    assert "You’re looking at sample data" in client.get("/dashboard").text
    sample_count = count(pg, user)
    assert sample_count > 0
    assert pg.execute("SELECT count(*) FROM transactions t JOIN wallets w ON w.id=t.wallet_id WHERE w.user_id=%s AND NOT t.is_sample",
                      (user,)).fetchone() == (0,)

    cash = pg.execute("SELECT id FROM wallets WHERE user_id = %s AND name = 'Cash'", (user,)).fetchone()[0]
    food = pg.execute("SELECT id FROM categories WHERE name = 'Food' AND kind = 'expense'").fetchone()[0]
    with app.app_context():
        ledger.create_transaction(user, ledger.TransactionDraft(cash, food, "expense", Decimal(77), date(2026, 11, 3), "My own coffee"))
        ledger.set_budget(user, date(2026, 12, 1), Decimal(5000), "UAH")  # the user's own limit

    removed = client.post("/demo/remove", data={"csrf_token": client.csrf})
    page = client.get(removed.headers["Location"]).text
    assert f"Removed {sample_count} sample transactions" in page and "Kept Cash" in page
    assert "You’re looking at sample data" not in page
    assert pg.execute("SELECT w.name, t.description FROM transactions t JOIN wallets w ON w.id=t.wallet_id WHERE w.user_id=%s",
                      (user,)).fetchall() == [("Cash", "My own coffee")]
    assert pg.execute("SELECT name FROM wallets WHERE user_id = %s", (user,)).fetchall() == [("Cash",)]
    assert pg.execute("SELECT month_start, amount FROM monthly_budgets WHERE user_id = %s", (user,)).fetchall() == [
        (date(2026, 12, 1), Decimal("5000.00"))]


def test_changing_a_sample_limit_makes_it_the_users_own(pg, app, make_user):
    user = make_user()
    with app.app_context():
        demo.seed(user, today=date(2026, 11, 18))
        ledger.set_budget(user, date(2026, 11, 1), Decimal(9000), "UAH")
        ledger.remove_sample_data(user)
    assert pg.execute("SELECT month_start, amount FROM monthly_budgets WHERE user_id = %s", (user,)).fetchall() == [
        (date(2026, 11, 1), Decimal("9000.00"))]


def test_migration_marks_only_exact_older_sample_data(pg, app, make_user):
    seeded, lookalike = make_user(), make_user()
    with app.app_context():
        demo.seed(seeded, today=date(2026, 11, 18))
        ledger.create_wallet(lookalike, ledger.WalletDraft("Cash", "UAH", Decimal("1200.00")))  # a real wallet that looks similar
    pg.execute("UPDATE wallets SET is_sample = false; UPDATE transactions SET is_sample = false; UPDATE monthly_budgets SET is_sample = false")
    pg.execute("INSERT INTO transactions (wallet_id, category_id, kind, amount, currency, occurred_on, description) "
               "SELECT w.id, c.id, 'expense', 5, 'UAH', '2026-11-05', 'Mine' FROM wallets w, categories c "
               "WHERE w.user_id = %s AND w.name = 'Cash' AND c.name = 'Food' AND c.kind = 'expense'", (seeded,))

    for _ in range(2):  # running the migration again changes nothing
        for statement in sqlparse.split(MIGRATION_005.read_text(encoding="utf-8")):
            pg.execute(statement)

    assert pg.execute("SELECT count(*) FROM wallets WHERE user_id = %s AND is_sample", (seeded,)).fetchone() == (3,)
    assert pg.execute("SELECT count(*) FROM wallets WHERE user_id = %s AND is_sample", (lookalike,)).fetchone() == (0,)
    assert pg.execute("SELECT is_sample FROM transactions WHERE description = 'Mine'").fetchone() == (False,)
    assert pg.execute("SELECT count(*) FROM monthly_budgets WHERE user_id = %s AND is_sample", (seeded,)).fetchone() == (2,)


# --------------------------------------------------------------------------- exchange card


def test_exchange_card_opens_on_a_currency_with_a_rate(pg, app, make_user, login, monkeypatch):
    user = make_user()
    client = login(user)
    wallet(app, user)
    eur = fx.Rate("EUR", Decimal("44.1"), "Monobank", datetime.now(UTC), sell_value=Decimal("44.9"))
    monkeypatch.setattr(fx, "load_rates", lambda codes: {"EUR": eur})
    page = client.get("/dashboard").text
    assert re.search(r'value="EUR"\s+data-flag="[^"]+" checked', page)
    assert re.search(r'data-quote="USD" hidden', page) and not re.search(r'data-quote="EUR" hidden', page)
