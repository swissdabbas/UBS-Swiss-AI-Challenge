"""One test (or more) per signal, on small synthetic transaction sets."""

from datetime import date, timedelta

from conftest import base_year, monthly_dates

from app.features import cashflow, signals


def detect(rows, profile):
    months = cashflow.monthly(rows)
    kpis = cashflow.kpis(rows, months)
    flows = cashflow.recurring_flows(rows)
    return {s["type"]: s for s in signals.detect_all(rows, kpis, months, flows, profile)}, kpis


def test_recurring_income_monthly_salary(mk, profile):
    rows = base_year(mk)
    sig, _ = detect(rows, profile)
    s = sig["recurring_income"]
    assert s["metrics"]["flows"][0]["cadence"] == "monthly"
    assert all(e.startswith("t") for e in s["evidence"]) and len(s["evidence"]) == 13


def test_irregular_income_is_not_recurring(mk, profile):
    rows = [mk(date(2025, 1, 1) + timedelta(days=d), "Uber Auszahlung", 100 + 37 * (d % 5), "gig_income")
            for d in (0, 3, 17, 22, 45, 49, 80, 81, 130)]
    rows[-1].balance = 100
    sig, _ = detect(rows, profile | {"employment_type": "self_employed"})
    assert "recurring_income" not in sig


def test_biweekly_salary_is_recurring(mk, profile):
    rows = [mk(date(2025, 1, 3) + timedelta(days=14 * i), "Spital Lohnzahlung", 2000, "salary") for i in range(26)]
    rows[-1].balance = 5000
    sig, _ = detect(rows, profile)
    assert sig["recurring_income"]["metrics"]["flows"][0]["cadence"] == "biweekly"
    assert "salary_jump" not in sig, "two payments in one month must not look like a raise"


def test_monthly_surplus_and_savings_rate(mk, profile):
    sig, k = detect(base_year(mk, salary=6000), profile)
    assert k["trailing_monthly_surplus"] == 5200  # 6000 - 800 groceries
    assert abs(k["trailing_savings_rate"] - 5200 / 6000) < 1e-3
    assert "monthly_surplus" in sig and "spending_exceeds_income" not in sig


def test_spending_exceeds_income(mk, profile):
    sig, _ = detect(base_year(mk, salary=500), profile)
    assert "spending_exceeds_income" in sig and "monthly_surplus" not in sig


def test_idle_cash_above_reserve(mk, profile):
    sig, k = detect(base_year(mk, balance_end=20000), profile)
    reserve = 3 * k["monthly_spending"]
    assert abs(sig["idle_cash"]["metrics"]["idle_cash"] - (20000 - reserve)) < 1
    sig2, _ = detect(base_year(mk, balance_end=1500), profile)
    assert "idle_cash" not in sig2


def test_foreign_spend(mk, profile):
    extra = [mk(date(2025, 6, 1), "Hotel Barcelona", -1500, "travel", is_foreign=True),
             mk(date(2025, 7, 1), "Airbnb Amsterdam", -900, "travel", is_foreign=True)]
    sig, _ = detect(base_year(mk, extra=extra), profile)
    s = sig["foreign_spend"]
    assert s["metrics"]["volume"] == 2400 and len(s["evidence"]) == 2


def test_recurring_rent_with_jittered_dates(mk, profile):
    rows = base_year(mk)
    for i, d in enumerate(monthly_dates("2025-01", 13, day=5)):
        rows.append(mk(d + timedelta(days=(i * 7) % 20), "Wohnungsmiete", -1500, "rent"))
    rows.sort(key=lambda r: (r.date, r.id))
    rows[-1].balance = 10000
    sig, _ = detect(rows, profile)
    assert sig["recurring_rent"]["metrics"]["monthly_rent"] == 1500


def test_family(mk, profile):
    extra = [mk(date(2025, m, 10), "Kindertagesstätte", -400, "childcare") for m in (2, 3, 4)]
    sig, _ = detect(base_year(mk, extra=extra), profile)
    assert sig["family"]["metrics"]["childcare_annual"] == 1200


def test_pillar3a_missing_and_below_max(mk, profile):
    sig, _ = detect(base_year(mk), profile)
    assert sig["pillar_3a_gap"]["metrics"]["status"] == "missing"
    extra = [mk(d, "Säule 3a Einzahlung", -300, "pillar_3a") for d in monthly_dates("2025-02", 12, day=1)]
    sig, _ = detect(base_year(mk, extra=extra), profile)
    assert sig["pillar_3a_gap"]["metrics"]["paid_12m"] == 3600
    assert sig["pillar_3a_gap"]["metrics"]["gap"] == 7258 - 3600


