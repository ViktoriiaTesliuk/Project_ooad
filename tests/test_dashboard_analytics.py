from datetime import date, datetime, timezone
from decimal import Decimal

from pocket_ledger import analytics, db, fx, ledger


SELECTED = date(2024, 2, 1)
RATE = fx.Rate("USD", Decimal("44.63"), "Monobank", datetime.now(timezone.utc),
               sell_value=Decimal("45.03"))


def test_empty_and_missing_currency_buckets():
    summary, missing, missing_budget, daily, monthly, history = analytics.summarize_dashboard(
        SELECTED, [date(2024, 1, 1), SELECTED],
        [("2024-01-01", "EUR", "income", None, None, "30")],
        ledger.Budget(SELECTED, Decimal("50"), "USD"), "UAH", {},
    )
    assert not summary.currencies and not missing and missing_budget
    assert len(daily["points"]) == 29 and daily["points"][-1][0] == "29 Feb 2024"
    assert not daily["ticks"] and not monthly["ticks"] and history == ["EUR"]


def test_actual_values_match_all_charts_after_edit_and_delete(pg, app, make_user, monkeypatch):
    monkeypatch.setattr(fx, "load_rates", lambda _: {"USD": RATE})
    user = make_user()
    other = make_user()
    with app.app_context():
        cash = ledger.create_wallet(user, ledger.WalletDraft("Cash", "UAH", Decimal("500")))
        usd = ledger.create_wallet(user, ledger.WalletDraft("USD", "USD", Decimal("120000")))
        private = ledger.create_wallet(other, ledger.WalletDraft("Private", "UAH", Decimal(0)))
        categories = ledger.list_categories()
        education = next(c.id for c in categories if c.name == "Education")
        salary = next(c.id for c in categories if c.name == "Salary")
        food = next(c.id for c in categories if c.name == "Food")
        large = ledger.TransactionDraft(usd, education, "expense", Decimal("100000"), date(2024, 2, 6))
        record = ledger.create_transaction(user, large)
        ledger.create_transaction(user, ledger.TransactionDraft(
            cash, food, "expense", Decimal("125.25"), date(2024, 2, 2)
        ))
        ledger.create_transaction(user, ledger.TransactionDraft(
            cash, salary, "income", Decimal("500"), date(2024, 2, 6)
        ))
        ledger.create_transaction(user, ledger.TransactionDraft(
            usd, food, "expense", Decimal("10"), date(2023, 12, 31)
        ))
        ledger.create_transaction(other, ledger.TransactionDraft(
            private, food, "expense", Decimal("9999"), date(2024, 2, 6)
        ))
        ledger.set_budget(user, SELECTED, Decimal("6000000"), "UAH")

        assert db.rows(db.execute("SELECT CAST(amount AS TEXT) FROM transactions WHERE id = :id",
                                  {"id": record})) == [("100000.00",)]
        summary, missing, _, daily, monthly, _, _ = analytics.load_dashboard(user, SELECTED, "UAH")
        values = summary.currencies[0]
        assert not missing
        assert values.income == Decimal("500") and values.expenses == Decimal("4463125.25")
        assert summary.budget.spent == values.expenses
        assert sum(item.amount for item in values.categories) == values.expenses
        assert daily["points"][5][2][0][1] == Decimal("4463000")
        assert daily["points"][0][2][0][1] == Decimal(0)
        assert len(daily["points"]) == 29 and any("M" in label for _, label in daily["ticks"])
        assert len(monthly["points"]) == 6
        assert monthly["points"][3][0] == "December 2023"
        assert monthly["points"][3][2][1][1] == Decimal("446.30")
        assert monthly["points"][-1][2][1][1] == values.expenses
        assert ledger.get_wallet(user, usd).balance == Decimal("19990")

        edit = ledger.TransactionDraft(usd, education, "expense", Decimal("100"), date(2024, 2, 7))
        ledger.update_transaction(user, record, edit)
        _, _, _, daily, *_ = analytics.load_dashboard(user, SELECTED, "UAH")
        assert daily["points"][5][2][0][1] == Decimal(0)
        assert daily["points"][6][2][0][1] == Decimal("4463")
        ledger.delete_transaction(user, record)
        summary, _, _, daily, monthly, *_ = analytics.load_dashboard(user, SELECTED, "UAH")
        assert summary.budget.spent == Decimal("125.25")
        assert daily["points"][6][2][0][1] == Decimal(0)
        assert monthly["points"][-1][2][1][1] == Decimal("125.25")
