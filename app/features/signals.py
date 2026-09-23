"""Deterministic signal detection. Every signal carries the ids of the transactions
that triggered it (evidence), shown in the UI and the advice record."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import timedelta
from typing import Callable

from .. import config
from ..tax import pillar3a
from .cashflow import Row, in_window, window


def _chf(x: float) -> str:
    return f"CHF {x:,.0f}".replace(",", "'")


def signal(type_: str, title: str, summary: str, evidence: list[str], metrics: dict, strength: str = "medium") -> dict:
    return {"type": type_, "title": title, "summary": summary, "evidence": evidence, "metrics": metrics, "strength": strength}


# ----------------------------------------------------------------- detectors

def recurring_income(rows, ctx) -> dict | None:
    flows = [f for f in ctx["flows"] if f["direction"] == "in" and f["detected_by"] == "interval" and f["stable_amount"]]
    if not flows:
        return None
    total = sum(f["annualised"] for f in flows)
    names = ", ".join(f"{f['counterparty']} ({f['cadence']})" for f in flows[:3])
    return signal(
        "recurring_income", "Recurring income",
        f"{len(flows)} regular income stream(s), about {_chf(total)} a year: {names}.",
        [e for f in flows for e in f["evidence"]],
        {"flows": [{k: f[k] for k in ("counterparty", "cadence", "avg_amount", "count", "amount_cv", "annualised")} for f in flows],
         "annualised_total": round(total, 2)},
        "high",
    )


def monthly_surplus(rows, ctx) -> dict | None:
    k = ctx["kpis"]
    if k["trailing_monthly_surplus"] <= 0:
        return None
    trailing = set(k["trailing_months"])
    ev = [r.id for r in rows if r.month in trailing and r.kind == "income"]
    rate = k["trailing_savings_rate"] or 0
    return signal(
        "monthly_surplus", "Monthly surplus",
        f"Income exceeds spending by {_chf(k['trailing_monthly_surplus'])} a month "
        f"(savings rate {rate:.0%}, last {len(trailing)} complete months).",
        ev,
        {"trailing_monthly_surplus": k["trailing_monthly_surplus"], "trailing_savings_rate": k["trailing_savings_rate"],
         "trailing_monthly_free_cash_flow": k["trailing_monthly_free_cash_flow"],
         "per_month": [{"month": m["month"], "surplus": m["surplus"], "savings_rate": m["savings_rate"]} for m in ctx["months"]]},
        "high" if rate >= 0.2 else "medium",
    )


def spending_exceeds_income(rows, ctx) -> dict | None:
    k = ctx["kpis"]
    if k["trailing_monthly_surplus"] >= 0:
        return None
    trailing = set(k["trailing_months"])
    ev = [r.id for r in rows if r.month in trailing and r.kind == "expense" and r.amount < -200]
    return signal(
        "spending_exceeds_income", "Spending exceeds income",
        f"Spending is {_chf(-k['trailing_monthly_surplus'])} a month above income over the last {len(trailing)} complete months.",
        ev, {"trailing_monthly_surplus": k["trailing_monthly_surplus"]}, "high",
    )


def idle_cash(rows, ctx) -> dict | None:
    k = ctx["kpis"]
    months = float(config.settings()["signals"]["idle_cash_months"])
    if k["balance"] is None:
        return None
    reserve = months * k["monthly_spending"]
    idle = k["balance"] - reserve
    if idle < 1000:
        return None
    last = next(r for r in reversed(rows) if r.balance is not None)
    return signal(
        "idle_cash", "Idle cash",
        f"Balance of {_chf(k['balance'])} is {_chf(idle)} above a {months:g}-month spending reserve ({_chf(reserve)}).",
        [last.id],
        {"balance": k["balance"], "reserve": round(reserve, 2), "reserve_months": months, "idle_cash": round(idle, 2)},
        "high" if idle > 3 * reserve else "medium",
    )


def foreign_spend(rows, ctx) -> dict | None:
    s = config.settings()["signals"]["foreign_spend"]
    w = ctx["window_rows"]
    total = -sum(r.amount for r in w if r.kind == "expense")
    fx = [r for r in w if r.kind == "expense" and r.is_foreign]
    vol = -sum(r.amount for r in fx)
    share = vol / total if total else 0
    if share < float(s["min_share"]) or vol < float(s["min_volume_chf"]):
        return None
    top = defaultdict(float)
    for r in fx:
        top[r.counterparty] += -r.amount
    names = ", ".join(n for n, _ in sorted(top.items(), key=lambda kv: -kv[1])[:3])
    return signal(
        "foreign_spend", "Foreign & travel spend",
        f"{_chf(vol)} ({share:.0%} of spending) went to merchants abroad, e.g. {names}.",
        [r.id for r in fx],
        {"volume": round(vol, 2), "share": round(share, 4), "count": len(fx),
         "note": "All transactions are booked in CHF; foreign merchants are detected from descriptions, "
                 "so FX fees actually paid cannot be measured."},
        "high" if share >= 0.1 else "medium",
    )


def recurring_rent(rows, ctx) -> dict | None:
    flows = [f for f in ctx["flows"] if f["category"] == "rent" and f["direction"] == "out" and f["count"] >= 6]
    if not flows:
        return None
    f = max(flows, key=lambda x: x["annualised"])
    return signal(
        "recurring_rent", "Recurring rent",
        f"Monthly rent of about {_chf(f['avg_amount'])} to '{f['counterparty']}' ({_chf(f['annualised'])} a year).",
        f["evidence"],
        {"monthly_rent": f["avg_amount"], "annual_rent": f["annualised"], "landlord": f["counterparty"]},
    )


def family(rows, ctx) -> dict | None:
    kids = [r for r in ctx["window_rows"] if r.category in ("childcare", "school")]
    if len(kids) < 2:
        return None
    childcare = -sum(r.amount for r in kids if r.category == "childcare")
    school = -sum(r.amount for r in kids if r.category == "school")
    return signal(
        "family", "Children in the household",
        f"{len(kids)} payments for childcare or school ({_chf(childcare + school)} a year).",
        [r.id for r in kids],
        {"childcare_annual": round(childcare, 2), "school_annual": round(school, 2), "count": len(kids)},
    )


def pillar_3a_gap(rows, ctx) -> dict | None:
    p = ctx["profile"]
    ok, why = pillar3a.eligibility(p)
    if not ok:
        return None
    cap, basis = pillar3a.cap(p)
    contrib_rows = [r for r in ctx["window_rows"] if r.category == "pillar_3a"]
    paid = -sum(r.amount for r in contrib_rows)
    gap = cap - paid
    if gap <= float(config.settings()["pillar3a"]["min_gap_share"]) * cap:
        return None
    earned_ev = [r.id for r in ctx["window_rows"] if r.category in ("salary", "self_employment_income", "gig_income")][:24]
    status = "missing" if paid <= 0 else "below maximum"
    return signal(
        "pillar_3a_gap", "Pillar 3a gap",
        (f"Earned income but no Pillar 3a contributions detected; up to {_chf(cap)} a year is tax-deductible."
         if paid <= 0 else
         f"Pillar 3a contributions of {_chf(paid)} are {_chf(gap)} below the {_chf(cap)} maximum."),
        [r.id for r in contrib_rows] + earned_ev,
        {"paid_12m": round(paid, 2), "cap": cap, "cap_basis": basis, "gap": round(gap, 2), "status": status,
         "has_pension_fund": p["has_pension_fund"]},
        "high",
    )


def _salary_series(rows: list[Row]) -> dict[str, list[Row]]:
    by_payer: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.category == "salary" and r.amount > 0:
            by_payer[re.sub(r"\s*(lohnzahlung|lohn|gehalt)\b.*$", "", r.counterparty, flags=re.I).strip() or r.counterparty].append(r)
    return by_payer


def salary_jump(rows, ctx) -> dict | None:
    s = config.settings()["signals"]
    thr = float(s["salary_jump_threshold"])
    max_cv = float(s["recurring"]["max_amount_cv"])
    best = None
    for payer, pays in _salary_series(rows).items():
        amts = [p.amount for p in pays]
        for i in range(3, len(amts) - 1):
            prior = amts[max(0, i - 3):i]
            if statistics.pstdev(prior) / statistics.fmean(prior) > max_cv:
                continue  # irregular pay (temp work, hourly): a jump is not meaningful
            before = statistics.median(prior)
            after = statistics.median(amts[i:i + 3])
            change = after / before - 1 if before else 0
            if change > thr and (best is None or change > best[1]):
                best = (payer, change, before, after, pays[max(0, i - 2):i + 2])
    if not best:
        return None
    payer, change, before, after, ev = best
    return signal(
        "salary_jump", "Salary increase",
        f"Salary from {payer} rose {change:.0%} (from {_chf(before)} to {_chf(after)} per payment) on {ev[-2].date.isoformat()}.",
        [r.id for r in ev], {"payer": payer, "change": round(change, 4), "before": before, "after": after},
        "high",
    )


def _new_and_stopped(series: dict[str, list[Row]], rows: list[Row]) -> tuple[list, list]:
    s = config.settings()["signals"]
    first, last = rows[0].date, rows[-1].date
    new = [(k, v) for k, v in series.items()
           if v[0].date >= first + timedelta(days=int(s["new_flow_min_days_after_start"])) and len(v) >= 2]
    stopped = [(k, v) for k, v in series.items()
               if v[-1].date <= last - timedelta(days=int(s["stopped_flow_min_days_before_end"]))]
    return new, stopped


def new_employer(rows, ctx) -> dict | None:
    new, stopped = _new_and_stopped(_salary_series(rows), rows)
    if not new:
        return None
    payer, pays = new[0]
    old = stopped[0] if stopped else None
    summary = f"New salary payer '{payer}' since {pays[0].date.isoformat()}"
    summary += f", replacing '{old[0]}' (last paid {old[1][-1].date.isoformat()})." if old else "."
    ev = [p.id for p in pays[:2]] + ([p.id for p in old[1][-2:]] if old else [])
    return signal("new_employer", "New employer", summary, ev,
                  {"new_payer": payer, "since": pays[0].date.isoformat(), "previous_payer": old[0] if old else None}, "high")


MOVE_RE = r"umzug|zügel|zuegel|moving|mietkaution|rental deposit|umzugsfirma|möbeltransport"


def move(rows, ctx) -> dict | None:
    reasons, ev = [], []
    rent_series: dict[str, list[Row]] = defaultdict(list)
    util_series: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.category == "rent":
            rent_series[r.counterparty].append(r)
        elif r.category in ("utilities", "telecom"):
            util_series[r.counterparty].append(r)
    new_rent, stopped_rent = _new_and_stopped(rent_series, rows)
    if new_rent and stopped_rent:
        reasons.append(f"rent now paid to '{new_rent[0][0]}' instead of '{stopped_rent[0][0]}'")
        ev += [r.id for r in new_rent[0][1][:2]] + [r.id for r in stopped_rent[0][1][-2:]]
    movers = [r for r in rows if re.search(MOVE_RE, r.description, re.I)]
    if movers:
        reasons.append(f"moving-related payment ('{movers[0].description}')")
        ev += [r.id for r in movers]
    new_util, stopped_util = _new_and_stopped(util_series, rows)
    if new_util and stopped_util:
        reasons.append(f"new utility provider '{new_util[0][0]}'")
        ev += [r.id for r in new_util[0][1][:2]]
    if not reasons:
        return None
    return signal("move", "Recent move", "Signs of a move: " + "; ".join(reasons) + ".", ev, {"reasons": reasons}, "medium")


def debt_stress(rows, ctx) -> dict | None:
    w = ctx["window_rows"]
    debt = [r for r in w if r.category == "debt_repayment"]
    fees = [r for r in w if r.category == "bank_fees" and re.search(r"mahn|überzieh|overdraft|zins", r.description, re.I)]
    if len(debt) < int(config.settings()["signals"]["debt_stress_min_payments"]) and not fees:
        return None
    paid = -sum(r.amount for r in debt)
    fee_total = -sum(r.amount for r in fees)
    return signal(
        "debt_stress", "Consumer debt",
        f"{len(debt)} debt repayments ({_chf(paid)} a year)" + (f" and {len(fees)} penalty/overdraft fees ({_chf(fee_total)})." if fees else "."),
        [r.id for r in debt + fees],
        {"debt_repayments_annual": round(paid, 2), "penalty_fees_annual": round(fee_total, 2), "count": len(debt)},
        "high",
    )


def existing_mortgage(rows, ctx) -> dict | None:
    m = [r for r in ctx["window_rows"] if r.category == "mortgage_interest"]
    if len(m) < 3:
        return None
    annual = -sum(r.amount for r in m)
    return signal(
        "existing_mortgage", "Existing mortgage",
        f"Mortgage interest of {_chf(annual)} a year ({len(m)} payments).",
        [r.id for r in m], {"annual_interest": round(annual, 2), "count": len(m)},
    )


def external_investments(rows, ctx) -> dict | None:
    inv = [r for r in ctx["window_rows"] if r.category in ("investments", "pillar_3a") and not re.search(r"\bubs\b", r.description, re.I)]
    inv = [r for r in inv if re.search(r"swissquote|viac|julius|bär|baer|lombard|pictet|frankly|finpension|postfinance|raiffeisen|private equity|portfolio", r.description, re.I)]
    if len(inv) < 2:
        return None
    total = -sum(r.amount for r in inv)
    providers = sorted({r.counterparty for r in inv})
    return signal(
        "external_investments", "Investments held elsewhere",
        f"{_chf(total)} a year goes to other providers: {', '.join(providers[:3])}.",
        [r.id for r in inv], {"annual": round(total, 2), "providers": providers},
    )


def high_net_worth(rows, ctx) -> dict | None:
    s = config.settings()["signals"]
    k, p = ctx["kpis"], ctx["profile"]
    tier = p["segment"] in ("High net worth", "Very high net worth", "Ultra high net worth")
    high_income = k["annual_income"] >= float(s["high_income_chf"])
    if not (tier or high_income):
        return None
    big = sorted((r for r in ctx["window_rows"] if r.kind == "income"), key=lambda r: -r.amount)[:10]
    basis = []
    if tier:
        basis.append(f"segment '{p['segment']}'")
    if high_income:
        basis.append(f"income {_chf(k['annual_income'])} a year")
    return signal("high_net_worth", "High net worth", "Wealth indicators: " + ", ".join(basis) + ".",
                  [r.id for r in big], {"segment": p["segment"], "annual_income": k["annual_income"]}, "high")


def self_employed(rows, ctx) -> dict | None:
    p = ctx["profile"]
    if p["employment_type"] != "self_employed":
        return None
    ev = [r for r in ctx["window_rows"] if r.category in ("self_employment_income", "gig_income", "business_expense")]
    biz = -sum(r.amount for r in ev if r.category == "business_expense")
    return signal(
        "self_employed", "Self-employed",
        f"Income comes mainly from clients or platforms (net about {_chf(p['self_employment_net'])} a year)"
        + (f"; {_chf(biz)} of business expenses run through this private account." if biz else "."),
        [r.id for r in ev][:40], {"self_employment_net": p["self_employment_net"], "business_expenses": round(biz, 2)},
    )


def retired(rows, ctx) -> dict | None:
    p = ctx["profile"]
    pens = [r for r in ctx["window_rows"] if r.category == "pension_income"]
    if p["employment_type"] != "retired" or not pens:
        return None
    return signal("retired", "Retired", f"Pension income of {_chf(sum(r.amount for r in pens))} a year (AHV / pension fund).",
                  [r.id for r in pens], {"pension_income": round(sum(r.amount for r in pens), 2), "still_working": p["still_working"]})


DETECTORS: list[Callable] = [
    recurring_income, monthly_surplus, spending_exceeds_income, idle_cash, foreign_spend, recurring_rent, family,
    pillar_3a_gap, salary_jump, new_employer, move, debt_stress, existing_mortgage, external_investments,
    high_net_worth, self_employed, retired,
]


def detect_all(rows: list[Row], kpis: dict, months: list[dict], flows: list[dict], profile: dict) -> list[dict]:
    start, end = window(rows)
    ctx = {"kpis": kpis, "months": months, "flows": flows, "profile": profile, "window_rows": in_window(rows, start, end)}
    out = []
    for det in DETECTORS:
        sig = det(rows, ctx)
        if sig:
            out.append(sig)
    return out
