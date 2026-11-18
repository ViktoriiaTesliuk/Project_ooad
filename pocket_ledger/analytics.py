"""Read-only dashboard analytics derived from the same dated expense records."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from . import db, fx, ledger

ZERO = Decimal(0)


def _period_rows(user_id, start, selected):
    return db.rows(db.execute(
        """
        SELECT CAST(CASE WHEN t.occurred_on >= :selected THEN t.occurred_on
                         ELSE CAST(date_trunc('month', t.occurred_on) AS DATE) END AS TEXT),
               t.currency, t.kind,
               CASE WHEN t.occurred_on >= :selected AND t.kind = 'expense' THEN c.id END,
               CASE WHEN t.occurred_on >= :selected AND t.kind = 'expense' THEN c.name END,
               CAST(SUM(t.amount) AS TEXT)
        FROM transactions AS t
        JOIN wallets AS w ON w.id = t.wallet_id
        JOIN categories AS c ON c.id = t.category_id
        WHERE w.user_id = :user_id AND t.kind IN ('income', 'expense')
          AND t.occurred_on >= :start AND t.occurred_on < :end
        GROUP BY 1, 2, 3, 4, 5
        """,
        {"user_id": user_id, "start": start, "selected": selected,
         "end": ledger.next_month(selected)},
    ))


def load_dashboard(user_id, selected, currency):
    months = [selected]
    for _ in range(5):
        months.append(ledger.previous_month(months[-1]))
    months.reverse()
    rows = _period_rows(user_id, months[0], selected)
    budget = ledger.get_budget(user_id, selected)
    rates = fx.load_rates({*ledger.CURRENCIES, *(row[1] for row in rows)})
    return (*summarize_dashboard(selected, months, rows, budget, currency, rates), rates)


def summarize_dashboard(selected, months, rows, budget, currency, rates):
    monthly = {month: {"income": ZERO, "expense": ZERO} for month in months}
    daily = {selected.replace(day=day): ZERO for day in range(1, (
        ledger.next_month(selected) - selected
    ).days + 1)}
    categories = defaultdict(Decimal)
    missing = defaultdict(set)
    for day_text, source, kind, category_id, name, amount in rows:
        day = date.fromisoformat(day_text)
        month = day.replace(day=1)
        value = fx.convert(Decimal(amount), source, currency, rates)
        if value is None:
            missing[month].add(source)
            continue
        monthly[month][kind] += value
        if month == selected and kind == "expense":
            daily[day] += value
            categories[category_id, name] += value

    missing_budget = False
    if budget:
        amount = fx.convert(budget.amount, budget.currency, currency, rates)
        missing_budget = amount is None
        budget = ledger.Budget(budget.month_start, amount, currency) if amount is not None else None
    current = monthly[selected]
    totals = [(currency, kind, amount) for kind, amount in current.items() if amount]
    summary = ledger.summarize(
        selected, totals,
        [(currency, category_id, name, value) for (category_id, name), value in categories.items()],
        budget,
    )
    daily_points = [(day.strftime("%d %b %Y"), str(day.day), {"expense": amount})
                    for day, amount in daily.items()]
    monthly_points = [(month.strftime("%B %Y"), month.strftime("%b"), totals)
                      for month, totals in monthly.items()]
    return (summary, sorted(missing[selected]), missing_budget, bar_chart(daily_points),
            bar_chart(monthly_points), sorted(set().union(*missing.values())))


def _compact(value):
    for divisor, suffix in ((10**15, "q"), (10**12, "t"), (10**9, "b"), (10**6, "M"), (10**3, "k")):
        if value >= divisor:
            return f"{(value / divisor).normalize():f}{suffix}"
    return f"{value.normalize():f}"


def bar_chart(points):
    peak = max((max(values.values()) for _, _, values in points), default=ZERO)
    if not peak:
        return {"points": [(title, label, [(kind, value, 0) for kind, value in values.items()])
                           for title, label, values in points], "ticks": []}
    target = peak / 4
    magnitude = Decimal(10) ** target.adjusted()
    step = next(magnitude * unit for unit in (1, 2, Decimal("2.5"), 5, 10)
                if magnitude * unit >= target)
    ceiling = step * 4
    return {
        "points": [
            (title, label, [(kind, amount, amount / ceiling * 100) for kind, amount in values.items()])
            for title, label, values in points
        ],
        "ticks": [(i * 25, _compact(step * i)) for i in range(5)],
    }
