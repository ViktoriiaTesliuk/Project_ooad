"""AI expense entry: one sentence or a receipt photo -> review -> one-click save.

Gemini is replaced at the HTTP boundary; everything else (SQL, validation,
review page, duplicate checks, the daily limit) runs for real.
"""

import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import Mock

import pytest
import requests

from pocket_ledger import ai_expenses, gemini, ledger

JPEG = b"\xff\xd8\xff\xe0" + b"receipt-photo-1" * 20
JPEG_RESHOT = b"\xff\xd8\xff\xe0" + b"same-receipt-other-photo" * 20
TODAY = ai_expenses.today_kyiv()


def expense(**fields):
    base = {"amount": None, "currency": None, "date": None, "merchant": None, "description": None,
            "category": None, "items": [], "uncertain": []}
    return dict(base, **fields)


class FakeGemini:
    def __init__(self, monkeypatch):
        self.calls = []
        self.replies = []
        monkeypatch.setattr(gemini.requests, "post", self)

    def reply(self, *expenses):
        self.replies.append({"expenses": list(expenses)})

    def __call__(self, url, *, headers, json, timeout):
        assert url == gemini.ENDPOINT and headers == {"x-goog-api-key": "placeholder"}
        self.calls.append(json)
        body = self.replies.pop(0)
        if isinstance(body, Exception):
            raise body
        text = __import__("json").dumps(body)
        return Mock(status_code=200, raise_for_status=Mock(),
                    json=Mock(return_value={"candidates": [{"content": {"parts": [{"text": text}]}}]}))


class FormValues(HTMLParser):
    """Collect what a browser would submit from the review form."""

    def __init__(self, html):
        super().__init__()
        self.values, self._select = {}, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name"):
            if a.get("type") == "checkbox":
                if "checked" in a:
                    self.values[a["name"]] = "on"
            else:
                self.values[a["name"]] = a.get("value", "")
        elif tag == "select":
            self._select = a.get("name")
        # Browsers submit the first option unless another one is selected.
        elif tag == "option" and self._select and ("selected" in a or self._select not in self.values):
            self.values[self._select] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


@pytest.fixture
def ai(app, monkeypatch):
    app.config["GEMINI_API_KEY"] = "placeholder"
    return FakeGemini(monkeypatch)


@pytest.fixture
def user(pg, app, make_user, login):
    user_id = make_user()
    client = login(user_id)
    with app.app_context():
        card = ledger.create_wallet(user_id, ledger.WalletDraft("Card", "UAH", Decimal(0)))
        euro = ledger.create_wallet(user_id, ledger.WalletDraft("Euro cash", "EUR", Decimal(0)))
        cash = ledger.create_wallet(user_id, ledger.WalletDraft("Cash", "UAH", Decimal(0)))
        food = category(pg, "Food")
        # The card is the most used UAH wallet, so UAH expenses default to it.
        ledger.create_transaction(user_id, ledger.TransactionDraft(card, food, "expense", Decimal(1), TODAY))
    client.ids = {"user": user_id, "card": card, "euro": euro, "cash": cash}
    return client


def category(pg, name):
    return pg.execute("SELECT id FROM categories WHERE name = %s AND kind = 'expense'", (name,)).fetchone()[0]


def recognise(client, text="", receipt=None):
    data = {"csrf_token": client.csrf, "ai_text": text, "back": "dashboard"}
    if receipt is not None:
        data["receipt"] = (io.BytesIO(receipt), "receipt.jpg")
    return client.post("/ai/expenses", data=data, content_type="multipart/form-data")


def review_form(client, response):
    assert response.status_code == 302 and response.headers["Location"].startswith("/ai/expenses/")
    page = client.get(response.headers["Location"])
    return response.headers["Location"], page, FormValues(page.text).values


def count(pg):
    return pg.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


# --------------------------------------------------------------------------- text