def test_pillar3a_not_flagged_when_maxed_or_ineligible(mk, profile):
    extra = [mk(date(2025, 12, 20), "Säule 3a Einzahlung", -7258, "pillar_3a")]
    sig, _ = detect(base_year(mk, extra=extra), profile)
    assert "pillar_3a_gap" not in sig
    retiree = profile | {"age": 70, "earned_income_net": 0.0, "still_working": False, "employment_type": "retired"}
    sig, _ = detect(base_year(mk), retiree)
    assert "pillar_3a_gap" not in sig


def test_pillar3a_self_employed_large_cap(mk, profile):
    p = profile | {"employment_type": "self_employed", "has_pension_fund": False, "earned_income_net": 100000.0}
    sig, _ = detect(base_year(mk), p)
    assert sig["pillar_3a_gap"]["metrics"]["cap"] == 20000


def test_salary_jump(mk, profile):
    rows = [mk(d, "Firma AG Lohnzahlung", 6000 if i < 7 else 7000, "salary") for i, d in enumerate(monthly_dates("2025-01", 13))]
    rows[-1].balance = 1000
    sig, _ = detect(rows, profile)
    assert abs(sig["salary_jump"]["metrics"]["change"] - 1 / 6) < 1e-3


def test_no_salary_jump_for_irregular_pay(mk, profile):
    amounts = [636, 657, 767, 1382, 350, 365, 352, 579, 470, 480]
    rows = [mk(d, "Randstad Lohnzahlung", a, "salary") for d, a in zip(monthly_dates("2025-01", 10), amounts)]
    rows[-1].balance = 1000
    sig, _ = detect(rows, profile)
    assert "salary_jump" not in sig


def test_new_employer(mk, profile):
    old = [mk(d, "Alt AG Lohnzahlung", 6000, "salary") for d in monthly_dates("2025-01", 6)]
    new = [mk(d, "Neu AG Lohnzahlung", 6500, "salary") for d in monthly_dates("2025-07", 7)]
    rows = sorted(old + new, key=lambda r: r.date)
    rows[-1].balance = 1000
    sig, _ = detect(rows, profile)
    s = sig["new_employer"]
    assert s["metrics"]["new_payer"] == "Neu AG" and s["metrics"]["previous_payer"] == "Alt AG"


def test_move_new_landlord_and_mover(mk, profile):
    rows = base_year(mk)
    rows += [mk(d, "Alte Wohnung Miete", -1500, "rent", counterparty="Verwaltung A") for d in monthly_dates("2025-01", 6, day=1)]
    rows += [mk(d, "Neue Wohnung Miete", -1800, "rent", counterparty="Verwaltung B") for d in monthly_dates("2025-07", 7, day=1)]
    rows.append(mk(date(2025, 6, 28), "Umzugsfirma Muster", -1200, "other"))
    rows.sort(key=lambda r: (r.date, r.id))
    rows[-1].balance = 5000
    sig, _ = detect(rows, profile)
    assert len(sig["move"]["metrics"]["reasons"]) == 2


def test_debt_stress(mk, profile):
    extra = [mk(date(2025, m, 15), "Konsumkredit Rate", -150, "debt_repayment") for m in (2, 3, 4, 5)]
    extra.append(mk(date(2025, 5, 20), "Mahngebühr", -30, "bank_fees"))
    sig, _ = detect(base_year(mk, extra=extra), profile)
    assert sig["debt_stress"]["metrics"]["count"] == 4 and len(sig["debt_stress"]["evidence"]) == 5


def test_existing_mortgage_and_external_investments(mk, profile):
    extra = [mk(d, "Hypothekarzins", -1800, "mortgage_interest") for d in monthly_dates("2025-02", 12, day=3)]
    extra += [mk(d, "Swissquote Einzahlung", -500, "investments") for d in monthly_dates("2025-02", 3, day=4)]
    sig, _ = detect(base_year(mk, extra=extra), profile)
    assert sig["existing_mortgage"]["metrics"]["annual_interest"] == 21600
    assert sig["external_investments"]["metrics"]["providers"] == ["Swissquote Einzahlung"]


def test_retired(mk, profile):
    rows = [mk(d, "AHV Rente", 2100, "pension_income") for d in monthly_dates("2025-01", 13)]
    rows[-1].balance = 1000
    p = profile | {"age": 70, "employment_type": "retired", "earned_income_net": 0.0, "still_working": False}
    sig, _ = detect(rows, p)
    assert "retired" in sig and "pillar_3a_gap" not in sig


def test_every_signal_has_resolvable_evidence(mk, profile):
    extra = [mk(date(2025, 6, 1), "Hotel Barcelona", -1500, "travel", is_foreign=True)]
    rows = base_year(mk, rent=1500, extra=extra)
    sig, _ = detect(rows, profile)
    ids = {r.id for r in rows}
    for s in sig.values():
        assert s["evidence"] and set(s["evidence"]) <= ids, s["type"]
