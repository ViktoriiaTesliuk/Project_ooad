"""End-to-end release checks through the Lambda Function URL adapter.

Every request below is a Function URL event handled by
``pocket_ledger.lambda_handler.call_wsgi``, so cookies, form posts,
base64 multipart uploads and redirects take the same path as on AWS. The
database is the local PostgreSQL from ``conftest.pg``. Only the outside world
is mocked: Google's token endpoint (the ID token is signed with a test key and
validated by Authlib as usual), Gemini, and the Monobank rate feed.
"""

import base64
import re
import time
from decimal import Decimal
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from authlib.integrations.requests_client import OAuth2Session
from joserfc import jwt
from joserfc.jwk import RSAKey

from pocket_ledger import fx, gemini
from pocket_ledger.lambda_handler import call_wsgi

PUBLIC_URL = "https://abc123.lambda-url.eu-central-1.on.aws"
CLIENT_ID = "release-test.apps.googleusercontent.com"
SAMPLE = Path(__file__).resolve().parents[1] / "pocket_ledger" / "static" / "sample-transactions.csv"
NNBSP = " "  # thousands separator used by the money filter


class FunctionUrlBrowser:
    """A tiny browser that talks to the app only through Function URL events."""

    def __init__(self, app):
        self.app = app
        self.cookies = {}
        self.last = None

    def request(self, method, url, *, form=None, files=None):
        parts = urlsplit(url)
        headers = {"host": urlsplit(PUBLIC_URL).hostname, "x-forwarded-proto": "https"}
        body, encoded = None, False
        if files:
            boundary = "----pocketledger"
            chunks = []
            for name, value in (form or {}).items():
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            for name, (filename, content) in files.items():
                chunks.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    f"Content-Type: text/csv\r\n\r\n".encode() + content + b"\r\n"
                )
            chunks.append(f"--{boundary}--\r\n".encode())
            body, encoded = base64.b64encode(b"".join(chunks)).decode(), True
            headers["content-type"] = f"multipart/form-data; boundary={boundary}"
        elif form is not None:
            body = urlencode(form)
            headers["content-type"] = "application/x-www-form-urlencoded"
        event = {
            "version": "2.0", "rawPath": parts.path, "rawQueryString": parts.query, "headers": headers,
            "cookies": [f"{k}={v}" for k, v in self.cookies.items()],
            "requestContext": {"domainName": headers["host"],
                               "http": {"method": method, "path": parts.path, "sourceIp": "198.51.100.7"}},
            "isBase64Encoded": encoded,
        }
        if body is not None:
            event["body"] = body
        response = call_wsgi(self.app, event)
        for raw in response["cookies"]:
            for name, morsel in SimpleCookie(raw).items():
                if morsel["max-age"] == "0" or morsel["expires"].startswith("Thu, 01 Jan 1970"):
                    self.cookies.pop(name, None)
                else:
                    assert morsel["secure"] and morsel["httponly"], "session cookie must be Secure and HttpOnly"
                    self.cookies[name] = morsel.value
        if response["isBase64Encoded"]:
            response["text"] = base64.b64decode(response["body"]).decode("utf-8", "replace")
        else:
            response["text"] = response["body"]
        self.last = response
        return response

    def get(self, url):
        return self.request("GET", url)

    def follow(self, response):
        while response["statusCode"] in (301, 302, 303):
            response = self.get(response["headers"]["location"])
        return response

    def csrf(self, page=None):
        return re.search(r'name="csrf_token" value="([^"]+)"', (page or self.last)["text"])[1]

    def post(self, url, data, files=None, *, page=None):
        token = self.csrf(page or self.get(url.split("?")[0] if not files else url))
        return self.request("POST", url, form={"csrf_token": token, **data}, files=files)


@pytest.fixture
def google(app, monkeypatch):
    """Stand in for Google's OpenID provider with a locally generated signing key."""
    key = RSAKey.generate_key(2048, parameters={"kid": "release-test"})
    app.config.update(GOOGLE_CLIENT_ID=CLIENT_ID, GOOGLE_CLIENT_SECRET="placeholder",
                      PUBLIC_BASE_URL=PUBLIC_URL, SESSION_COOKIE_SECURE=True)
    client = app.extensions["pocket_ledger.oauth"].google
    client.client_id, client.client_secret = CLIENT_ID, "placeholder"
    client.server_metadata.update({
        "_loaded_at": time.time(),
        "issuer": "https://accounts.google.com",
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks": {"keys": [key.as_dict(private=False)]},
        "id_token_signing_alg_values_supported": ["RS256"],
    })
    pending = {}

    def fetch_token(session, url, **kwargs):
        assert url == "https://oauth2.googleapis.com/token"
        assert kwargs["code"] == pending["code"] and kwargs.get("code_verifier")
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": pending.get("aud", CLIENT_ID), "sub": pending["sub"],
                  "email": pending["email"], "name": pending["email"].split("@")[0].title(),
                  "iat": now, "exp": now + 600, "nonce": pending["nonce"]}
        id_token = jwt.encode({"alg": "RS256", "kid": "release-test"}, claims, key)
        return {"access_token": "placeholder", "token_type": "Bearer", "expires_in": 600, "id_token": id_token}

    monkeypatch.setattr(OAuth2Session, "fetch_token", fetch_token)

    def sign_in(browser, sub, email, *, tamper=None, expect=302):
        start = browser.get("/auth/google")
        assert start["statusCode"] == 302
        location = urlsplit(start["headers"]["location"])
        params = parse_qs(location.query)
        assert location.netloc == "accounts.google.com"
        assert params["redirect_uri"] == [f"{PUBLIC_URL}/auth/google/callback"]
        pending.clear()
        pending.update({"sub": sub, "email": email, "code": f"code-{sub}", "nonce": params["nonce"][0],
                        **(tamper or {})})
        query = urlencode({"state": params["state"][0], "code": pending["code"]})
        done = browser.get(f"/auth/google/callback?{query}")
        if expect != 302:
            assert done["statusCode"] == expect
            return done
        assert done["statusCode"] == 302 and done["headers"]["location"] == "/dashboard"
        return browser.follow(done)

    return sign_in


