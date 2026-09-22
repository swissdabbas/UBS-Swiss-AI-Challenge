"""Client profile = file-name facts + transaction-derived facts + client questionnaire answers.

Every field records where it came from, so the UI and the advice record can show it.
"""

from __future__ import annotations

import re

from .. import config
from ..ingest.schemas import Profile
from .cashflow import Row, in_window, window

OVERRIDABLE = (
    "age", "marital_status", "children", "zip", "city", "canton", "confession",
    "employment_type", "has_pension_fund", "is_student", "other_assets",
)


def _sum(rows: list[Row], cats: set[str]) -> float:
    return sum(r.amount for r in rows if r.category in cats)


def derive_profile(base: Profile, rows: list[Row], answers: dict | None = None) -> dict:
    s = config.settings()
    answers = answers or {}
    start, end = window(rows)
    w = in_window(rows, start, end)

    salary = _sum(w, {"salary", "bonus"})
    self_emp = _sum(w, {"self_employment_income"})
    gig = _sum(w, {"gig_income"})
    business_costs = -_sum(w, {"business_expense"})
    pension = _sum(w, {"pension_income"})
    invest_income = _sum(w, {"investment_income"})
    self_emp_net = max(0.0, self_emp + gig - business_costs)
    earned = salary + self_emp_net

    if earned <= 0 and pension > 0:
        employment = "retired"
    elif earned <= 0 and invest_income > 0:
        employment = "independent_means"
    elif salary >= self_emp_net:
        employment = "employee"
    elif earned > 0:
        employment = "self_employed"
    else:
        employment = "unknown"
    if pension > 0 and base.age >= 63 and employment != "employee":
        employment = "retired"

    gross_salary = salary / float(s["tax"]["net_to_gross_salary"]) if salary else 0.0
    has_pf = employment == "employee" and gross_salary >= float(s["pillar3a"]["bvg_entry_threshold"])
    if any(r.category == "pillar_2_buyin" for r in w):
        has_pf = True

    kids_rows = [r for r in w if r.category in ("childcare", "school")]
    children = 1 if kids_rows else 0

    loc = dict(s["tax"]["default_location"])
    loc_source = "default"
    if any(re.search(r"\bewz\b|zürich|zurich", r.description, re.I) for r in rows):
        loc_source = "transactions (Zürich merchants / EWZ utility)"

    p = {
        "client_id": base.client_id,
        "display_name": base.display_name,
        "occupation": base.occupation,
        "segment": base.segment,
        "gender": base.gender,
        "age": base.age,
        "age_band": base.age_band,
        "marital_status": base.marital_status,
        "children": children,
        "zip": loc["zip"],
        "city": loc["city"],
        "canton": loc["canton"],
        "confession": "none",
        "employment_type": employment,
        "still_working": earned > 0,
        "has_pension_fund": has_pf,
        "is_student": False,
        "other_assets": 0.0,
        "salary_net": round(salary, 2),
        "gross_salary_est": round(gross_salary, 2),
        "self_employment_net": round(self_emp_net, 2),
        "pension_income": round(pension, 2),
        "investment_income": round(invest_income, 2),
        "earned_income_net": round(earned, 2),
        "sources": {
            "age": "file name (band midpoint, please confirm)" if base.age_estimated else "file name",
            "marital_status": "file name",
            "children": "transactions (childcare / school payments)" if kids_rows else "transactions (none seen)",
            "zip": loc_source, "city": loc_source, "canton": loc_source,
            "confession": "default (no church tax)",
            "employment_type": "transactions (income mix)",
            "has_pension_fund": "transactions (salary above BVG threshold / pension buy-in)",
            "is_student": "default",
            "other_assets": "default",
        },
    }
    for key in OVERRIDABLE:
        if key in answers and answers[key] not in (None, ""):
            p[key] = answers[key]
            p["sources"][key] = "client questionnaire"
    p["age"] = int(p["age"])
    p["children"] = int(p["children"])
    p["other_assets"] = float(p["other_assets"] or 0)
    p["relationship"] = "married" if p["marital_status"] in ("married", "registered_partnership") else "single"
    if p["employment_type"] == "self_employed":
        p["has_pension_fund"] = bool(answers.get("has_pension_fund", False))
    return p
