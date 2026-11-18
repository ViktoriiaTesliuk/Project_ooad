"""Request and validate editable expense fields from Gemini."""

import json
from decimal import Decimal

import requests
from flask import current_app

from . import ledger

MODEL = "gemini-3.1-flash-lite"
MAX_TEXT = 500


class DraftError(ValueError):
    pass


def suggest(text, categories):
    key = current_app.config["GEMINI_API_KEY"]
    if not key:
        raise DraftError("AI assistance is unavailable. You can still enter the expense manually.")
    if not text or len(text) > MAX_TEXT:
        raise DraftError("Enter an expense description of 1 to 500 characters.")
    if not categories:
        raise DraftError("An expense category is needed before drafting an expense.")

    names = list(categories)
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            headers={"x-goog-api-key": key},
            json={
                "systemInstruction": {"parts": [{"text": (
                    "Extract an expense amount, category and short description from the input. "
                    "Use null when no clear positive amount or matching category exists."
                )}]},
                "contents": [{"parts": [{"text": json.dumps(
                    {"text": text, "categories": names}, ensure_ascii=False
                )}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": ["number", "null"]},
                            "category": {"type": ["string", "null"]},
                            "description": {"type": "string"},
                        },
                        "required": ["amount", "category", "description"],
                    },
                    "maxOutputTokens": 640,
                },
            },
            timeout=(2, 8),
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise DraftError("AI assistance is temporarily unavailable. You can still enter the expense manually.") from error

    try:
        candidate = response.json()["candidates"][0]
        parts = candidate["content"]["parts"]
        data = json.loads(
            "".join(part.get("text", "") for part in parts if not part.get("thought")),
            parse_float=Decimal,
        )
        amount, category, description = data["amount"], data["category"], data["description"]
        if (category not in categories or not isinstance(description, str)
                or len(description) > ledger.MAX_DESCRIPTION or "\x00" in description):
            raise ValueError("Invalid draft fields")
        if not isinstance(amount, (int, Decimal)) or isinstance(amount, bool):
            raise ValueError("Invalid draft amount")
        amount = ledger.parse_amount(amount)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise DraftError("The assistant could not prepare a complete expense. You can enter it manually.") from error
    return {"kind": "expense", "amount": f"{amount:.2f}",
            "category_id": categories[category], "description": description.strip()}
