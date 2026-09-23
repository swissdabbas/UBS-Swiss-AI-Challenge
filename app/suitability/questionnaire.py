"""Client-facing questionnaire (served to the UI as JSON) and the FinSA notice."""

from __future__ import annotations

import hashlib

RISK_QUESTIONS = [
    {"id": "objective", "text": "What is your main goal for money you invest?", "options": [
        ["Keep it safe, even if it barely grows", 1], ["Earn a small, regular income", 2],
        ["Balance growth and safety", 3], ["Grow it over the long term", 4],
        ["Maximise growth, accepting large swings", 5]]},
    {"id": "drawdown_reaction", "text": "Your investments fall 20% within a year. What do you do?", "options": [
        ["Sell everything", 1], ["Sell part of it", 2], ["Wait and hold", 3], ["Buy a little more", 4], ["Buy a lot more", 5]]},
    {"id": "max_loss", "text": "What is the largest loss in one year you could live with?", "options": [
        ["Up to 3%", 1], ["Up to 10%", 2], ["Up to 15%", 3], ["Up to 25%", 4], ["More than 25%", 5]]},
    {"id": "horizon", "text": "When will you probably need this money?", "options": [
        ["Within 2 years", 1], ["In 2 to 5 years", 2], ["In 5 to 10 years", 3], ["In 10 to 15 years", 4], ["In more than 15 years", 5]]},
    {"id": "downturn_experience", "text": "Have you held investments through a market downturn?", "options": [
        ["I have never invested", 1], ["I invested, but never through a downturn", 2], ["Once, and it made me uneasy", 3],
        ["Yes, and I stayed calm", 4], ["Several times, and I used it to buy more", 5]]},
]

HORIZON_YEARS = {1: 1, 2: 3, 3: 7, 4: 12, 5: 20}

KNOWLEDGE_CATEGORIES = [
    ["savings", "Savings and pension accounts"],
    ["funds", "Investment funds and ETFs"],
    ["equities", "Shares"],
    ["bonds", "Bonds"],
    ["structured", "Structured products"],
    ["derivatives", "Options and futures"],
    ["alternatives", "Alternative investments (hedge funds, private equity, commodities)"],
    ["fx", "Foreign-exchange trading"],
    ["real_estate", "Real-estate investments"],
    ["credit", "Loans secured on investments (Lombard)"],
]
KNOWLEDGE_LEVELS = [["none", "No knowledge", 0], ["basic", "I understand how it works", 1],
                    ["experienced", "I understand it and have invested in it in the last 3 years", 2]]
LEVEL_RANK = {k: r for k, _l, r in KNOWLEDGE_LEVELS}

PROFILE_FIELDS = [
    {"id": "age", "label": "Age", "type": "number"},
    {"id": "marital_status", "label": "Marital status", "type": "select",
     "options": ["single", "married", "divorced", "widowed"]},
    {"id": "children", "label": "Children in your household", "type": "number"},
    {"id": "zip", "label": "Postcode", "type": "text"},
    {"id": "city", "label": "Municipality", "type": "text"},
    {"id": "canton", "label": "Canton", "type": "select",
     "options": ["AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE", "NW", "OW", "SG", "SH",
                 "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH"]},
    {"id": "confession", "label": "Church tax", "type": "select",
     "options": ["none", "reformed", "roman_catholic", "christ_catholic", "other"]},
    {"id": "employment_type", "label": "Employment", "type": "select",
     "options": ["employee", "self_employed", "retired", "independent_means", "unknown"]},
    {"id": "has_pension_fund", "label": "Member of a pension fund (2nd pillar)", "type": "bool"},
    {"id": "is_student", "label": "Student", "type": "bool"},
    {"id": "other_assets", "label": "Financial assets held elsewhere (CHF)", "type": "number"},
]

NOTICE_VERSION = "finsa-notice-v1"
NOTICE_TEXT = """Information under the Swiss Financial Services Act (FinSA)

1. Nature of this service. The following recommendations are personal investment advice based on the information
   you provided in this questionnaire and on the transactions in your account. They are not an offer and do not
   replace a conversation with an adviser.
2. Suitability and appropriateness. We only show products that match your risk profile, investment horizon and
   financial situation (suitability), and for which you have declared sufficient knowledge and experience
   (appropriateness). Products that fail these checks are listed separately with the reason.
3. Risks. Investments can lose value. Past performance and the illustrative return scenarios (low, medium, high)
   shown are no guarantee of future results. Projections use assumptions that are displayed next to each chart.
4. Costs. Product costs shown are indicative. The binding costs are in the product documentation and price brochure.
5. Your information. Please tell us if your situation changes; the advice is only as good as the information it
   is based on. Incomplete or incorrect information can lead to unsuitable recommendations.
6. Automated analysis. Your transactions are categorised with automated tools; items with low confidence are
   marked for your review. All calculations (tax, projections, eligibility) are deterministic and reproducible.
7. Complaints. You may refer disputes to the Swiss Banking Ombudsman, an independent mediation body.
"""


def notice_sha256() -> str:
    return hashlib.sha256(NOTICE_TEXT.encode()).hexdigest()


def spec() -> dict:
    return {
        "risk_questions": RISK_QUESTIONS,
        "knowledge_categories": KNOWLEDGE_CATEGORIES,
        "knowledge_levels": KNOWLEDGE_LEVELS,
        "profile_fields": PROFILE_FIELDS,
        "notice": {"version": NOTICE_VERSION, "text": NOTICE_TEXT, "sha256": notice_sha256()},
    }


def demo_answers(profile: dict, signal_types: set[str]) -> dict:
    """Plausible answers for the demo button; a real client fills the form themselves."""
    age = profile["age"]
    if "debt_stress" in signal_types or "spending_exceeds_income" in signal_types:
        risk, know = 2, "none"
    elif age >= 63 or profile["employment_type"] == "retired":
        risk, know = 2, "basic"
    elif "high_net_worth" in signal_types:
        risk, know = 4, "experienced"
    elif age < 35:
        risk, know = 4, "basic"
    else:
        risk, know = 3, "basic"
    horizon = 5 if age < 40 else 4 if age < 52 else 3 if age < 63 else 2
    answers = {q["id"]: risk for q in RISK_QUESTIONS}
    answers["horizon"] = horizon
    knowledge = {c: know for c, _ in KNOWLEDGE_CATEGORIES}
    knowledge["savings"] = "experienced"
    if know != "experienced":
        for c in ("structured", "derivatives", "alternatives", "fx", "credit"):
            knowledge[c] = "none"
    if "external_investments" in signal_types:
        knowledge["funds"] = knowledge["equities"] = "experienced"
    return {"risk": answers, "knowledge": knowledge}