def test_one_sentence_with_two_expenses(pg, user, ai):
    ai.reply(expense(amount=500, currency="UAH", description="Taxi", category="Transport"),
             expense(amount=150, currency="UAH", description="Lunch", category="Cafes & Restaurants"))
    url, page, values = review_form(user, recognise(user, "Paid 500 UAH for a taxi and 150 UAH for lunch"))

    sent = ai.calls[0]
    assert json.loads(sent["contents"][0]["parts"][0]["text"]) == {"message": "Paid 500 UAH for a taxi and 150 UAH for lunch"}
    system = sent["systemInstruction"]["parts"][0]["text"]
    assert f"Today is {TODAY.isoformat()}" in system and "Cafes & Restaurants" in system and "Salary" not in system
    assert sent["generationConfig"]["responseJsonSchema"] == ai_expenses.SCHEMA

    assert "Check these 2 expenses" in page.text and "Save 2 expenses" in page.text
    assert values["r0-amount"] == "500.00" and values["r1-amount"] == "150.00"
    assert values["r0-wallet_id"] == str(user.ids["card"]) and values["r0-occurred_on"] == TODAY.isoformat()
    assert values["r1-category_id"] == str(category(pg, "Cafes & Restaurants"))
    assert "check-badge\">Check</span>" not in page.text.split("Save 2")[0].split("please confirm them")[1]
    assert count(pg) == 1  # nothing saved yet

    saved = user.post(url, data={**values, "action": "save"})
    assert saved.status_code == 302 and saved.headers["Location"].startswith("/dashboard?month=")
    dashboard = user.get(saved.headers["Location"]).text
    assert "Saved 2 expenses (650.00 UAH)." in dashboard and "Transport" in dashboard
    assert count(pg) == 3
    again = user.post(url, data={**values, "action": "save"})
    assert "already saved" in user.get(again.headers["Location"]).text
    assert count(pg) == 3


def test_ukrainian_yesterday_without_currency_is_flagged(pg, user, ai):
    yesterday = TODAY - timedelta(days=1)
    ai.reply(expense(amount=300, date=yesterday.isoformat(), description="Вечеря", category="Cafes & Restaurants"))
    _url, page, values = review_form(user, recognise(user, "Вчора витратила 300 на вечерю"))
    assert values["r0-occurred_on"] == yesterday.isoformat()
    assert values["r0-wallet_id"] == str(user.ids["card"])  # most used wallet when no currency is given
    assert page.text.count('class="check-badge">Check</span>') >= 2  # legend + wallet/currency


def test_uncertain_and_invalid_model_values_are_not_invented(pg, user, ai):
    ai.reply(expense(amount=-40, currency="CZK", date="2031-01-01", description="Something",
                     category="Crypto", uncertain=["merchant", "bogus"]))
    _url, page, values = review_form(user, recognise(user, "something weird"))
    assert values["r0-amount"] == ""  # a negative amount is dropped, not "fixed"
    assert values["r0-category_id"] == str(category(pg, "Other"))
    assert "CZK isn’t supported" in page.text
    assert page.text.count('class="check-badge">Check</span>') >= 4  # amount, wallet/currency, category, date


def test_review_errors_keep_everything_unsaved(pg, user, ai):
    ai.reply(expense(amount=90, currency="UAH", description="Coffee", category="Cafes & Restaurants"))
    url, _page, values = review_form(user, recognise(user, "Bought coffee for 90 UAH"))
    broken = user.post(url, data={**values, "r0-amount": "", "action": "save"})
    assert broken.status_code == 422 and "Enter an amount." in broken.text
    none_selected = user.post(url, data={k: v for k, v in values.items() if k != "r0-include"} | {"action": "save"})
    assert none_selected.status_code == 422 and "Select at least one expense" in none_selected.text
    assert count(pg) == 1


def test_discard_and_other_accounts(pg, user, ai, make_user, login):
    ai.reply(expense(amount=90, currency="UAH", description="Coffee", category="Food"))
    url, _page, values = review_form(user, recognise(user, "coffee 90 uah"))
    stranger = login(make_user())
    assert stranger.get(url).status_code == 404
    assert stranger.post(url, data={**values, "csrf_token": stranger.csrf, "action": "save"}).status_code == 404
    assert user.post(url, data={"csrf_token": user.csrf, "action": "discard"}).status_code == 302
    assert user.get(url).status_code == 404
    assert count(pg) == 1


def test_gemini_failure_keeps_the_text_and_manual_entry(pg, user, ai):
    ai.replies.append(requests.Timeout())
    failed = recognise(user, "taxi 120")
    assert failed.status_code == 302 and failed.headers["Location"] == "/dashboard"
    page = user.get("/dashboard").text
    assert "temporarily unavailable" in page and ">taxi 120</textarea>" in page
    assert count(pg) == 1


def test_daily_limit(pg, app, user, ai):
    app.config["AI_DAILY_LIMIT"] = 2
    for _ in range(2):
        ai.reply(expense(amount=10, currency="UAH", description="Bus", category="Transport"))
        assert recognise(user, "bus 10 uah").headers["Location"].startswith("/ai/expenses/")
    blocked = recognise(user, "bus 10 uah")
    assert len(ai.calls) == 2
    page = user.get(blocked.headers["Location"]).text
    assert "used all 2 AI requests for today" in page and "0 of 2 AI requests" not in page.split("used all")[0]


