from app import config
from app.ingest.loader import product_index
from app.llm.client import LLMUnavailable
from app.recommend import engine
from test_suitability import ctx

SIGNALS = [
    {"type": "pillar_3a_gap", "summary": "No 3a.", "evidence": ["t1", "t2"], "metrics": {}},
    {"type": "idle_cash", "summary": "Idle.", "evidence": ["t3"], "metrics": {"idle_cash": 30000}},
    {"type": "monthly_surplus", "summary": "Surplus.", "evidence": ["t4"], "metrics": {}},
    {"type": "recurring_income", "summary": "Salary.", "evidence": ["t5"], "metrics": {}},
]
TAX = {"eligible": True, "years_to_retirement": 30, "recommended_monthly": 600, "additional_annual": 7258, "tax_saved_per_year": 1800}


class RankingLLM:
    available = True

    def __init__(self, items=None, fail=False):
        self.items, self.fail = items, fail

    def model_for(self, tier):
        return "fake"

    def structured(self, **kw):
        if self.fail:
            raise LLMUnavailable("down")
        return {"recommendations": self.items, "summary": "ok"}


def c(profile, level=3):
    return ctx(profile, level=level, signals=[s["type"] for s in SIGNALS])


def test_rule_file_only_references_catalog_products():
    catalog = product_index()
    for rule in config.load_yaml("product_rules.yaml")["rules"]:
        assert set(rule["products"]) <= set(catalog), rule["id"]


def test_candidates_follow_signals(profile):
    cands = engine.candidates(SIGNALS, c(profile))
    assert {"ubs-fisca-3a", "ubs-key4-pension-3a", "ubs-key4-smart-investing"} <= set(cands)
    assert "ubs-prepaid-card" not in cands  # no debt signal


def test_llm_cannot_add_products_outside_catalog_or_gate(profile):
    items = [
        {"product_id": "ubs-crypto-moonshot", "rank": 1, "rationale": "x", "cited_signals": ["idle_cash"]},
        {"product_id": "options", "rank": 2, "rationale": "x", "cited_signals": ["idle_cash"]},
        {"product_id": "ubs-fisca-3a", "rank": 1, "rationale": "Save CHF 1800 tax.", "cited_signals": ["pillar_3a_gap", "made_up"]},
    ]
    rec = engine.recommend(SIGNALS, c(profile), TAX, RankingLLM(items))
    ids = [p["product_id"] for b in rec["baskets"] for p in b["products"]]
    assert ids == ["ubs-fisca-3a"]
    assert {r["product_id"] for r in rec["rejected_by_validation"]} == {"ubs-crypto-moonshot", "options"}
    fisca = rec["baskets"][0]["products"][0]
    assert fisca["cited_signals"] == ["pillar_3a_gap"] and fisca["evidence"] == ["t1", "t2"]


def test_all_recommended_products_exist_and_passed_the_gate(profile):
    rec = engine.recommend(SIGNALS, c(profile, level=2), TAX, RankingLLM(fail=True))
    catalog = product_index()
    for b in rec["baskets"]:
        for p in b["products"]:
            assert p["product_id"] in catalog and rec["gate"][p["product_id"]]["passed"]
            assert p["evidence"] or p["profile_basis"], "every recommendation cites its basis"
    hidden = {h["product_id"] for h in rec["hidden"]}
    assert "equities" in hidden and all(h["reasons"] for h in rec["hidden"])


def test_invalid_llm_output_falls_back_to_rules(profile):
    rec = engine.recommend(SIGNALS, c(profile), TAX, RankingLLM([{"product_id": "nope", "rank": 1, "rationale": "", "cited_signals": []}]))
    assert rec["ranked_by"] == "rules" or rec["baskets"]
    assert all(p["ranked_by"] == "rules" for b in rec["baskets"] for p in b["products"])


def test_deterministic_ranking_prefers_fitting_risk(profile):
    rec = engine.recommend(SIGNALS, c(profile, level=4), TAX, RankingLLM(fail=True))
    first = {b["basket"]: b["products"][0]["product_id"] for b in rec["baskets"]}
    assert first["Investment"] == "ubs-key4-smart-investing"
