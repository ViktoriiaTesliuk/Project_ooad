"""Validation and dashboard rules that need no database."""

from datetime import date
from decimal import Decimal

import pytest

from pocket_ledger import ledger


def context():
    wallet = ledger.Wallet(7, "Cash", "UAH", Decimal(0), Decimal(0), 0)
    return ledger.TransactionContext(
        wallets={7: wallet},
        categories={
            1: ledger.Category(1, "Food", "expense"),
            5: ledger.Category(5, "Salary", "income"),
        },
    )


@pytest.mark.parametrize(("text", "expected"), [
    ("125", Decimal("125.00")),
    ("125,5", Decimal("125.50")),
    ("1 234,50", Decimal("1234.50")),
    ("1,234.50", Decimal("1234.50")),
    ("0.1", Decimal("0.10")),
    (12.5, Decimal("12.50")),
])
def test_parse_amount_accepts_common_formats(text, expected):
    assert ledger.parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", "1.005", "NaN", "Infinity", "1e20"])
def test_parse_amount_rejects_invalid_values(text):
    with pytest.raises(ValueError):
        ledger.parse_amount(text)


def test_opening_balance_may_be_negative():
    assert ledger.parse_amount("-20.5", positive=False) == Decimal("-20.50")


def test_parse_transaction_returns_typed_draft():
    draft = ledger.parse_transaction(
        {"kind": "expense", "wallet_id": "7", "category_id": "1", "amount": "42,10",
         "occurred_on": "2026-11-03", "description": "  Lunch "},
        context(),
    )
    assert draft == ledger.TransactionDraft(7, 1, "expense", Decimal("42.10"), date(2026, 11, 3), "Lunch")


def test_parse_transaction_accepts_typed_values_from_imports():
    draft = ledger.parse_transaction(
        {"kind": "income", "wallet_id": 7, "category_id": 5,
         "amount": Decimal(1000), "occurred_on": date(2026, 11, 1)},
        context(),
    )
    assert draft.amount == Decimal("1000.00") and draft.description == ""


def test_parse_transaction_reports_every_problem():
    with pytest.raises(ledger.ValidationError) as caught:
        ledger.parse_transaction(
            {"kind": "income", "wallet_id": "99", "category_id": "1",
             "amount": "-1", "occurred_on": "31.12.2026"},
            context(),
        )
    assert set(caught.value.errors) == {"wallet_id", "category_id", "amount", "occurred_on"}


def test_context_lookups_are_case_insensitive():
    assert context().find_wallet(" cash ").id == 7
    assert context().find_category("FOOD", "expense").id == 1
    assert context().find_category("Food", "income") is None


def test_parse_month():
    assert ledger.parse_month("2026-02") == date(2026, 2, 1)
    assert ledger.parse_month("", default=None) is None
    with pytest.raises(ValueError):
        ledger.parse_month("2026-13")


def test_budget_only_counts_expenses_in_its_currency():
    month = date(2026, 11, 1)
    summary = ledger.summarize(
        month,
        totals=[
            ("UAH", "expense", Decimal("300.00")),
            ("UAH", "income", Decimal("1000.00")),
            ("USD", "expense", Decimal("50.00")),
        ],
        category_totals=[
            ("UAH", 1, "Food", Decimal("200.00")),
            ("UAH", 2, "Transport", Decimal("100.00")),
            ("USD", 1, "Food", Decimal("50.00")),
        ],
        budget=ledger.Budget(month, Decimal("1000.00"), "UAH"),
    )
    assert summary.budget.spent == Decimal("300.00")
    assert summary.budget.remaining == Decimal("700.00")
    assert summary.budget.used_percent == Decimal("30.0")
    assert summary.budget.excluded_currencies == ("USD",)
    uah, usd = summary.currencies
    assert (uah.currency, uah.net) == ("UAH", Decimal("700.00"))
    assert [(c.name, c.share) for c in uah.categories] == [("Food", Decimal("66.7")), ("Transport", Decimal("33.3"))]
    assert usd.expenses == Decimal("50.00")


def test_budget_without_matching_expenses_is_untouched():
    month = date(2026, 11, 1)
    summary = ledger.summarize(
        month, [("USD", "expense", Decimal(10))], [],
        ledger.Budget(month, Decimal("100.00"), "EUR"),
    )
    assert summary.budget.spent == 0
    assert summary.budget.remaining == Decimal("100.00")
    assert not summary.budget.over
