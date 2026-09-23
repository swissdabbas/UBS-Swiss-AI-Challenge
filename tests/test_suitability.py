import sqlite3

import pytest

from app import db, pipeline
from app.ingest.loader import product_index
from app.suitability.gate import check_product, client_context, risk_tolerance

KPIS = {"annual_income": 90000, "trailing_savings_rate": 0.3, "reserve_months": 6, "balance": 40000}
ALL_BASIC = {c: "basic" for c in ("savings", "funds", "equities", "bonds", "structured", "derivatives",
                                  "alternatives", "fx", "real_estate", "credit")}


def answers(level, horizon=5, knowledge=None):
    return {"risk": {q: level for q in ("objective", "drawdown_reaction", "max_loss", "downturn_experience")} | {"horizon": horizon},
            "knowledge": knowledge or ALL_BASIC}


def ctx(profile, level=4, signals=(), horizon=5, knowledge=None, kpis=KPIS):
    return client_context(profile, kpis, [{"type": s, "evidence": []} for s in signals], answers(level, horizon, knowledge))


def test_tolerance_needs_all_answers():
    assert risk_tolerance({"objective": 3}) is None
    assert risk_tolerance(answers(2)["risk"] | {"horizon": 5}) == 3  # mean of 2,2,2,2,5 = 2.6 -> 3


def test_risk_profile_is_min_of_capacity_and_tolerance(profile):
    c = ctx(profile, level=5, signals=["recurring_income"])
    assert c["risk_profile"] == min(c["risk_capacity"]["score"], c["risk_tolerance"])


def test_debt_caps_capacity(profile):
    c = ctx(profile, level=5, signals=["debt_stress"])
    assert c["risk_capacity"]["score"] == 1 and c["risk_profile"] == 1


def test_gate_blocks_risk_above_profile(profile):
    c = ctx(profile, level=2)
    res = check_product(product_index()["equities"], c)
    assert not res["passed"] and any("exceeds your risk profile" in r for r in res["reasons"])


def test_gate_blocks_missing_knowledge(profile):
    k = ALL_BASIC | {"structured": "basic"}
    c = ctx(profile, level=5, signals=["recurring_income", "high_net_worth"], knowledge=k)
    res = check_product(product_index()["structured-products"], c)
    assert any(ch["type"] == "appropriateness" and not ch["ok"] for ch in res["checks"])


def test_gate_blocks_short_horizon(profile):
    c = ctx(profile, level=4, horizon=1)
    res = check_product(product_index()["ubs-key4-smart-investing"], c)
    assert any("horizon" in r for r in res["reasons"])


def test_gate_blocks_intermediary_and_qualified_only(profile):
    c = ctx(profile, level=5)
    idx = product_index()
    assert not check_product(idx["ubs-transact-intermediaries"], c)["passed"]
    assert any("Qualified investors" in r for r in check_product(idx["alternative-investments"], c)["reasons"])


def test_gate_blocks_3a_and_credit_cards_with_consumer_debt(profile):
    c = ctx(profile, level=3, signals=["debt_stress"])
    idx = product_index()
    assert not check_product(idx["ubs-fisca-3a"], c)["passed"]
    assert not check_product(idx["ubs-classic-credit-card"], c)["passed"]
    assert check_product(idx["ubs-prepaid-card"], c)["passed"]


def test_gate_passes_suitable_product(profile):
    c = ctx(profile, level=3, signals=["recurring_income"])
    res = check_product(product_index()["ubs-key4-smart-investing"], c)
    assert res["passed"], res["reasons"]


def test_acknowledgement_is_stored_and_must_follow_answers():
    pipeline.save_answers("c9", answers(3))
    assert not pipeline.ack_is_current("c9")
    ack = pipeline.acknowledge("c9")
    assert ack["acknowledged_at"] and ack["notice_version"] == "finsa-notice-v1"
    assert pipeline.ack_is_current("c9")


@pytest.mark.parametrize("table", ["llm_audit", "events", "acknowledgements"])
def test_audit_tables_are_append_only(table):
    db.log_event("x", "c1")
    pipeline.save_answers("c1", answers(3))
    pipeline.acknowledge("c1")
    db.execute("INSERT INTO llm_audit (ts, purpose, provider, model, prompt_version, input_sha256, input_json, status) "
               "VALUES ('t','p','openai','m','v','h','{}','ok')")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(f"UPDATE {table} SET id = id")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(f"DELETE FROM {table}")
