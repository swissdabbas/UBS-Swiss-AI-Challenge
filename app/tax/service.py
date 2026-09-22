"""Pillar 3a recommendation and tax saved: ESTV live (cached), else the pre-computed canton table."""

from __future__ import annotations

import json
from functools import lru_cache

from .. import config
from ..features.cashflow import Row
from . import estv, pillar3a


@lru_cache(maxsize=1)
def _tables() -> dict | None:
    p = config.path(config.settings()["tax"]["fallback_tables"])
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def fallback_rate(canton: str, gross: float, relationship: str) -> tuple[float, str]:
    """Interpolated marginal 3a saving rate for the canton capital (rough estimate)."""
    t = _tables()
    if not t or canton not in t["cantons"]:
        return 0.20, "flat 20% assumption (no table for this canton)"
    xs, ys = t["gross_incomes"], t["cantons"][canton][relationship]
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if gross <= pts[0][0]:
        rate = pts[0][1]
    elif gross >= pts[-1][0]:
        rate = pts[-1][1]
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= gross <= x1:
                rate = y0 + (y1 - y0) * (gross - x0) / (x1 - x0)
                break
    city = t["cantons"][canton]["city"]
    return rate, f"pre-computed ESTV table for {city} ({canton}), {t['tax_year']}; municipality differences not modelled"


def _round_down(x: float, step: int = 100) -> float:
    return float(int(x // step) * step)


def pillar3a_plan(p: dict, kpis: dict, rows: list[Row]) -> dict:
    s = config.settings()
    c3a = s["pillar3a"]
    year = int(c3a["tax_year"])
    ok, why = pillar3a.eligibility(p)
    if not ok:
        return {"eligible": False, "reason": why}

    cap, basis = pillar3a.cap(p)
    window_start = kpis["window_start"]
    paid = -sum(r.amount for r in rows if r.category == "pillar_3a" and r.date.isoformat() >= window_start)
    free = max(0.0, kpis["annual_free_cash_flow"])
    affordable = paid + free * float(c3a["surplus_share_for_3a"])
    recommended = cap if affordable >= cap else _round_down(max(paid, affordable))
    work_limit = c3a["retirement_age"] + (c3a["max_work_extension_years"] if p["age"] >= c3a["retirement_age"] else 0)
    years = max(1, work_limit - p["age"])

    first_gap = int(c3a["retroactive_first_gap_year"])
    paid_2025 = -sum(r.amount for r in rows if r.category == "pillar_3a" and r.date.year == first_gap)
    data_starts = min(r.date for r in rows)
    retro_amount = max(0.0, cap - paid_2025) if year > first_gap else 0.0

    tax_input = dict(p)
    tax_input["side_income_net"] = p["self_employment_net"] if p["employment_type"] in ("employee", "retired") else 0.0
    tax_input["wealth_for_tax"] = max(0.0, (kpis.get("balance") or 0.0) + p.get("other_assets", 0.0))

    result = {
        "eligible": True,
        "reason": why,
        "cap": cap,
        "cap_basis": basis,
        "paid_12m": round(paid, 2),
        "recommended_annual": recommended,
        "recommended_monthly": round(recommended / 12, 2),
        "additional_annual": round(max(0.0, recommended - paid), 2),
        "affordable_from_surplus": round(affordable, 2),
        "years_to_retirement": years,
        "tax_year": year,
        "location": {"zip": p["zip"], "city": p["city"], "canton": p["canton"]},
        "retroactive": {
            "year": first_gap,
            "paid_seen": round(paid_2025, 2),
            "possible_up_to": round(retro_amount, 2),
            "data_starts": data_starts.isoformat(),
            "note": (f"Since 2025, missed 3a contributions can be paid in later (up to 10 years back), "
                     f"provided the current year's maximum is paid first. Only contributions from "
                     f"{data_starts.isoformat()} are visible, so the {first_gap} gap is an upper bound to confirm."),
        },
    }
    try:
        r = estv.taxes_for_3a(tax_input, sorted({0.0, paid, recommended}), year)
        t = r["taxes"]
        tax0, taxp, taxr = t[0]["total"], t[round(paid)]["total"], t[round(recommended)]["total"]
        saved_total = tax0 - taxr
        saved_additional = taxp - taxr
        marginal = saved_total / recommended if recommended else (t[0]["marginal_rate"] or 0) / 100
        retro_saved = None
        if retro_amount > 0:
            retro_saved = _retro_saving_estv(tax_input, recommended, retro_amount, year)
        result.update(
            source="ESTV tax calculator (live, cached)",
            estv_location=r["location"],
            tax_without_3a=tax0,
            tax_with_current=taxp,
            tax_with_recommended=taxr,
            estv_payload=r["payload"],
        )
    except estv.EstvError as exc:
        gross = p["gross_salary_est"] or p["earned_income_net"]
        marginal, note = fallback_rate(p["canton"], gross, p["relationship"])
        saved_total = marginal * recommended
        saved_additional = marginal * max(0.0, recommended - paid)
        retro_saved = marginal * retro_amount if retro_amount else None
        result.update(source=f"Rough estimate: {note} (ESTV unavailable: {exc})")

    result.update(
        tax_saved_per_year=round(saved_total, 0),
        additional_tax_saved_per_year=round(saved_additional, 0),
        marginal_rate=round(marginal, 4),
        cumulative_tax_saved=round(saved_total * years, 0),
    )
    result["retroactive"]["tax_saved_estimate"] = round(retro_saved, 0) if retro_saved else None
    return result


def _retro_saving_estv(p: dict, recommended: float, retro: float, year: int) -> float:
    """Retroactive buy-ins are an extra deduction; model them in ESTV's 'other deductions' line."""
    loc = estv.location_id(p["zip"], p["city"], year)
    base = estv.household_payload(p, year, loc["TaxLocationID"])
    budget = estv._call("API_calculateTaxBudget", base)
    totals = []
    for extra in (0, retro):
        b = [dict(line) for line in budget]
        for line in b:
            if line.get("Ident") == "PRAEMIEN3A":
                line["Value"] = round(recommended)
            if line.get("Ident") == "UEBRIGEABZUEGE":
                line["Value"] = round(extra)
        totals.append(estv._call("API_calculateDetailedTaxes", {**base, "Fortune": 0, "Budget": b})["TotalTax"])
    return totals[0] - totals[1]