def test_box_is_hidden_without_a_key(pg, app, user):
    app.config["GEMINI_API_KEY"] = None
    assert "Add expenses with AI" not in user.get("/dashboard").text
    assert recognise(user, "coffee 90").headers["Location"] == "/dashboard"


# --------------------------------------------------------------------------- receipts


def test_receipt_is_read_once_and_never_saved_twice(pg, user, ai):
    receipt = expense(amount=412.3, currency="UAH", date=TODAY.isoformat(), merchant="Silpo",
                      description="Silpo", category="Food", items=["Milk", "Bread", "Cheese"])
    ai.reply(receipt)
    url, page, values = review_form(user, recognise(user, receipt=JPEG))
    image = ai.calls[0]["contents"][0]["parts"][0]["inlineData"]
    assert image["mimeType"] == "image/jpeg" and image["data"]
    assert "Check the receipt" in page.text and "3 items on the receipt" in page.text and "Cheese" in page.text
    assert values["r0-description"] == "Silpo: Milk, Bread, Cheese" and values["r0-amount"] == "412.30"

    # Uploading the same file again before saving reuses the draft without another AI call.
    assert recognise(user, receipt=JPEG).headers["Location"] == url
    user.post(url, data={**values, "action": "save"})
    assert count(pg) == 2

    # The same file after saving: no AI call, a link to the saved expense instead.
    again = recognise(user, receipt=JPEG)
    assert len(ai.calls) == 1 and "/transactions/" in again.headers["Location"]
    assert "already saved this receipt" in user.get(again.headers["Location"]).text

    # Another photo of the same receipt: recognised, but unselected with a warning.
    ai.reply(receipt)
    _url, page, values = review_form(user, recognise(user, receipt=JPEG_RESHOT))
    assert "A matching receipt was already saved" in page.text
    assert "r0-include" not in values and "Save 0 expenses" in page.text

    # Deleting the saved expense allows the receipt again.
    record = pg.execute("SELECT id FROM transactions WHERE description LIKE 'Silpo%%'").fetchone()[0]
    user.post(f"/transactions/{record}/delete", data={"csrf_token": user.csrf})
    ai.reply(receipt)
    assert recognise(user, receipt=JPEG).headers["Location"].startswith("/ai/expenses/")


def test_receipt_without_currency_wallet_needs_a_choice(pg, user, ai):
    ai.reply(expense(amount=25, currency="PLN", date=TODAY.isoformat(), merchant="Żabka", category="Food"))
    url, page, values = review_form(user, recognise(user, receipt=JPEG))
    assert "You have no PLN wallet" in page.text and values["r0-wallet_id"] == ""
    rejected = user.post(url, data={**values, "action": "save"})
    assert rejected.status_code == 422 and "Choose one of your wallets" in rejected.text


def test_non_images_and_large_files_are_rejected_without_ai(pg, user, ai):
    not_image = recognise(user, receipt=b"%PDF-1.7 not a photo")
    assert "Upload a JPEG, PNG, WebP or HEIC photo" in user.get(not_image.headers["Location"]).text
    assert recognise(user, receipt=b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)).status_code == 413
    assert ai.calls == []


def test_image_type_detection():
    assert ai_expenses.image_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert ai_expenses.image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert ai_expenses.image_type(b"\x00\x00\x00\x18ftypheic") == "image/heic"
    assert ai_expenses.image_type(b"GIF89a") is None


def test_normalise_rules():
    categories = {"Food": 1, "Other": 4, "Transport": 2}
    today = date(2026, 11, 18)
    plain = ai_expenses.normalise(expense(amount=90, currency="uah", category="food"), categories, today, receipt=False)
    assert plain["amount"] == "90.00" and plain["currency"] == "UAH" and plain["category_id"] == 1
    assert plain["occurred_on"] == "2026-11-18" and plain["uncertain"] == []
    dated_receipt = ai_expenses.normalise(expense(amount=5, currency="EUR", merchant="Shop"), categories, today, receipt=True)
    assert "date" in dated_receipt["uncertain"]  # a receipt without a readable date is flagged
    assert ai_expenses.normalise(expense(), categories, today, receipt=False) is None
    assert re.fullmatch(r"[0-9a-f]{64}", ai_expenses.content_fingerprint("  SILPO ", "2026-11-18", "412.3", "UAH"))
    assert (ai_expenses.content_fingerprint("silpo", "2026-11-18", Decimal("412.30"), "UAH")
            == ai_expenses.content_fingerprint("  SILPO ", "2026-11-18", "412.3", "UAH"))
