"""Client for the (unofficial, undocumented) JSON API behind the ESTV tax calculator
https://swisstaxcalculator.estv.admin.ch. Responses are cached in SQLite by payload hash."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import httpx

from .. import config, db

RELATIONSHIP = {"single": 1, "married": 2}
CONFESSION = {"reformed": 1, "roman_catholic": 2, "christ_catholic": 3, "none": 4, "other": 5}
REVENUE_TYPE = {"employee": 1, "self_employed": 2, "retired": 3, "other": 4}


class EstvError(RuntimeError):
    pass


def _call(op: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload, sort_keys=True)
    key = hashlib.sha256(f"{op}|{body}".encode()).hexdigest()
    row = db.fetchone("SELECT response_json FROM estv_cache WHERE key=?", (key,))
    if row:
        return json.loads(row["response_json"])
    s = config.settings()["tax"]
    try:
        r = httpx.post(s["estv_base_url"] + op, content=body, headers={"Content-Type": "application/json"},
                       timeout=float(s["timeout_s"]))
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EstvError(f"ESTV {op} unreachable or rejected the request: {exc}") from exc
    if r.status_code >= 400 or "response" not in data:
        raise EstvError(f"ESTV {op} failed (HTTP {r.status_code})")
    db.execute("INSERT OR REPLACE INTO estv_cache (key, response_json, created_at) VALUES (?,?,?)",
               (key, json.dumps(data["response"]), db.now_iso()))
    return data["response"]


def location_id(zip_code: str, city: str, tax_year: int) -> dict:
    res = _call("API_searchLocation", {"Search": str(zip_code), "Language": 1, "TaxYear": tax_year})
    if not res:
        raise EstvError(f"ESTV knows no location for {zip_code} {city}")
    exact = [r for r in res if str(r.get("ZipCode")) == str(zip_code)]
    same_city = [r for r in exact if r.get("City", "").lower() == city.lower()]
    return (same_city or exact or res)[0]


def household_payload(p: dict, tax_year: int, loc_id: int) -> dict:
    etype = p["employment_type"] if p["employment_type"] in REVENUE_TYPE else "other"
    if etype == "employee":
        revenue = p["gross_salary_est"]
    elif etype == "self_employed":
        revenue = p["self_employment_net"]
    elif etype == "retired":
        revenue = p["pension_income"]
    else:
        revenue = p["earned_income_net"]
    married = p["relationship"] == "married"
    conf = CONFESSION.get(p.get("confession", "none"), 4)
    payload = {
        "SimKey": None,
        "TaxYear": tax_year,
        "TaxLocationID": loc_id,
        "Relationship": RELATIONSHIP["married" if married else "single"],
        "Confession1": conf,
        "Children": [{"Age": 10} for _ in range(int(p.get("children", 0)))],
        "Age1": int(p["age"]),
        "RevenueType1": REVENUE_TYPE[etype],
        "Revenue1": round(max(0.0, revenue)),
        "Fortune": round(max(0.0, p.get("wealth_for_tax", 0.0))),
    }
    if married:
        payload |= {"Confession2": conf, "Age2": int(p["age"]), "RevenueType2": REVENUE_TYPE["other"], "Revenue2": 0}
    else:
        payload |= {"Confession2": 0, "Age2": 0, "RevenueType2": 0, "Revenue2": 0}
    return payload


def taxes_for_3a(p: dict, contributions: list[float], tax_year: int) -> dict:
    """Total tax (CHF) for each 3a contribution amount, computed by ESTV in detailed mode.

    ESTV derives social deductions, insurance and professional-expense deductions from gross
    income itself; we only change the Pillar 3a budget line (PRAEMIEN3A) between calls.
    """
    loc = location_id(p["zip"], p["city"], tax_year)
    base = household_payload(p, tax_year, loc["TaxLocationID"])
    budget = _call("API_calculateTaxBudget", base)
    if not any(line.get("Ident") == "PRAEMIEN3A" for line in budget):
        raise EstvError("ESTV budget has no Pillar 3a line")
    side = p.get("side_income_net", 0.0)
    out: dict[str, Any] = {"location": {k: loc.get(k) for k in ("ZipCode", "City", "Canton", "BfsName")}, "taxes": {}}
    for c in contributions:
        b = copy.deepcopy(budget)
        for line in b:
            if line.get("Ident") == "PRAEMIEN3A":
                line["Value"] = round(c)
            if line.get("Ident") == "NEBENERWERB_P1" and side:
                line["Value"] = round(side)
        r = _call("API_calculateDetailedTaxes", {**base, "Fortune": 0, "Budget": b})
        out["taxes"][round(c)] = {
            "total": r.get("TotalTax"),
            "income_tax": sum(r.get(k, 0) or 0 for k in ("IncomeTaxFed", "IncomeTaxCanton", "IncomeTaxCity", "IncomeTaxChurch")),
            "taxable_income_canton": r.get("TaxableIncomeCanton"),
            "taxable_income_fed": r.get("TaxableIncomeFed"),
            "marginal_rate": r.get("MarginalTaxRate"),
        }
    out["payload"] = base
    return out
