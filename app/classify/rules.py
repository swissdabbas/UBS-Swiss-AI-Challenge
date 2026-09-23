"""Rule-based pre-classifier for the obvious cases (saves LLM cost), plus the
source-category fallback used when no LLM is available."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Rule:
    pattern: str
    category: str
    sign: Optional[str] = None                   # "+" income only, "-" outflow only
    sources: Optional[tuple[str, ...]] = None    # restrict to these CSV categories
    confidence: float = 0.97

    def matches(self, desc: str, source: str, amount: float) -> bool:
        if self.sign == "+" and amount <= 0:
            return False
        if self.sign == "-" and amount >= 0:
            return False
        if self.sources and source not in self.sources:
            return False
        return re.search(self.pattern, desc, flags=re.I) is not None


RULES: list[Rule] = [
    # income
    Rule(r"13\.\s*monatslohn|quartalsbonus|\bbonus\b", "bonus", "+"),
    Rule(r"lohnzahlung|\blohn\b|gehalt", "salary", "+"),
    Rule(r"ahv[- ]rente|pensionskasse rente|bvg[- ]rente", "pension_income", "+"),
    Rule(r"alimente", "alimony", "+"),
    Rule(r"dividende|kapitalgewinn|trust distribution|stiftung ausschüttung", "investment_income", "+"),
    Rule(r"kundenzahlung|honorar|auftragszahlung|vorschuss auftraggeber", "self_employment_income", "+"),
    Rule(r"^(uber|uber eats|wolt|fiverr) auszahlung", "gig_income", "+"),
    # housing
    Rule(r"hypothekarzins", "mortgage_interest", "-"),
    Rule(r"\bmiete\b|wohnungsmiete|zimmer miete", "rent", "-", ("Housing",)),
    Rule(r"liegenschaft|hausunterhalt|hauswartung", "property_costs", "-", ("Housing", "Properties")),
    Rule(r"ewz|\bstrom\b|wasserwerk|erdgas", "utilities", "-"),
    Rule(r"swisscom|\bsalt\b|sunrise", "telecom", "-"),
    # pension and savings
    Rule(r"säule 3a|saeule 3a|3a[- ]einzahlung", "pillar_3a", "-"),
    Rule(r"pensionskasse einkauf|pk[- ]einkauf", "pillar_2_buyin", "-"),
    # family
    Rule(r"kindertagesstätte|\bkita\b|babysitter|krippe", "childcare", "-"),
    Rule(r"schulmaterial|privatschule|schulgeld|kinderturnen|klavierunterricht", "school", "-"),
    # insurance and health
    Rule(r"krankenkasse", "health_insurance", "-"),
    Rule(r"apotheke|arzt selbstbehalt|kinderarzt", "healthcare", "-"),
    # debt and fees
    Rule(r"konsumkredit|kreditkarte mindestzahlung|studienkredit", "debt_repayment", "-"),
    Rule(r"mahngebühr|überziehungszins", "bank_fees", "-"),
    Rule(r"steuerverwaltung|steueramt", "taxes", "-"),
    Rule(r"bancomat|geldautomat|\batm\b", "cash", "-"),
    Rule(r"twint transfer|überweisung an|dauerauftrag", "transfers", "-"),
    # everyday
    Rule(r"^(migros|coop|denner|aldi suisse|lidl schweiz|volg)$", "groceries", "-", ("Groceries",)),
    Rule(r"netflix|spotify|disney\+|icloud", "subscriptions", "-"),
    Rule(r"autoleasing|tankstelle|migrol", "car", "-", ("Transportation",)),
]

# Obvious "outside Switzerland" markers; the LLM decides the rest.
FOREIGN_MARKERS = (
    r"barcelona|lissabon|amsterdam|mallorca|malediven|côte d'azur|cote d'azur|cap ferrat|monaco|london|"
    r"andalusien|gardasee|frankreich|europapark|interrail|ryanair|kreuzfahrt|paris|milano|mailand|new york"
)

# CSV category -> (taxonomy category, confidence) when no LLM is available.
SOURCE_FALLBACK: dict[str, tuple[str, float]] = {
    "Food": ("dining", 0.85),
    "Groceries": ("groceries", 0.9),
    "Income": ("other_income", 0.4),
    "Utilities": ("utilities", 0.85),
    "Phone/Utilities": ("telecom", 0.75),
    "Transportation": ("transport", 0.7),
    "Housing": ("property_costs", 0.5),
    "Entertainment": ("entertainment", 0.85),
    "Shopping": ("shopping", 0.85),
    "Healthcare": ("healthcare", 0.85),
    "Health Insurance": ("health_insurance", 0.9),
    "Subscriptions": ("subscriptions", 0.9),
    "Software": ("subscriptions", 0.8),
    "Travel": ("travel", 0.85),
    "Misc": ("other", 0.4),
    "Debt Repayment": ("debt_repayment", 0.9),
    "Business Expense": ("business_expense", 0.9),
    "Coworking": ("business_expense", 0.75),
    "Retirement": ("pillar_3a", 0.5),
    "Insurance": ("insurance", 0.8),
    "Kids/School": ("school", 0.85),
    "Childcare": ("childcare", 0.9),
    "Investing": ("investments", 0.75),
    "Alternative Investments": ("investments", 0.8),
    "Family Office": ("wealth_fees", 0.85),
    "Private Banking": ("wealth_fees", 0.85),
    "Staff": ("household_staff", 0.9),
    "Properties": ("property_costs", 0.8),
    "Philanthropy": ("gifts_donations", 0.85),
    "Gifts": ("gifts_donations", 0.9),
    "Fees": ("bank_fees", 0.85),
    "Taxes": ("taxes", 0.9),
    "Yacht": ("luxury", 0.85),
    "Private Jet": ("luxury", 0.75),
    "Luxury Goods": ("luxury", 0.85),
}


def is_foreign_marker(desc: str) -> bool:
    return re.search(FOREIGN_MARKERS, desc, flags=re.I) is not None


def rule_classify(desc: str, source: str, amount: float) -> Optional[dict]:
    for rule in RULES:
        if rule.matches(desc, source, amount):
            return {
                "category": rule.category,
                "is_foreign": is_foreign_marker(desc),
                "confidence": rule.confidence,
                "reason": f"Rule: description matches '{rule.pattern.split('|')[0]}'",
                "source": "rule",
            }
    return None


def fallback_classify(desc: str, source: str, amount: float) -> dict:
    if amount > 0:
        cat, conf = ("other_income", 0.4) if source != "Income" else SOURCE_FALLBACK["Income"]
    else:
        cat, conf = SOURCE_FALLBACK.get(source, ("other", 0.3))
    return {
        "category": cat,
        "is_foreign": is_foreign_marker(desc),
        "confidence": conf,
        "reason": f"Fallback: mapped from bank category '{source}'",
        "source": "fallback",
    }
