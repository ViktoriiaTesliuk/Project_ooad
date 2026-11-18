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
- CSV and Monobank statement import with a preview and explicit confirmation
- AI expense entry from one sentence or a receipt photo, with a review step before saving
- Responsive pages for phones and desktops, with light and dark themes
- Synthetic sample data for demos

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
redirects to the sign-in page. Signing in and every ledger page need Google
Sign-In and an Aurora database reachable through the RDS Data API, configured
as below; the same database can serve local development and Lambda.

### Google Sign-In

Create a Google OAuth web client and add
`http://localhost:5000/auth/google/callback` as an authorized redirect URI.
Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `PUBLIC_BASE_URL` in
`.env`. If the OAuth app is in testing mode, add your Google account as a
test user. For HTTPS on AWS, see [Deploy on AWS](#deploy-on-aws-from-the-console).

### Aurora schema

With an existing Aurora PostgreSQL cluster, enable the RDS Data API.
Create the database if it does not already exist. Set `RDS_RESOURCE_ARN`,
`RDS_SECRET_ARN`, `RDS_DATABASE`, and `AWS_DEFAULT_REGION`. Use the standard
AWS credential chain, such as an existing AWS CLI profile locally or an
execution role on Lambda.

```bash
flask --app pocket_ledger init-db
```

This applies the numbered SQL files in `migrations/` in one transaction. Every
migration can be applied again safely, so rerun it after each update. The
migration identity needs the RDS Data API execute and transaction actions
plus access to the database secret, and its database user needs permission to
create tables. On Lambda, the same migrations run from a test event (see
below).

### Sample data

A new account's dashboard offers **Explore with sample data**, which adds
three invented wallets (two in UAH, one in EUR), this and last month's
income and expenses, and a monthly limit. It only works on an account with no
wallets, so it never mixes with real records. From a terminal with database
access, the same data can be added to an account that has signed in once:

```bash
flask --app pocket_ledger seed-demo you@example.com
```

## CSV import

From **History → Import CSV**, choose a wallet, upload a UTF-8 comma-delimited
file, and check the preview before confirming. The [sample CSV](pocket_ledger/static/sample-transactions.csv)
(also downloadable from the import page)
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

## AI expense entry

**Add expenses with AI** on the dashboard and the **Add** page records expenses
from one sentence or a receipt photo, in English or Ukrainian:

- *Spent 250 UAH on groceries at Silpo yesterday* or *Таксі 500 грн і обід 150 грн*
  each become one draft per expense, with the amount, currency, date, description
  and category filled in. A missing date means today (Kyiv time), and "yesterday"
  or "вчора" is resolved from today.
- **Scan a receipt** reads the merchant, date, total, currency and purchased items
  from a photo or screenshot. The browser shrinks the photo to 1600 px first.
- Each expense goes to the most used wallet in its currency. If no currency is
  given, it goes to the most used wallet overall.
- The **Check** page shows every draft for correction. Fields the model was
  unsure about, or that had to be assumed, are marked **Check**. A value is left
  empty rather than invented. **Save** stores the selected drafts in one database
  transaction and opens the dashboard.
- Duplicates are caught twice. The same receipt file is never sent to Gemini
  again once saved: the page links to the saved expense instead. Another photo of
  an already saved receipt, matched by merchant, date and total, is unselected
  with a warning. Saving the same draft twice adds nothing.
- Each user has `AI_DAILY_LIMIT` requests per Kyiv day (default 30), shared by
  text and receipts, so one account can't use up the free quota.

Setup: create an API key in Google AI Studio for a project on the Gemini API Free
Tier, keep paid billing disabled for that project, and set `GEMINI_API_KEY` (in the
app secret on AWS). Without a key the AI box is hidden and manual entry works as
usual. The app uses Gemini 3.1 Flash-Lite with a JSON response schema, at
temperature 0, and makes one request per recognition.

Privacy: only the typed text or the receipt photo, plus the expense category
names, are sent to Gemini. Free Tier requests may be used by Google to improve its
products, which the AI box and the Privacy Policy say. Photos are processed in
memory and never stored. For saved receipts the app keeps only a SHA-256 of the
file and of the merchant, date and total. Unsaved drafts expire after 30 minutes.
The API key never leaves the server.

## Deploy on AWS from the Console

The release runs as one Lambda function behind an HTTPS Function URL, talking
to Aurora PostgreSQL Serverless v2 through the RDS Data API. Credentials live
in AWS Secrets Manager. The function runs outside a VPC: the Data API,
Secrets Manager, Google, Gemini and the rate feeds are all reached over
HTTPS, so no NAT gateway is needed.

### Before you start: cost limits

Use an AWS account on the **AWS Free account plan** with available credits.
Do not upgrade the plan to deploy Pocket Ledger, and stop if the console asks
you to. Aurora, Secrets Manager and CloudWatch are paid by usage, so check how
much credit is left under **Billing and Cost Management → Credits**, and set up:

- **A budget alert.** In **Billing and Cost Management → Budgets**, create a
  monthly cost budget with an email alert well below your remaining credits.
- **Aurora that scales to zero.** Set the minimum capacity to 0 ACUs
  (automatic pause) and the maximum to 1–2 ACUs. If your engine version
  can't go to 0, use the lowest minimum it offers. The first request after a
  pause takes several seconds while the database resumes.
- **A capped Lambda.** If the account's concurrency quota allows it, set
  reserved concurrency to a small number such as 5.
- **Short log retention.** Set the function's CloudWatch log group to keep
  logs for 1 or 2 weeks.

Pick one region for everything below, for example `eu-central-1`.

### 1. Build the deployment package

```bash
python -m pip install -r requirements-dev.txt
python scripts/build_lambda.py              # dist/pocket-ledger-lambda-arm64.zip
python scripts/smoke_lambda_package.py dist/pocket-ledger-lambda-arm64.zip   # needs Docker
```

The build installs the exact, hash-checked versions in
`requirements-lambda.txt` as Linux wheels for the Lambda `python3.13`
runtime. It adds `pocket_ledger/` (including templates and static files) and
`migrations/`, checks that they and the native `cryptography` library are
present, and prints the zip's sha256. Rebuilding the same commit gives the
same hash. `.env` and other local files are never packaged. Use
`--arch x86_64` for an x86 function. After changing `requirements.txt`, run
`python scripts/build_lambda.py --lock` and commit the new lock file.

The smoke test runs the zip in the official AWS Lambda Python image and
sends it Function URL events. It checks:

- HTML pages and static assets
- the Google redirect, using the HTTPS callback URL
- the session cookie and its `Secure`, `HttpOnly` and `SameSite` flags
- CSRF rejection of a base64 multipart upload
- error pages

### 2. Aurora PostgreSQL with the Data API

In **Aurora and RDS → Create database**:

1. Choose **Aurora (PostgreSQL Compatible)**, the latest available engine
   version, and the dev/test template.
2. Name the cluster `pocket-ledger`, and under **Credentials management**
   choose **Managed in AWS Secrets Manager**.
3. Choose **Serverless v2** with the capacity limits above, and a single
   instance with no replicas.
4. Turn off public access. The Data API does not need it.
5. Under **Connectivity**, select **Enable the RDS Data API**.
6. Under **Additional configuration**, set **Initial database name** to
   `pocket_ledger`.

When the cluster is available, copy its ARN from **Configuration** and the
credentials secret's ARN from the cluster's **Configuration → Master
credentials ARN**. To check access, connect with **Query editor** using that
secret and the `pocket_ledger` database, and run `SELECT 1`. The query editor
uses the Data API, so if it can't use the managed secret, neither can the
app. In that case, store a secret of type **Credentials for Amazon RDS
database** with the same username and password for this cluster, and use
that secret's ARN instead.

### 3. App secret in Secrets Manager

In **Secrets Manager → Store a new secret**, choose **Other type of secret**,
and add these keys on the plaintext tab:

```json
{
  "SECRET_KEY": "output of: python -c 'import secrets; print(secrets.token_hex(32))'",
  "GOOGLE_CLIENT_ID": "….apps.googleusercontent.com",
  "GOOGLE_CLIENT_SECRET": "…",
  "GEMINI_API_KEY": "…"
}
```

Name it `pocket-ledger/app` and turn off automatic rotation. Omit
`GEMINI_API_KEY` to deploy without AI drafts. Any other key stops the
function at startup, which catches a database secret pasted here by mistake.
Copy the secret's ARN.

### 4. Execution role with scoped permissions

In **IAM → Roles → Create role**:

1. Choose **AWS service → Lambda** and attach the managed policy
   **AWSLambdaBasicExecutionRole**, which covers CloudWatch logs.
2. Name the role `pocket-ledger-lambda`.
3. Add an inline policy from
   [`deploy/lambda-execution-policy.json`](deploy/lambda-execution-policy.json).
   Replace the cluster ARN placeholders, `DATABASE_SECRET_ARN` and
   `APP_SECRET_ARN` with your values.

The role can then run SQL on that one cluster through the Data API and read
those two secrets, and nothing else. If you encrypted a secret with your own
KMS key, also allow `kms:Decrypt` on that key.

### 5. Lambda function and HTTPS Function URL

In **Lambda → Create function**:

1. Choose **Author from scratch**, name it `pocket-ledger`, and select runtime
   **Python 3.13**, architecture **arm64** (matching the zip), and the
   existing role `pocket-ledger-lambda`.
2. Under **Code → Upload from → .zip file**, upload
   `dist/pocket-ledger-lambda-arm64.zip`. Under **Runtime settings**, set the
   handler to `pocket_ledger.lambda_handler.handler`.
3. Under **Configuration → General configuration**, set memory to 512 MB and
   the timeout to 60 seconds. That leaves room for Aurora to resume from a
   pause (up to 25 seconds) plus a receipt recognition.
4. Under **Configuration → Function URL → Create function URL**, choose auth
   type **NONE**, since the app does its own sign-in, and leave CORS off.
   Copy the URL, for example `https://abc123.lambda-url.eu-central-1.on.aws`.
5. Under **Configuration → Environment variables**, add:

| Variable | Value |
| --- | --- |
| `PUBLIC_BASE_URL` | The Function URL without a trailing slash |
| `SESSION_COOKIE_SECURE` | `true` |
| `RDS_RESOURCE_ARN` | The Aurora cluster ARN |
| `RDS_SECRET_ARN` | The cluster's credentials secret ARN |
| `RDS_DATABASE` | `pocket_ledger` |
| `APP_SECRETS_ARN` | The `pocket-ledger/app` secret ARN |

Lambda sets the region itself (`AWS_REGION`), so don't add
`AWS_DEFAULT_REGION`. Instead of `APP_SECRETS_ARN`, the four app secrets can
also be set as environment variables, but keeping them in Secrets Manager
keeps them out of the function's configuration page.

### 6. Apply the migrations

On the function's **Test** tab, create a test event named `migrate` with this
body and run it:

```json
{"action": "migrate"}
```

The result should be `{"status": "migrated"}`. Run it again after every
deployment; it is safe to repeat. If it times out on the first run, the
database was resuming, so run it again.

### 7. Google OAuth callback over HTTPS

In **Google Cloud Console → APIs & Services → Credentials**, edit the OAuth
web client and add this authorized redirect URI:

```
https://<function-url-host>/auth/google/callback
```

It must match `PUBLIC_BASE_URL` exactly, including `https://`. While the OAuth
consent screen is in **Testing**, only the listed test users can sign in, so
add every demo account there.

### 8. Check the deployment

1. Open `<Function URL>/health` and expect `{"status": "ok"}`.
2. Sign in with Google, select **Explore with sample data**, and confirm
   that the dashboard shows the budget, categories and converted totals.
3. Add an expense and check that the budget's remaining amount changes.
4. Import the sample CSV from **History → Import CSV**: check the preview,
   then confirm.
5. In **Add expenses with AI**, type "Таксі 500 грн і обід 150 грн" or scan a receipt, check the drafts, and save them.
6. Sign in from a private window with a second test user and check that none
   of the first user's data is visible.

The function logs go to CloudWatch under `/aws/lambda/pocket-ledger`.

### Updating

Build a new zip from the merged `main`, upload it under **Code → Upload
from → .zip file**, and run the `migrate` test event again.

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

`tests/test_release_flow.py` drives the whole app through the Lambda Function
URL adapter with real cookies, form posts and base64 multipart uploads:
Google sign-in, wallets, expenses, CSV import, a Gemini draft, budget
recalculation, currency conversion and isolation between two accounts. Only
external services are mocked: Google's token endpoint (the ID token is
signed with a test key and validated normally), Gemini, and the rate feed.

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
