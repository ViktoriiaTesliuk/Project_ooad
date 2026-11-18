# Pocket Ledger

Pocket Ledger is a personal budgeting app for recording everyday spending, managing wallets, and checking how much remains in a monthly budget.

## Features

- Google sign-in
- Cash and card wallets in UAH, USD, EUR, PLN or GBP; balances are derived from
  the opening balance plus recorded income and expenses
- Income and expense entry, editing and deletion, with history filters by
  month, wallet and category
- A monthly spending limit and a dashboard with income, expenses, remaining
  budget and spending by category in a selected display currency
- CSV import with a preview and explicit confirmation
- Gemini-assisted expense drafts with manual review

## Project documents

- [Architecture diagram](ARCHITECTURE.md)
- [Business pitch](BUSINESS_PITCH.md)

## Repository workflow

Use `main` as the base branch. Create a separate branch for each change and open a pull request for review before merging.

## Local setup

Use Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -c 'import secrets; print(secrets.token_hex(32))'
```

Set `SECRET_KEY` in `.env` to the generated value and start Flask.

```bash
flask --app pocket_ledger run
```

Open `http://localhost:5000`. The sign-in page and `GET /health` work
without AWS or Google credentials. Visiting `/dashboard` while signed out
redirects to the sign-in page.

### Google Sign-In

Create a Google OAuth web client and add
`http://localhost:5000/auth/google/callback` as an authorized redirect URI.
Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `PUBLIC_BASE_URL` in
`.env`. If the OAuth app is in testing mode, add your Google account as a
test user. For an HTTPS deployment, set `PUBLIC_BASE_URL` to the public
origin, register its callback with Google, and enable
`SESSION_COOKIE_SECURE`.

### Aurora schema

With an existing Aurora PostgreSQL cluster, enable the RDS Data API.
Create the database if it does not already exist. Set `RDS_RESOURCE_ARN`,
`RDS_SECRET_ARN`, `RDS_DATABASE`, and `AWS_DEFAULT_REGION`. Use the standard
AWS credential chain, such as an existing AWS CLI profile locally or an
execution role on Lambda.

```bash
flask --app pocket_ledger init-db
```

This applies the numbered SQL files in `migrations/` in one transaction.
Rerun it on an existing database to add the CSV import table and the
exchange sell rate column. The migration
identity needs the RDS Data API execute and transaction actions plus access
to the database secret. Its database user needs permission to create the
tables. Use only resources covered by the available AWS Free account plan
and credits.

## CSV import

From **History → Import CSV**, choose a wallet, upload a UTF-8 comma-delimited
file, and check the preview before confirming. The [example CSV](examples/transactions.csv)
uses these columns:

| Column | Format |
| --- | --- |
| `date` | YYYY-MM-DD |
| `kind` | `expense` or `income` |
| `amount` | Positive decimal, up to two fractional digits |
| `currency` | The selected wallet's currency code |
| `category` | An existing category name for the row's kind |
| `description` | Optional text, up to 500 characters |

Monobank statements can also be uploaded directly. The importer reads the
Ukrainian operation-date header, description, MCC, and signed card-currency
amount. Select a wallet in the same currency as the card. Where the amount
cell includes a currency code, the importer checks it against the wallet.
Common food and transport MCCs are categorized automatically; other rows
use the `Other` category for their transaction kind.

A file can hold up to 100 transactions and 128 KiB. Correct all marked
rows before confirming. A preview is valid for 30 minutes and its
confirmation can be retried without inserting the batch again. Uploading
the same file as a new preview starts a separate batch.

## Exchange rates

Dashboard and budget comparisons use the latest available rate when
viewed, including for past months. Wallets and history retain their
original currency. Conversion uses UAH as the common currency. Monobank's
buy rate converts into UAH and its sell rate converts out of UAH. A
quoted cross rate can serve both directions, with the NBU
official rate as a per-currency fallback. Aurora caches fetched rates
for five minutes and provides the last known rate during an outage.
The dashboard shows the source and fetch time and labels cached rates
and incomplete totals.

## Gemini draft setup

Set `GEMINI_API_KEY` to a key from a Gemini API Free Tier project with
paid billing disabled. The app uses Gemini 3.1 Flash-Lite. Leave the key
unset to use manual entry. **Fill from text** sends the typed expense
description and allowed expense categories to Gemini. Review the suggested
fields and the selected wallet currency before using **Add transaction**.

## Tests

```bash
python -m pip install -r requirements-dev.txt
docker run -d --rm --name pocket-ledger-test-db -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=pocket_ledger_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/pocket_ledger_test python -m pytest
```

The database tests run the real SQL against that local PostgreSQL through a
stand-in for the RDS Data API and recreate its schema each time, so never
point `TEST_DATABASE_URL` at real data. Without it, only the database-free
tests run.

## Contributor contracts

- Register feature routes as Flask blueprints in `pocket_ledger.create_app`.
  Use `pocket_ledger.auth.login_required` for private views.
- `flask.g.user_id` contains the signed-in local user ID or `None`.
  Restrict queries for payments, wallets, and budgets to that user.
- `pocket_ledger.db.execute(sql, params)` accepts named SQL placeholders
  such as `:user_id` and a dictionary of parameters. It returns the raw
  RDS Data API response. Read selected values from `records`; monetary
  columns are returned as strings by default and should be decoded as Decimal.
- Wallet currency must match transaction currency. Use `kind` for income
  or expense, `occurred_on` for the transaction date, and the first day of
  the month as `monthly_budgets.month_start`.
- `pocket_ledger.db.rows(response)` decodes `records` into tuples, and
  `pocket_ledger.db.transaction()` yields a transaction ID for statements that
  must commit together.
- Every POST, PUT, PATCH and DELETE must carry the session CSRF token, either as
  a `csrf_token` form field (`{{ csrf_token() }}` in templates) or as an
  `X-CSRF-Token` header (`pocket_ledger.csrf`).
- Extend `pocket_ledger/templates/base.html` for new pages; it contains the
  shared navigation. The protected `ledger.dashboard` (`/dashboard`) is the
  landing page after sign-in.

### Recording transactions from other sources

`pocket_ledger.ledger` is the only place that validates and stores income and
expenses; it does not depend on the Flask request. CSV import and AI drafts
should reuse it rather than writing SQL:

```python
from pocket_ledger import ledger

context = ledger.load_context(g.user_id)            # the user's wallets + categories
wallet = context.find_wallet("Cash")                 # case-insensitive name lookups
category = context.find_category("Food", "expense")
draft = ledger.parse_transaction({                   # raises ledger.ValidationError
    "kind": "expense", "wallet_id": wallet.id, "category_id": category.id,
    "amount": "125,50", "occurred_on": "2026-11-18", "description": "Lunch",
}, context)
ledger.create_transactions(g.user_id, [draft])       # all rows or none
```

- `parse_transaction` accepts form strings or typed values (`int`, `Decimal`,
  `float`, `date`) and reports every invalid field in
  `ValidationError.errors` as `{field: message}`, which suits per-row import
  errors and an editable AI draft form.
- The stored currency always comes from the wallet. A wallet that belongs to
  another user raises `ledger.NotFound`.
- `create_transaction(user_id, draft, transaction_id=...)` joins an existing
  `db.transaction()`. `update_transaction`, `delete_transaction`,
  `get_transaction` and `list_transactions` are scoped to the user in the
  same way.
