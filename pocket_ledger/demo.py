"""Synthetic sample data for demos and first-time exploration.

Every name and amount here is invented. Seeding only works on an account with
no wallets, so it can never mix with a user's real records.
"""

from dataclasses import dataclass
from decimal import Decimal

from . import db, ledger

WALLETS = (
    ("Everyday card", "UAH", "8000.00"),
    ("Cash", "UAH", "1200.00"),
    ("Travel card", "EUR", "150.00"),
)

# (day of month, wallet, kind, category, amount, description)
TRANSACTIONS = (
    (1, "Everyday card", "income", "Salary", "32000.00", "Monthly salary"),
    (2, "Everyday card", "expense", "Housing", "9500.00", "Rent share"),
    (3, "Everyday card", "expense", "Food", "412.30", "Weekly groceries"),
    (4, "Cash", "expense", "Transport", "15.00", "Bus fare"),
    (5, "Cash", "expense", "Food", "185.00", "Lunch"),
    (7, "Everyday card", "expense", "Transport", "240.00", "Taxi home"),
    (9, "Everyday card", "expense", "Food", "1064.75", "Groceries for the week"),
    (10, "Everyday card", "expense", "Other", "349.00", "Phone plan"),
    (12, "Travel card", "expense", "Food", "18.40", "Café on a city trip"),
    (14, "Travel card", "expense", "Transport", "42.00", "Train ticket"),
    (15, "Cash", "income", "Other", "600.00", "Sold old headphones"),
    (18, "Cash", "expense", "Food", "96.50", "Coffee and pastry"),
    (21, "Everyday card", "expense", "Food", "938.20", "Groceries"),
    (24, "Everyday card", "expense", "Transport", "320.00", "Monthly transit pass"),
)

BUDGETS = (("current", "12000.00", "UAH"), ("previous", "11000.00", "UAH"))


class AccountNotEmpty(RuntimeError):
    """Sample data is only added to accounts without wallets."""


@dataclass(frozen=True)
class SeedSummary:
    wallets: int
    transactions: int


def seed(user_id, *, today=None):
    """Add sample wallets, this and last month's entries, and two monthly limits.

    Entries dated after ``today`` are skipped, so the dashboard never shows
    future spending. Everything is written in one database transaction.
    """
    today = today or ledger.today()
    if ledger.list_wallets(user_id):
        raise AccountNotEmpty("This account already has wallets.")
    categories = {(c.kind, c.name): c.id for c in ledger.list_categories()}
    current = today.replace(day=1)
    previous = ledger.previous_month(current)

    with db.transaction() as transaction_id:
        wallet_ids = {
            name: ledger.create_wallet(
                user_id, ledger.WalletDraft(name, currency, Decimal(balance)),
                transaction_id=transaction_id,
            )
            for name, currency, balance in WALLETS
        }
        count = 0
        for month, scale in ((previous, Decimal("0.94")), (current, Decimal(1))):
            for day, wallet, kind, category, amount, description in TRANSACTIONS:
                occurred_on = month.replace(day=day)
                if occurred_on > today:
                    continue
                value = Decimal(amount)
                if kind == "expense" and category != "Housing":
                    value = (value * scale).quantize(ledger.CENT)
                ledger.create_transaction(
                    user_id,
                    ledger.TransactionDraft(
                        wallet_ids[wallet], categories[(kind, category)], kind,
                        value, occurred_on, description,
                    ),
                    transaction_id=transaction_id,
                )
                count += 1
        for which, amount, currency in BUDGETS:
            ledger.set_budget(
                user_id, current if which == "current" else previous,
                Decimal(amount), currency, transaction_id=transaction_id,
            )
        # Mark everything created here, so it can be shown as sample data and removed later.
        for wallet_id in wallet_ids.values():
            for table, column in (("wallets", "id"), ("transactions", "wallet_id")):
                db.execute(f"UPDATE {table} SET is_sample = true WHERE {column} = :wallet_id",
                           {"wallet_id": wallet_id}, transaction_id=transaction_id)
        db.execute(
            "UPDATE monthly_budgets SET is_sample = true WHERE user_id = :user_id AND month_start IN (:current, :previous)",
            {"user_id": user_id, "current": current, "previous": previous}, transaction_id=transaction_id,
        )
    return SeedSummary(len(wallet_ids), count)
