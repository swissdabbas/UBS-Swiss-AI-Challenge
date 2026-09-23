"""Pillar 3a rules and tax. The three ESTV cases were recorded from the live ESTV calculator
(tax year 2026); run `ESTV_LIVE=1 pytest tests/test_tax.py` to re-verify them against the source."""

import pytest
from conftest import LIVE, base_year

from app.features import cashflow
from app.tax import estv, pillar3a
from app.tax.service import fallback_rate, pillar3a_plan

BASE = dict(self_employment_net=0, pension_income=0, confession="none", children=0)
ESTV_CASES = [
    # name, household, contribution, total tax without 3a, total tax with 3a (CHF, ESTV 2026)
    ("zh_single_employee_100k", dict(BASE, employment_type="employee", gross_salary_est=100000, earned_income_net=88000,
                                     relationship="single", age=40, zip="8001", city="Zürich", canton="ZH"), 7258, 11848, 10013),
    ("be_married_employee_150k_2kids", dict(BASE, employment_type="employee", gross_salary_est=150000, earned_income_net=132000,
                                            relationship="married", age=45, zip="3011", city="Bern", canton="BE", children=2), 7258, 17882, 15918),
    ("zg_single_selfemployed_120k", dict(BASE, employment_type="self_employed", gross_salary_est=0, self_employment_net=120000,
                                         earned_income_net=120000, relationship="single", age=50, zip="6300", city="Zug", canton="ZG"), 24000, 10618, 6497),
]


@pytest.mark.skipif(not LIVE, reason="set ESTV_LIVE=1 to call the ESTV calculator")
@pytest.mark.parametrize("name,household,contribution,tax0,tax1", ESTV_CASES)
def test_estv_live_matches_recorded_cases(name, household, contribution, tax0, tax1):
    r = estv.taxes_for_3a(household, [0, contribution], 2026)
    assert r["taxes"][0]["total"] == tax0
    assert r["taxes"][contribution]["total"] == tax1


@pytest.mark.parametrize("name,household,contribution,tax0,tax1", ESTV_CASES[:2])
def test_offline_table_is_close_to_estv(name, household, contribution, tax0, tax1):
    """The fallback table (pre-computed from ESTV for canton capitals) stays within 10% of the live answer."""
    rate, _ = fallback_rate(household["canton"], household["gross_salary_est"], household["relationship"])
    saved_live = tax0 - tax1
    assert abs(rate * contribution - saved_live) / saved_live < 0.10


def test_caps():
    emp = {"has_pension_fund": True, "earned_income_net": 90000}
    assert pillar3a.cap(emp)[0] == 7258
    se = {"has_pension_fund": False, "earned_income_net": 50000}
    assert pillar3a.cap(se)[0] == 10000
    rich = {"has_pension_fund": False, "earned_income_net": 1_000_000}
    assert pillar3a.cap(rich)[0] == 36288


@pytest.mark.parametrize("age,earned,working,ok", [
    (40, 50000, True, True), (68, 9000, True, True), (71, 9000, True, False), (66, 0, False, False),
])
def test_eligibility(age, earned, working, ok):
    assert pillar3a.eligibility({"age": age, "earned_income_net": earned, "still_working": working})[0] is ok


def test_fallback_interpolation_is_monotone_between_points():
    r1, note = fallback_rate("ZH", 90000, "single")
    r0, _ = fallback_rate("ZH", 80000, "single")
    r2, _ = fallback_rate("ZH", 100000, "single")
    assert r0 <= r1 <= r2 and "Zürich" in note


def test_plan_uses_estv_differences(mk, profile, monkeypatch):
    """Tax saved = ESTV(current contribution) - ESTV(recommended); cumulative over years to 65."""
    def fake(p, contributions, year):
        return {"location": {}, "payload": {}, "taxes": {round(c): {"total": 12000 - 0.25 * c, "marginal_rate": 25.0} for c in contributions}}
    monkeypatch.setattr(estv, "taxes_for_3a", fake)
    monkeypatch.setattr("app.tax.service._retro_saving_estv", lambda *a: 1000.0)
    rows = base_year(mk)
    k = cashflow.kpis(rows, cashflow.monthly(rows))
    plan = pillar3a_plan(profile, k, rows)
    assert plan["recommended_annual"] == 7258
    assert plan["tax_saved_per_year"] == round(0.25 * 7258)
    assert plan["cumulative_tax_saved"] == round(0.25 * 7258 * 30)  # age 35 -> 65
    assert plan["source"].startswith("ESTV")


def test_plan_falls_back_offline(mk, profile, monkeypatch):
    def down(*a):
        raise estv.EstvError("unreachable")
    monkeypatch.setattr(estv, "taxes_for_3a", down)
    rows = base_year(mk)
    k = cashflow.kpis(rows, cashflow.monthly(rows))
    plan = pillar3a_plan(profile, k, rows)
    assert plan["source"].startswith("Rough estimate")
    assert 0.1 < plan["marginal_rate"] < 0.4


def test_recommendation_limited_by_affordability(mk, profile):
    rows = base_year(mk, salary=1200)  # 400 a month free cash flow
    k = cashflow.kpis(rows, cashflow.monthly(rows))
    plan = pillar3a_plan(profile, k, rows)
    assert plan["recommended_annual"] < 7258
    assert plan["recommended_annual"] <= 0.6 * k["annual_free_cash_flow"] + 1