@pytest.fixture
def outside_services(app, monkeypatch):
    """Gemini drafts and Monobank rates without network access."""
    app.config["GEMINI_API_KEY"] = "placeholder"

    def gemini_post(url, *, headers, json, timeout):
        assert "gemini" in url and headers["x-goog-api-key"] == "placeholder"
        reply = '{"amount": 12.5, "category": "Food", "description": "Coffee and croissant"}'
        return Mock(raise_for_status=Mock(), json=Mock(return_value={
            "candidates": [{"content": {"parts": [{"text": reply}]}}]}))

    monkeypatch.setattr(gemini.requests, "post", gemini_post)
    monkeypatch.setattr(fx, "_feed", lambda url: [
        {"currencyCodeA": 978, "currencyCodeB": 980, "rateBuy": 44.0, "rateSell": 45.0},
        {"currencyCodeA": 840, "currencyCodeB": 980, "rateBuy": 41.0, "rateSell": 41.5},
    ] if url == fx.MONOBANK_URL else [])


def count(pg, sql, *params):
    return pg.execute(sql, params).fetchone()[0]


def test_release_flow_through_function_url(pg, app, google, outside_services):
    alice = FunctionUrlBrowser(app)
    assert "Sign in with Google" in alice.get("/")["text"]
    welcome = google(alice, "google-alice", "alice@example.test")
    assert "Welcome to Pocket Ledger" in welcome["text"]

    # Wallets: one in UAH for spending, one in EUR for the Gemini draft.
    for name, currency, opening in (("Salary card", "UAH", "1000"), ("Euro cash", "EUR", "100")):
        created = alice.post("/wallets", {"name": name, "currency": currency, "opening_balance": opening})
        assert created["statusCode"] == 302
    card = count(pg, "SELECT id FROM wallets WHERE name = 'Salary card'")
    euro = count(pg, "SELECT id FROM wallets WHERE name = 'Euro cash'")
    food = count(pg, "SELECT id FROM categories WHERE name = 'Food' AND kind = 'expense'")

    # Budget and a manual expense.
    assert alice.post("/budget?month=2026-11", {"amount": "2000", "currency": "UAH"})["statusCode"] == 302
    added = alice.post("/transactions/new", {
        "kind": "expense", "wallet_id": card, "category_id": food, "amount": "300",
        "occurred_on": "2026-11-10", "description": "Market",
    })
    assert added["statusCode"] == 302

    # CSV import: multipart upload in a base64 body, preview, then confirmation.
    preview = alice.post("/transactions/import", {"wallet_id": card},
                         files={"csv_file": ("sample.csv", SAMPLE.read_bytes())}, page=alice.get("/transactions/import"))
    assert preview["statusCode"] == 200
    assert "Rows to import" in preview["text"] and "Confirm import" in preview["text"]
    assert count(pg, "SELECT COUNT(*) FROM transactions") == 1  # nothing saved by the preview
    token = re.search(r'name="token" value="([^"]+)"', preview["text"])[1]
    confirmed = alice.request("POST", "/transactions/import/confirm",
                              form={"csrf_token": alice.csrf(preview), "token": token})
    assert confirmed["statusCode"] == 302
    history = alice.follow(confirmed)["text"]
    assert "Imported 3 transactions." in history
    assert all(text in history for text in ("Lunch", "Bus", "Gift", "Market"))

    # Gemini draft: suggested, reviewed (wallet changed to EUR), then saved.
    form_page = alice.get("/transactions/new")
    draft = alice.request("POST", "/transactions/suggest", form={
        "csrf_token": alice.csrf(form_page), "ai_text": "12.5 for coffee and a croissant"})
    assert draft["statusCode"] == 200 and "Review the draft" in draft["text"]
    assert 'value="12.50"' in draft["text"] and 'value="Coffee and croissant"' in draft["text"]
    assert count(pg, "SELECT COUNT(*) FROM transactions") == 4  # the draft is not saved
    saved = alice.request("POST", "/transactions/new", form={
        "csrf_token": alice.csrf(draft), "kind": "expense", "wallet_id": euro, "category_id": food,
        "amount": "12.50", "occurred_on": "2026-11-15", "description": "Coffee and croissant"})
    assert saved["statusCode"] == 302
    assert count(pg, "SELECT currency FROM transactions WHERE description = 'Coffee and croissant'") == "EUR"

    # Dashboard in UAH: 300 + 185 + 25.50 UAH, plus 12.50 EUR × 44 (Monobank buy rate) = 1 060.50.
    dashboard = alice.get("/dashboard?month=2026-11&currency=UAH")["text"]
    assert f"1{NNBSP}060.50" in dashboard and "500.00" in dashboard  # expenses, income
    assert "939.50 UAH" in dashboard and "53.0% used" in dashboard  # remaining of 2 000
    assert "Monobank" in dashboard and "Totals are incomplete" not in dashboard

    # Editing an expense recalculates the budget.
    market = count(pg, "SELECT id FROM transactions WHERE description = 'Market'")
    edit_page = alice.get(f"/transactions/{market}/edit")
    assert "Delete this transaction" in edit_page["text"]
    alice.request("POST", f"/transactions/{market}/edit", form={
        "csrf_token": alice.csrf(edit_page), "kind": "expense", "wallet_id": card, "category_id": food,
        "amount": "1500", "occurred_on": "2026-11-10", "description": "Market"})
    dashboard = alice.get("/dashboard?month=2026-11&currency=UAH")["text"]
    assert "Over budget" in dashboard and "260.50 UAH" in dashboard
    euro_view = alice.get("/dashboard?month=2026-11&currency=EUR")["text"]
    assert "Show in EUR" in euro_view and "Totals are incomplete" not in euro_view

    # A second account sees none of it and cannot touch it.
    bob = FunctionUrlBrowser(app)
    assert "Welcome to Pocket Ledger" in google(bob, "google-bob", "bob@example.test")["text"]
    assert "Market" not in bob.get("/transactions")["text"]
    assert bob.get(f"/transactions/{market}/edit")["statusCode"] == 404
    assert bob.post(f"/transactions/{market}/delete", {}, page=bob.get("/wallets"))["statusCode"] == 404
    assert bob.get(f"/wallets/{card}/edit")["statusCode"] == 404
    replay = bob.request("POST", "/transactions/import/confirm", form={"csrf_token": bob.csrf(bob.get("/wallets")),
                                                                       "token": token})
    assert replay["statusCode"] == 404
    assert count(pg, "SELECT amount FROM transactions WHERE id = %s", market) == Decimal("1500.00")

    # Deleting needs the confirmation form; then signing out ends the session.
    page = alice.get(f"/transactions/{market}/edit")
    deleted = alice.request("POST", f"/transactions/{market}/delete", form={"csrf_token": alice.csrf(page)})
    assert deleted["statusCode"] == 302 and "Transaction deleted." in alice.follow(deleted)["text"]
    signed_out = alice.request("POST", "/auth/logout", form={"csrf_token": alice.csrf(alice.get("/wallets"))})
    assert signed_out["statusCode"] == 302
    assert alice.get("/dashboard")["statusCode"] == 302


