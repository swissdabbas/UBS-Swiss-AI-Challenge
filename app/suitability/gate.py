"""FinSA gate: risk profile (capacity x tolerance), suitability, appropriateness and eligibility.

A product that fails any check is hidden from the recommendations and listed with its reasons.
"""

from __future__ import annotations

import statistics

from .. import config
from ..ingest.schemas import Product
from ..recommend.mortgage import affordability
from ..tax import pillar3a
from .questionnaire import HORIZON_YEARS, LEVEL_RANK, RISK_QUESTIONS


def _band(value: float, cuts: list[float]) -> int:
    """1..5 score: number of cut points the value reaches, plus one."""
    return 1 + sum(1 for c in cuts if value >= c)


def risk_capacity(p: dict, kpis: dict, signal_types: set[str]) -> dict:
    years_to_ret = max(0, int(config.settings()["pillar3a"]["retirement_age"]) - p["age"])
    rate = kpis.get("trailing_savings_rate")
    reserve = kpis.get("reserve_months") or 0
    if "recurring_income" in signal_types and p["employment_type"] in ("employee", "retired"):
        stability = 4
    elif p["employment_type"] in ("self_employed", "independent_means"):
        stability = 3
    else:
        stability = 2
    seg = p["segment"]
    wealth = 5 if "high net worth" in seg.lower() else 4 if seg == "Mass affluent" else 1 if seg.startswith(("Low", "Negative")) else 3
    comps = {
        "horizon": (_band(years_to_ret, [3, 8, 15, 25]), f"{years_to_ret} years to retirement"),
        "savings_rate": (_band(rate if rate is not None else -1, [0.0, 0.05, 0.15, 0.30]),
                         f"savings rate {rate:.0%}" if rate is not None else "no income"),
        "reserve": (_band(reserve, [1, 3, 6, 12]), f"{reserve:g} months of spending in the account"),
        "income_stability": (stability, "regular income" if stability >= 4 else "irregular or self-employed income"),
        "wealth": (wealth, f"segment: {seg}"),
    }
    score = round(statistics.fmean(v for v, _ in comps.values()))
    caps = []
    if "debt_stress" in signal_types:
        score = min(score, 1)
        caps.append("capped at 1: consumer debt should be repaid before taking investment risk")
    if "spending_exceeds_income" in signal_types:
        score = min(score, 2)
        caps.append("capped at 2: spending currently exceeds income")
    return {"score": int(score), "components": {k: {"score": v, "basis": b} for k, (v, b) in comps.items()}, "caps": caps}


def risk_tolerance(risk_answers: dict) -> int | None:
    vals = [int(risk_answers[q["id"]]) for q in RISK_QUESTIONS if risk_answers.get(q["id"]) not in (None, "")]
    if len(vals) < len(RISK_QUESTIONS):
        return None
    return int(round(statistics.fmean(vals)))


def client_context(p: dict, kpis: dict, signals: list[dict], answers: dict) -> dict:
    s = config.settings()["suitability"]
    types = {sg["type"] for sg in signals}
    cap = risk_capacity(p, kpis, types)
    tol = risk_tolerance(answers.get("risk", {}))
    profile_score = min(cap["score"], tol) if tol is not None else None
    horizon_answer = answers.get("risk", {}).get("horizon")
    horizon = HORIZON_YEARS.get(int(horizon_answer)) if horizon_answer else None
    assets = (kpis.get("balance") or 0.0) + float(p.get("other_assets") or 0.0)
    return {
        "profile": p,
        "kpis": kpis,
        "signal_types": types,
        "risk_capacity": cap,
        "risk_tolerance": tol,
        "risk_profile": profile_score,
        "risk_profile_label": s["profiles"].get(profile_score) if profile_score else None,
        "horizon_years": horizon,
        "knowledge": answers.get("knowledge", {}),
        "financial_assets": round(assets, 2),
        "mortgage": affordability(p, kpis, types),
        "complete": tol is not None and horizon is not None and bool(answers.get("knowledge")),
    }


