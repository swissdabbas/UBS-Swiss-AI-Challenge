import pytest

from app.projection.scenarios import basket_projections, project

FLAT = {"low": 0.0, "medium": 0.0, "high": 0.0, "volatility": 0.0, "fees": 0.0}


def test_zero_return_equals_contributions():
    p = project(1000, 100, 5, FLAT)
    assert p["scenarios"]["medium"][-1] == pytest.approx(1000 + 100 * 60)
    assert p["contributions"][-1] == pytest.approx(1000 + 100 * 60)
    assert p["monte_carlo"] is None


def test_compounding_lump_sum():
    p = project(10000, 0, 10, {"low": 5, "medium": 5, "high": 5, "fees": 0})
    assert p["scenarios"]["medium"][-1] == pytest.approx(10000 * 1.05 ** 10, abs=0.01)


def test_fees_are_deducted():
    gross = project(10000, 100, 10, {"low": 2, "medium": 4, "high": 6, "fees": 0})
    net = project(10000, 100, 10, {"low": 2, "medium": 4, "high": 6, "fees": 1})
    assert net["final"]["medium"] < gross["final"]["medium"]
    assert net["scenarios"]["medium"][-1] == pytest.approx(project(10000, 100, 10, {"low": 3, "medium": 3, "high": 3})["final"]["medium"])


def test_scenarios_are_ordered_and_monte_carlo_is_reproducible():
    r = {"low": 1.5, "medium": 4, "high": 6.5, "volatility": 10, "fees": 1}
    a, b = project(5000, 300, 15, r), project(5000, 300, 15, r)
    assert a["monte_carlo"] == b["monte_carlo"]
    for y in range(16):
        assert a["scenarios"]["low"][y] <= a["scenarios"]["medium"][y] <= a["scenarios"]["high"][y]
        assert a["monte_carlo"]["p10"][y] <= a["monte_carlo"]["p50"][y] <= a["monte_carlo"]["p90"][y]
    # median path is close to the medium scenario
    assert a["monte_carlo"]["p50"][-1] == pytest.approx(a["scenarios"]["medium"][-1], rel=0.05)


def test_basket_contributions_come_from_surplus_and_idle_cash():
    baskets = [
        {"basket": "Pillar 3a", "products": [{"product_id": "ubs-fisca-3a", "name": "Fisca", "holding_period_years": 30,
                                              "returns": {"low": 0.2, "medium": 0.5, "high": 0.8, "fees": 0}}]},
        {"basket": "Investment", "products": [{"product_id": "ubs-key4-smart-investing", "name": "key4", "holding_period_years": 10,
                                               "returns": {"low": 1.5, "medium": 4, "high": 6.5, "volatility": 10, "fees": 0.75}}]},
    ]
    signals = [{"type": "idle_cash", "metrics": {"idle_cash": 10000}}]
    tax = {"eligible": True, "recommended_monthly": 604.83, "additional_annual": 7258, "tax_saved_per_year": 1800}
    out = basket_projections(baskets, {"trailing_monthly_free_cash_flow": 2604.83}, signals, tax)
    assert out["Pillar 3a"]["assumptions"]["monthly"] == pytest.approx(604.83)
    assert out["Pillar 3a"]["tax_saved"]["cumulative"][-1] == 1800 * 30
    assert out["Investment"]["assumptions"]["initial"] == pytest.approx(7000)
    assert out["Investment"]["assumptions"]["monthly"] == pytest.approx((2604.83 - 604.83) * 0.5)
