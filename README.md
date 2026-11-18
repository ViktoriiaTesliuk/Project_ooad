# Pocket Ledger

Pocket Ledger is a personal budgeting app for recording everyday spending, managing wallets, and checking how much remains in a monthly budget.

## Planned features

- Google sign-in
- Cash and card wallets with currency conversion
- Manual transaction entry and CSV import
- Monthly budgets and spending summaries
- AI-assisted drafts for manual expense entry

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

This applies `migrations/001_initial.sql` in one transaction. The initial
schema and default categories can be applied again safely. The migration
identity needs the RDS Data API execute and transaction actions plus access
to the database secret. Its database user needs permission to create the
tables. Use only resources covered by the available AWS Free account plan
and credits.

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
- Extend `pocket_ledger/templates/base.html` for new pages. The protected
  `/dashboard` is the landing page after sign-in.
