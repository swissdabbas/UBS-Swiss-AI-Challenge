"""Projections per basket: low / medium / high expected-return scenarios (from config/products.yaml)
plus a seeded Monte Carlo 10th-90th percentile band. Running costs are deducted from returns."""

from __future__ import annotations

import math

import numpy as np

from .. import config


def _scenario(initial: float, monthly: float, years: int, annual_net: float) -> list[float]:
    r = (1 + annual_net) ** (1 / 12) - 1
    v, out = initial, [round(initial, 2)]
    for m in range(1, years * 12 + 1):
        v = v * (1 + r) + monthly
        if m % 12 == 0:
            out.append(round(v, 2))
    return out


def project(initial: float, monthly: float, years: float, returns: dict, seed: int | None = None, paths: int | None = None) -> dict:
    s = config.settings()["projection"]
    years = max(1, int(math.ceil(years)))
    fees = returns.get("fees", 0.0) / 100
    nets = {k: returns[k] / 100 - fees for k in ("low", "medium", "high")}
    lines = {k: _scenario(initial, monthly, years, v) for k, v in nets.items()}
    contributions = [round(initial + monthly * 12 * y, 2) for y in range(years + 1)]

    mc = None
    vol = returns.get("volatility", 0.0) / 100
    if vol > 0:
        rng = np.random.default_rng(int(seed if seed is not None else s["seed"]))
        n = int(paths or s["mc_paths"])
        mu = math.log(1 + nets["medium"]) / 12
        sigma = vol / math.sqrt(12)
        shocks = rng.normal(mu, sigma, size=(n, years * 12))
        growth = np.exp(shocks)
        v = np.full(n, float(initial))
        yearly = [np.full(n, float(initial))]
        for m in range(years * 12):
            v = v * growth[:, m] + monthly
            if (m + 1) % 12 == 0:
                yearly.append(v.copy())
        arr = np.stack(yearly, axis=1)
        mc = {q: [round(float(x), 2) for x in np.percentile(arr, pct, axis=0)] for q, pct in (("p10", 10), ("p50", 50), ("p90", 90))}
        mc["paths"] = n
    final = {k: lines[k][-1] for k in lines}
    return {
        "years": list(range(years + 1)),
        "contributions": contributions,
        "scenarios": lines,
        "monte_carlo": mc,
        "final": final,
        "total_contributed": contributions[-1],
        "assumptions": {
            "initial": round(initial, 2), "monthly": round(monthly, 2), "years": years,
            "return_low_pct": returns["low"], "return_medium_pct": returns["medium"], "return_high_pct": returns["high"],
            "volatility_pct": returns.get("volatility", 0.0), "running_costs_pct": returns.get("fees", 0.0),
            "note": "Illustrative demo assumptions, not UBS figures; returns shown after running costs.",
        },
    }


def basket_projections(baskets: list[dict], kpis: dict, signals: list[dict], tax_plan: dict) -> dict:
    """Decide contributions per basket from the detected surplus and idle cash, then project."""
    s = config.settings()["projection"]
    by_type = {sg["type"]: sg for sg in signals}
    idle = by_type["idle_cash"]["metrics"]["idle_cash"] if "idle_cash" in by_type else 0.0
    free_monthly = max(0.0, kpis["trailing_monthly_free_cash_flow"])
    extra_3a_monthly = (tax_plan.get("additional_annual", 0.0) / 12) if tax_plan.get("eligible") else 0.0
    after_3a = max(0.0, free_monthly - extra_3a_monthly)
    names = {b["basket"] for b in baskets}
    invest_share = float(s["idle_cash_share_to_invest"]) if "Investment" in names else 0.0

    out = {}
    for b in baskets:
        lead = next((p for p in b["products"] if p.get("returns") and p.get("holding_period_years")), None)
        if not lead:
            continue
        name = b["basket"]
        if name == "Pillar 3a":
            initial, monthly = 0.0, tax_plan.get("recommended_monthly", 0.0)
        elif name == "Investment":
            initial, monthly = idle * invest_share, after_3a * float(s["investment_share_of_free_surplus"])
        elif name == "Savings":
            initial, monthly = idle * (1 - invest_share), after_3a * float(s["savings_share_of_free_surplus"])
        else:
            continue
        proj = project(initial, monthly, lead["holding_period_years"], lead["returns"])
        proj["product_id"] = lead["product_id"]
        proj["product_name"] = lead["name"]
        if name == "Pillar 3a" and tax_plan.get("eligible"):
            per_year = tax_plan.get("tax_saved_per_year", 0.0)
            proj["tax_saved"] = {"per_year": per_year,
                                 "cumulative": [round(per_year * y, 0) for y in proj["years"]],
                                 "source": tax_plan.get("source")}
        out[name] = proj
    return out
