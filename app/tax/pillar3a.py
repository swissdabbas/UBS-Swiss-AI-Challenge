"""Pillar 3a eligibility and contribution caps (numbers from config/settings.yaml)."""

from __future__ import annotations

from .. import config


def eligibility(p: dict) -> tuple[bool, str]:
    """3a needs AHV-liable earned income; allowed up to 5 years past reference age if still working."""
    c = config.settings()["pillar3a"]
    if p["earned_income_net"] <= 0:
        return False, "No earned (AHV-liable) income detected; Pillar 3a requires income from work."
    limit = c["retirement_age"] + (c["max_work_extension_years"] if p["still_working"] else 0)
    if p["age"] >= limit:
        return False, f"Age {p['age']} is above the Pillar 3a contribution limit of {limit}."
    return True, "Earned income from work detected."


def cap(p: dict) -> tuple[float, str]:
    """Maximum annual 3a contribution for this person."""
    c = config.settings()["pillar3a"]
    if p["has_pension_fund"]:
        return float(c["max_with_pension_fund"]), "employee with pension fund (small cap)"
    big = float(c["max_without_pension_fund"])
    share = float(c["self_employed_income_share"])
    amount = min(big, share * max(0.0, p["earned_income_net"]))
    return round(amount, 0), f"no pension fund: {share:.0%} of net earned income, max CHF {big:,.0f}"
