"""Minimal Gemini API client used by AI expense entry.

The API key stays on the server (``GEMINI_API_KEY``); browsers never see it.
Responses are constrained to a JSON schema and decoded with ``Decimal`` so
money never passes through binary floats.
"""

import json
from decimal import Decimal

import requests
from flask import current_app

MODEL = "gemini-3.1-flash-lite"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


class DraftError(ValueError):
    """Gemini is unavailable or returned something unusable. The message is safe to show."""


def is_configured():
    return bool(current_app.config.get("GEMINI_API_KEY"))


def generate(parts, *, system, schema, timeout=(3, 20), max_output_tokens=2048):
    """Send ``parts`` (text and/or inline images) and return the JSON object Gemini produced."""
    key = current_app.config.get("GEMINI_API_KEY")
    if not key:
        raise DraftError("AI assistance is unavailable. You can still enter the expense manually.")
    try:
        response = requests.post(
            ENDPOINT,
            headers={"x-goog-api-key": key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                    "temperature": 0,
                    "maxOutputTokens": max_output_tokens,
                },
            },
            timeout=timeout,
        )
        if response.status_code == 429:
            raise DraftError("The free AI quota is busy right now. Try again in a minute or enter the expense manually.")
        response.raise_for_status()
    except requests.RequestException as error:
        current_app.logger.warning("Gemini request failed: %s", type(error).__name__)
        raise DraftError("AI assistance is temporarily unavailable. You can still enter the expense manually.") from error

    try:
        candidate = response.json()["candidates"][0]
        text = "".join(part.get("text", "") for part in candidate["content"]["parts"] if not part.get("thought"))
        data = json.loads(text, parse_float=Decimal)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise DraftError("The assistant could not read that. Try rephrasing, or enter the expense manually.") from error
    if not isinstance(data, dict):
        raise DraftError("The assistant could not read that. Try rephrasing, or enter the expense manually.")
    return data