def check_product(prod: Product, ctx: dict) -> dict:
    s = config.settings()["suitability"]
    p = ctx["profile"]
    checks: list[dict] = []

    def add(kind: str, ok: bool, detail: str) -> None:
        checks.append({"type": kind, "ok": ok, "detail": detail})

    # segment
    if prod.segment == "intermediary":
        add("eligibility", False, "Service for financial intermediaries, not offered to private clients.")
    elif prod.segment == "info":
        add("eligibility", False, "Research publication, not an investable product.")
    elif prod.segment == "business" and p["employment_type"] != "self_employed":
        add("eligibility", False, "Business product; requires self-employment or a company.")

    # eligibility
    el = prod.eligibility
    if el.get("age_max") and p["age"] > int(el["age_max"]):
        add("eligibility", False, f"Age limit {el['age_max']} (you are {p['age']}).")
    if el.get("requires_student") and not p.get("is_student"):
        add("eligibility", False, "For students only.")
    if el.get("requires_3a_eligible"):
        ok, why = pillar3a.eligibility(p)
        if not ok:
            add("eligibility", False, why)
    if el.get("credit_check") and ctx["signal_types"] & {"debt_stress", "spending_exceeds_income"}:
        add("eligibility", False, "Credit check: current consumer debt or a spending deficit; more credit is not in your interest.")
    if el.get("min_income_key"):
        need = float(s["credit_card_min_income"][el["min_income_key"]])
        income = ctx["kpis"]["annual_income"]
        if income < need:
            add("eligibility", False, f"Card affordability: income CHF {income:,.0f} is below the CHF {need:,.0f} guideline.")
    if el.get("qualified_investor"):
        need = float(s["qualified_investor_min_assets"])
        if ctx["financial_assets"] < need:
            add("eligibility", False, f"Qualified investors only: requires financial assets of CHF {need:,.0f}+ "
                                      f"(declared/seen: CHF {ctx['financial_assets']:,.0f}).")
    if el.get("mortgage_affordability"):
        m = ctx["mortgage"]
        if not m["eligible"]:
            add("eligibility", False, "Mortgage affordability: " + ("; ".join(m["reasons"]) or "no rent or mortgage history."))

    # suitability (FinSA art. 12): risk, horizon, financial situation
    if prod.risk_class > 0:
        rp = ctx["risk_profile"] or 0
        if prod.risk_class > rp:
            add("suitability", False, f"Product risk {prod.risk_class}/5 exceeds your risk profile "
                                      f"{rp}/5 ({ctx['risk_profile_label'] or 'not set'}).")
        if prod.min_horizon_years and (ctx["horizon_years"] or 0) < prod.min_horizon_years:
            add("suitability", False, f"Needs an investment horizon of {prod.min_horizon_years:g}+ years "
                                      f"(yours: {ctx['horizon_years'] or 0} years).")
        if ctx["signal_types"] & {"debt_stress", "spending_exceeds_income"}:
            add("suitability", False, "Financial situation: repay consumer debt and stabilise the budget before investing.")
        if not any(c["type"] == "suitability" and not c["ok"] for c in checks):
            add("suitability", True, f"Risk {prod.risk_class}/5 fits your profile {rp}/5; horizon fits.")

    # appropriateness (FinSA art. 11): knowledge and experience
    if prod.knowledge:
        have = ctx["knowledge"].get(prod.knowledge, "none")
        if LEVEL_RANK.get(have, 0) < LEVEL_RANK[prod.knowledge_level]:
            add("appropriateness", False, f"Requires '{prod.knowledge_level}' knowledge of {prod.knowledge.replace('_', ' ')} "
                                          f"(declared: '{have}').")
        else:
            add("appropriateness", True, f"Declared knowledge of {prod.knowledge.replace('_', ' ')}: {have}.")

    passed = all(c["ok"] for c in checks)
    return {"product_id": prod.id, "passed": passed, "checks": checks,
            "reasons": [c["detail"] for c in checks if not c["ok"]]}