def test_sign_in_rejects_a_forged_state(pg, app, google):
    browser = FunctionUrlBrowser(app)
    browser.get("/auth/google")
    assert browser.get("/auth/google/callback?state=forged&code=x")["statusCode"] == 400


@pytest.mark.parametrize("tamper", [{"nonce": "replayed"}, {"aud": "someone-else.apps.googleusercontent.com"}])
def test_sign_in_rejects_invalid_id_tokens(pg, app, google, tamper):
    browser = FunctionUrlBrowser(app)
    google(browser, "google-mallory", "mallory@example.test", tamper=tamper, expect=400)
    assert count(pg, "SELECT COUNT(*) FROM users") == 0
    assert browser.get("/dashboard")["statusCode"] == 302


def test_demo_data_only_fills_an_empty_account(pg, app, google):
    browser = FunctionUrlBrowser(app)
    google(browser, "google-demo", "demo@example.test")
    loaded = browser.post("/demo", {}, page=browser.get("/dashboard"))
    assert browser.follow(loaded)["statusCode"] == 200
    assert "Added 3 sample wallets" in browser.last["text"]
    assert "Everyday card" in browser.last["text"] and "Spending by category" in browser.last["text"]
    transactions = count(pg, "SELECT COUNT(*) FROM transactions")
    assert transactions >= 3
    again = browser.request("POST", "/demo", form={"csrf_token": browser.csrf()})
    assert "only be added to an account without wallets" in browser.follow(again)["text"]
    assert count(pg, "SELECT COUNT(*) FROM transactions") == transactions


def test_seed_demo_cli(pg, app, google):
    google(FunctionUrlBrowser(app), "google-cli", "cli@example.test")
    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed-demo", "CLI@example.test"])
    assert result.exit_code == 0, result.output
    assert "Added 3 wallets" in result.output
    assert runner.invoke(args=["seed-demo", "cli@example.test"]).exit_code != 0
    assert runner.invoke(args=["seed-demo", "nobody@example.test"]).exit_code != 0
