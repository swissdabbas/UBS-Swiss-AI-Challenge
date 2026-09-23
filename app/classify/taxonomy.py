"""Fixed transaction taxonomy. `kind` drives the cash-flow maths:

income   - counts towards income
expense  - counts towards spending (consumption)
saving   - money set aside (3a, investments); excluded from spending, counted as saved
transfer - neutral
"""

from __future__ import annotations

CATEGORIES: dict[str, tuple[str, str]] = {
    # income
    "salary": ("Salary", "income"),
    "bonus": ("Bonus / 13th salary", "income"),
    "pension_income": ("Pension (AHV / pension fund)", "income"),
    "self_employment_income": ("Self-employment income", "income"),
    "gig_income": ("Gig / platform income", "income"),
    "investment_income": ("Investment income", "income"),
    "alimony": ("Alimony received", "income"),
    "other_income": ("Other income", "income"),
    # housing
    "rent": ("Rent", "expense"),
    "mortgage_interest": ("Mortgage interest", "expense"),
    "property_costs": ("Property costs", "expense"),
    "utilities": ("Utilities", "expense"),
    "telecom": ("Phone & internet", "expense"),
    # insurance and health
    "health_insurance": ("Health insurance", "expense"),
    "insurance": ("Other insurance", "expense"),
    "healthcare": ("Healthcare", "expense"),
    # family
    "childcare": ("Childcare", "expense"),
    "school": ("School & kids", "expense"),
    # living
    "groceries": ("Groceries", "expense"),
    "dining": ("Restaurants & cafés", "expense"),
    "transport": ("Public transport", "expense"),
    "car": ("Car (fuel, leasing)", "expense"),
    "travel": ("Travel", "expense"),
    "shopping": ("Shopping", "expense"),
    "entertainment": ("Leisure", "expense"),
    "subscriptions": ("Subscriptions & software", "expense"),
    "gifts_donations": ("Gifts & donations", "expense"),
    "luxury": ("Luxury & collectibles", "expense"),
    "household_staff": ("Household staff", "expense"),
    "wealth_fees": ("Wealth management fees", "expense"),
    "business_expense": ("Business expenses", "expense"),
    "debt_repayment": ("Debt repayment", "expense"),
    "bank_fees": ("Bank fees & penalties", "expense"),
    "taxes": ("Taxes", "expense"),
    "cash": ("Cash withdrawals", "expense"),
    "transfers": ("Transfers & P2P payments", "expense"),
    # saving
    "investments": ("Investments", "saving"),
    "pillar_3a": ("Pillar 3a", "saving"),
    "pillar_2_buyin": ("Pension fund buy-in", "saving"),
    "other": ("Other", "expense"),
}

CATEGORY_IDS = list(CATEGORIES)
EARNED_INCOME = {"salary", "bonus", "self_employment_income", "gig_income"}


def label(cat: str) -> str:
    return CATEGORIES.get(cat, (cat, "expense"))[0]


def kind(cat: str) -> str:
    return CATEGORIES.get(cat, (cat, "expense"))[1]
