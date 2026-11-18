import io
import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from pocket_ledger import db, fx, ledger

HEADER = b"date,kind,amount,currency,category,description\n"
EXAMPLE = Path(__file__).resolve().parents[1] / "pocket_ledger" / "static" / "sample-transactions.csv"


def _wallet(app, user, currency="UAH"):
    with app.app_context():
        return ledger.create_wallet(user, ledger.WalletDraft("Cash", currency, Decimal(0)))


def _preview(client, wallet, content):
    return client.post("/transactions/import", data={
        "csrf_token": client.csrf, "wallet_id": wallet,
        "csv_file": (io.BytesIO(content), "transactions.csv"),
    })


def test_csv_preview_confirm_and_retry(pg, app, make_user, login):
    user = make_user()
    client = login(user)
    wallet = _wallet(app, user)
    response = _preview(client, wallet, EXAMPLE.read_bytes())
    assert response.status_code == 200
    assert "Lunch" in response.text
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone() == (0,)
    token = re.search(r'name="token" value="([^"]+)"', response.text)[1]
    confirm = {"csrf_token": client.csrf, "token": token}

    other = login(make_user())
    assert other.post("/transactions/import/confirm", data=confirm).status_code == 404
    assert _preview(other, wallet, EXAMPLE.read_bytes()).status_code == 404
    for _ in range(2):
        assert client.post("/transactions/import/confirm", data=confirm).status_code == 302
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone() == (3,)
    assert pg.execute("SELECT payload FROM csv_imports").fetchone() == (None,)


def test_csv_reports_invalid_rows_without_staging(pg, app, make_user, login):
    user = make_user()
    client = login(user)
    wallet = _wallet(app, user)
    response = _preview(client, wallet, HEADER + b"bad,expense,-5,USD,Food,Invalid\n")
    assert response.status_code == 422
    assert "Row 2" in response.text and "Use UAH" in response.text
    assert "Confirm import" not in response.text
    assert pg.execute("SELECT COUNT(*) FROM csv_imports").fetchone() == (0,)
    assert _preview(client, wallet, b" " * 160_000).status_code == 413


def test_csv_confirmation_rolls_back_and_can_retry(pg, app, make_user, login, monkeypatch):
    user = make_user()
    client = login(user)
    wallet = _wallet(app, user)
    response = _preview(client, wallet, EXAMPLE.read_bytes())
    token = re.search(r'name="token" value="([^"]+)"', response.text)[1]
    confirm = {"csrf_token": client.csrf, "token": token}
    create = ledger.create_transaction
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "create_transaction", _fail_second(create))
        with pytest.raises(RuntimeError, match="failed"):
            client.post("/transactions/import/confirm", data=confirm)
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone() == (0,)
    assert client.post("/transactions/import/confirm", data=confirm).status_code == 302
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone() == (3,)


def _fail_second(create):
    calls = iter((False, True))
    def insert(*args, **kwargs):
        if next(calls):
            raise RuntimeError("failed")
        return create(*args, **kwargs)
    return insert


def test_rate_fallback_and_cached_rate(monkeypatch):
    monkeypatch.setattr(db, "execute", Mock(return_value={"records": []}))
    feeds = {
        fx.MONOBANK_URL: [{"currencyCodeA": 840, "currencyCodeB": 980, "rateBuy": 39, "rateSell": 41}],
        fx.NBU_URL: [{"cc": "EUR", "rate": 45}],
    }
    monkeypatch.setattr(fx, "_feed", feeds.__getitem__)
    rates = fx.load_rates({"USD", "EUR"})
    assert rates["USD"].source == "Monobank"
    assert rates["EUR"].source == "NBU"
    assert fx.convert(Decimal(45), "USD", "EUR", rates) == Decimal(39)
    assert fx.convert(Decimal(41), "UAH", "USD", rates) == Decimal(1)
    assert db.execute.call_count == 3

    stale_eur = [["EUR", "45", "NBU", "2020-01-01 00:00:00+00", "45"]]
    monkeypatch.setattr(db, "rows", lambda _: stale_eur)
    monkeypatch.setattr(fx, "_feed", lambda _: [])
    rates = fx.load_rates({"EUR", "USD"})
    assert rates["EUR"].cached and rates["EUR"].fetched_at.year == 2020
    assert fx.convert(Decimal(1), "USD", "EUR", rates) is None


def test_monobank_statement_preview():
    from werkzeug.datastructures import FileStorage

    from pocket_ledger.csv_import import parse_file

    statement = (
        "Звіт за карткою\n"
        "Дата операції;Опис;MCC;Сума в валюті картки;Валюта операції\n"
        "12.11.2026 14:25:00;Lunch;5812;−185,25 UAH;EUR\n"
        "2026.11.13 09:00:00;Transfer;0000;500,00;UAH\n"
        "14.11.2026 08:00:00;Bus;4111;-30 EUR;EUR\n"
    )
    wallet = ledger.Wallet(1, "Cash", "UAH", Decimal(0), Decimal(0), 0)
    context = ledger.TransactionContext({1: wallet}, {
        1: ledger.Category(1, "Food", "expense"),
        2: ledger.Category(2, "Other", "income"),
        3: ledger.Category(3, "Transport", "expense"),
    })
    rows = parse_file(FileStorage(io.BytesIO(statement.encode("utf-8"))), wallet, context)
    assert rows[0][2].amount == Decimal("185.25")
    assert rows[0][2].kind == "expense" and rows[0][3] == {}
    assert rows[1][2].kind == "income" and rows[1][3] == {}
    assert rows[2][3] == {"currency": "Use UAH for this wallet."}
