"""Deterministic cash-flow maths on classified transactions."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .. import config
from ..classify import taxonomy
from ..ingest.schemas import Transaction


@dataclass
class Row:
    """A transaction joined with its classification."""

    id: str
    date: date
    description: str
    counterparty: str
    source_category: str
    amount: float
    balance: float | None
    category: str
    kind: str
    is_foreign: bool

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m")


def enrich(txns: list[Transaction], classes: dict[str, dict]) -> list[Row]:
    rows = []
    for t in txns:
        c = classes[t.id]
        rows.append(
            Row(
                id=t.id, date=t.date, description=t.description, counterparty=t.counterparty,
                source_category=t.source_category, amount=t.amount, balance=t.balance,
                category=c["category"], kind=taxonomy.kind(c["category"]),
                is_foreign=bool(c.get("is_foreign")) or t.is_fx,
            )
        )
    rows.sort(key=lambda r: (r.date, r.id))
    return rows


def window(rows: list[Row]) -> tuple[date, date]:
    """Trailing 365-day analysis window ending on the last transaction date."""
    end = max(r.date for r in rows)
    return end - timedelta(days=364), end


def in_window(rows: Iterable[Row], start: date, end: date) -> list[Row]:
    return [r for r in rows if start <= r.date <= end]


def _months_between(first: date, last: date) -> list[str]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _is_complete(month: str, first: date, last: date) -> bool:
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start >= first and nxt - timedelta(days=1) <= last


def monthly(rows: list[Row]) -> list[dict]:
    first, last = rows[0].date, rows[-1].date
    by_month: dict[str, dict] = {
        mth: {"month": mth, "income": 0.0, "spending": 0.0, "saving": 0.0, "by_category": defaultdict(float),
              "income_by_category": defaultdict(float), "complete": _is_complete(mth, first, last)}
        for mth in _months_between(first, last)
    }
    for r in rows:
        b = by_month[r.month]
        if r.kind == "income":
            b["income"] += r.amount
            b["income_by_category"][r.category] += r.amount
        elif r.kind == "expense":
            b["spending"] += -r.amount
            b["by_category"][r.category] += -r.amount
        elif r.kind == "saving":
            b["saving"] += -r.amount
    out = []
    for b in by_month.values():
        b["by_category"] = {k: round(v, 2) for k, v in sorted(b["by_category"].items(), key=lambda kv: -kv[1])}
        b["income_by_category"] = {k: round(v, 2) for k, v in b["income_by_category"].items()}
        b["surplus"] = round(b["income"] - b["spending"], 2)
        b["free_cash_flow"] = round(b["income"] - b["spending"] - b["saving"], 2)
        b["savings_rate"] = round(b["surplus"] / b["income"], 4) if b["income"] > 0 else None
        for k in ("income", "spending", "saving"):
            b[k] = round(b[k], 2)
        out.append(b)
    return out


def kpis(rows: list[Row], months: list[dict]) -> dict:
    s = config.settings()["signals"]
    start, end = window(rows)
    w = in_window(rows, start, end)
    income = sum(r.amount for r in w if r.kind == "income")
    spending = -sum(r.amount for r in w if r.kind == "expense")
    saving = -sum(r.amount for r in w if r.kind == "saving")
    complete = [m for m in months if m["complete"]]
    trailing = complete[-int(s["trailing_months"]):] or months
    t_inc = sum(m["income"] for m in trailing)
    t_sp = sum(m["spending"] for m in trailing)
    t_sav = sum(m["saving"] for m in trailing)
    n = len(trailing)
    balance = next((r.balance for r in reversed(rows) if r.balance is not None), None)
    monthly_spending = spending / 12
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "annual_income": round(income, 2),
        "annual_spending": round(spending, 2),
        "annual_saving": round(saving, 2),
        "annual_surplus": round(income - spending, 2),
        "annual_free_cash_flow": round(income - spending - saving, 2),
        "monthly_income": round(income / 12, 2),
        "monthly_spending": round(monthly_spending, 2),
        "trailing_months": [m["month"] for m in trailing],
        "trailing_monthly_surplus": round((t_inc - t_sp) / n, 2),
        "trailing_monthly_free_cash_flow": round((t_inc - t_sp - t_sav) / n, 2),
        "trailing_savings_rate": round((t_inc - t_sp) / t_inc, 4) if t_inc > 0 else None,
        "savings_rate_12m": round((income - spending) / income, 4) if income > 0 else None,
        "balance": round(balance, 2) if balance is not None else None,
        "reserve_months": round(balance / monthly_spending, 1) if balance is not None and monthly_spending > 0 else None,
    }


# ------------------------------------------------------------ recurring flows

CYCLES = (("weekly", 7), ("biweekly", 14), ("monthly", 30.4), ("quarterly", 91.3))


def _cadence_by_interval(dates: list[date], tol: float) -> str | None:
    iv = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not iv:
        return None
    for name, days in CYCLES:
        tol_c = tol if days <= 31 else tol * 3
        hits = sum(1 for d in iv if abs(d - days) <= tol_c)
        if hits / len(iv) >= 0.8:
            return name
    return None


def _cadence_by_calendar(rows: list[Row], first: date, last: date) -> str | None:
    months = [m for m in _months_between(first, last) if _is_complete(m, first, last)]
    if len(months) < 3:
        return None
    counts = defaultdict(int)
    for r in rows:
        counts[r.month] += 1
    covered = sum(1 for m in months if counts.get(m, 0) >= 1)
    avg_per_month = sum(counts.get(m, 0) for m in months) / len(months)
    if covered / len(months) >= 0.8 and 0.8 <= avg_per_month <= 1.5:
        return "monthly"
    return None


def recurring_flows(rows: list[Row]) -> list[dict]:
    s = config.settings()["signals"]["recurring"]
    first, last = rows[0].date, rows[-1].date
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        if r.kind == "transfer":
            continue
        groups[(r.counterparty, "in" if r.amount > 0 else "out")].append(r)
    flows = []
    for (cp, direction), g in groups.items():
        if len(g) < int(s["min_occurrences"]):
            continue
        dates = [r.date for r in g]
        amounts = [abs(r.amount) for r in g]
        mean = statistics.fmean(amounts)
        cv = statistics.pstdev(amounts) / mean if mean else 0.0
        cadence = _cadence_by_interval(dates, float(s["interval_tolerance_days"]))
        method = "interval"
        if cadence is None:
            cadence, method = _cadence_by_calendar(g, first, last), "calendar"
        if cadence is None:
            continue
        per_year = {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4}[cadence]
        flows.append(
            {
                "counterparty": cp,
                "direction": direction,
                "category": statistics.mode(r.category for r in g),
                "cadence": cadence,
                "detected_by": method,
                "count": len(g),
                "avg_amount": round(mean, 2),
                "amount_cv": round(cv, 3),
                "stable_amount": cv <= float(s["max_amount_cv"]),
                "annualised": round(mean * per_year, 2),
                "first_date": dates[0].isoformat(),
                "last_date": dates[-1].isoformat(),
                "evidence": [r.id for r in g],
            }
        )
    flows.sort(key=lambda f: (f["direction"] != "in", -f["annualised"]))
    return flows
