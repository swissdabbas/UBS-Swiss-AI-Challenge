"""Swiss mortgage affordability rule of thumb (Tragbarkeit): imputed 5% interest + 1% maintenance
+ amortisation of the second mortgage must stay below a third of gross income; 20% equity."""

from __future__ import annotations

from .. import config


def affordability(p: dict, kpis: dict, signal_types: set[str]) -> dict:
    m = config.settings()["mortgage"]
    retirement_age = int(config.settings()["pillar3a"]["retirement_age"])
    gross = p["gross_salary_est"] + p["self_employment_net"] + p["pension_income"]
    years_to_ret = max(1, retirement_age - p["age"])
    amort_years = max(1, min(int(m["amortization_years"]), years_to_ret))
    ltv = float(m["max_ltv"])
    cost_factor = ltv * float(m["imputed_rate"]) + float(m["maintenance_rate"]) + float(m["second_mortgage_share"]) / amort_years
    max_by_income = float(m["affordability_max_share"]) * gross / cost_factor if gross > 0 else 0.0
    liquid = max(0.0, (kpis.get("balance") or 0.0) + p.get("other_assets", 0.0))
    max_by_equity = liquid / (1 - ltv)
    max_price = min(max_by_income, max_by_equity)
    min_price = float(m["min_property_price"])
    stable = "recurring_income" in signal_types and p["employment_type"] == "employee"
    renter = "recurring_rent" in signal_types
    owner = "existing_mortgage" in signal_types

    purchase_ok = renter and stable and p["age"] <= int(m["max_age_new_mortgage"]) and max_price >= min_price
    refinance_ok = owner and p["age"] <= int(m["max_age_refinancing"])
    reasons = []
    if renter and not purchase_ok:
        if not stable:
            reasons.append("no stable salaried income detected (banks require a regular salary for a new mortgage)")
        if p["age"] > int(m["max_age_new_mortgage"]):
            reasons.append(f"age {p['age']} is above {m['max_age_new_mortgage']}, too close to retirement for a new mortgage")
        if max_by_income < min_price:
            reasons.append(f"income supports a property up to about CHF {max_by_income:,.0f} "
                           f"(one-third rule at {m['imputed_rate']:.0%} imputed rate), below CHF {min_price:,.0f}")
        if max_by_equity < min_price:
            reasons.append(f"savings of CHF {liquid:,.0f} cover the 20% equity for a property up to CHF {max_by_equity:,.0f}; "
                           f"CHF {max(0.0, (1 - ltv) * min_price - liquid):,.0f} more equity is needed")
    if owner and not refinance_ok:
        reasons.append(f"age {p['age']} is above {m['max_age_refinancing']} for a refinancing review")
    return {
        "gross_income_est": round(gross, 0),
        "max_price_by_income": round(max_by_income, -3),
        "max_price_by_equity": round(max_by_equity, -3),
        "max_price": round(max_price, -3),
        "liquid_equity": round(liquid, 0),
        "purchase_ok": purchase_ok,
        "refinance_ok": refinance_ok,
        "eligible": purchase_ok or refinance_ok,
        "mode": "refinancing" if refinance_ok else "purchase" if purchase_ok else None,
        "reasons": reasons,
        "assumptions": {k: m[k] for k in ("imputed_rate", "maintenance_rate", "max_ltv", "affordability_max_share", "min_property_price")},
    }
