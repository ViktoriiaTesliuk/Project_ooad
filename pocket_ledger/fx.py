"""Convert dashboard amounts through UAH using the latest available public rates."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests

from . import db, ledger

MONOBANK_URL = "https://api.monobank.ua/bank/currency"
NBU_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchangeNew?json"
ISO_CODES = {840: "USD", 978: "EUR", 985: "PLN", 826: "GBP"}
CACHE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Rate:
    currency: str
    value: Decimal  # UAH per unit at the buy rate
    source: str
    fetched_at: datetime
    cached: bool = False
    sell_value: Decimal | None = None


def _positive(value):
    try:
        value = Decimal(str(value))
    except InvalidOperation:
        return None
    return value if value.is_finite() and Decimal("0.000000000001") <= value < 10**12 else None


def _feed(url):
    try:
        response = requests.get(url, timeout=(2, 3))
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _monobank():
    rates = {}
    for item in _feed(MONOBANK_URL):
        code = item.get("currencyCodeA")
        if type(code) is not int or code not in ISO_CODES or item.get("currencyCodeB") != 980:
            continue
        buy, sell = _positive(item.get("rateBuy")), _positive(item.get("rateSell"))
        cross = _positive(item.get("rateCross"))
        if buy and sell:
            rates[ISO_CODES[code]] = (buy, sell)
        elif cross:
            rates[ISO_CODES[code]] = (cross, cross)
    return rates


def _nbu():
    rates = {}
    for item in _feed(NBU_URL):
        code = item.get("cc")
        value = _positive(item.get("rate"))
        if code in ISO_CODES.values() and value:
            rates[code] = (value, value)
    return rates


def load_rates(currencies):
    needed = set(currencies) - {"UAH"}
    if not needed:
        return {}
    now = datetime.now(timezone.utc)
    cache = {
        code: Rate(code, Decimal(value), source, datetime.fromisoformat(timestamp),
                   cached=True, sell_value=Decimal(sell) if sell is not None else None)
        for code, value, source, timestamp, sell in db.rows(db.execute(
            """
            SELECT base_currency, CAST(rate AS TEXT), source, CAST(fetched_at AS TEXT),
                   CAST(sell_rate AS TEXT)
            FROM exchange_rates WHERE quote_currency = 'UAH'
            """
        ))
        if code in needed
    }
    refresh = {code for code in needed if code not in cache or now - cache[code].fetched_at >= CACHE_TTL}
    if not refresh:
        return cache

    primary = _monobank()
    fallback = _nbu() if refresh - primary.keys() else {}
    for code in sorted(refresh):
        pair = primary.get(code) or fallback.get(code)
        if pair is None:
            continue
        buy, sell = pair
        source = "Monobank" if code in primary else "NBU"
        db.execute(
            """
            INSERT INTO exchange_rates (base_currency, quote_currency, rate, sell_rate, source)
            VALUES (:currency, 'UAH', :rate, :sell, :source)
            ON CONFLICT (base_currency, quote_currency) DO UPDATE
            SET rate = EXCLUDED.rate, sell_rate = EXCLUDED.sell_rate,
                source = EXCLUDED.source, fetched_at = EXCLUDED.fetched_at
            """,
            {"currency": code, "rate": buy, "sell": sell, "source": source},
        )
        cache[code] = Rate(code, buy, source, now, sell_value=sell)
    return cache


def convert(amount, source, target, rates):
    if source == target:
        return amount
    base = rates[source].value if source in rates else (Decimal(1) if source == "UAH" else None)
    target_rate = rates.get(target)
    if target_rate is not None:
        quote = target_rate.sell_value or target_rate.value
    else:
        quote = Decimal(1) if target == "UAH" else None
    return amount * base / quote if base is not None and quote is not None else None


def dashboard_summary(summary, currency, rates):
    categories = {}
    totals = []
    missing = set()
    for item in summary.currencies:
        factor = convert(Decimal(1), item.currency, currency, rates)
        if factor is None:
            missing.add(item.currency)
            continue
        totals.extend(((currency, "income", item.income * factor),
                       (currency, "expense", item.expenses * factor)))
        for category in item.categories:
            key = (category.category_id, category.name)
            categories[key] = categories.get(key, Decimal(0)) + category.amount * factor

    budget = summary.budget.budget if summary.budget else None
    unconverted_budget = False
    if budget is not None:
        amount = convert(budget.amount, budget.currency, currency, rates)
        unconverted_budget = amount is None
        budget = ledger.Budget(budget.month_start, amount, currency) if amount is not None else None
    converted = ledger.summarize(
        summary.month_start, totals,
        [(currency, category_id, name, amount) for (category_id, name), amount in categories.items()],
        budget,
    )
    return converted, sorted(missing), unconverted_budget
