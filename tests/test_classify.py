from app import db
from app.classify.rules import fallback_classify, rule_classify
from app.classify.service import classify_transactions, validate_llm_items
from app.ingest.loader import parse_transactions
from app.llm.client import LLMUnavailable

CSV = """date,description,category,amount,currency,balance
2026-01-25,Firma AG Lohnzahlung,Income,6000,CHF,6000
2026-01-26,Adecco Temporärbüro Auszahlung,Income,900,CHF,6900
2026-01-27,Hotel Barcelona,Travel,-300,CHF,6600
2026-01-28,Coop,Groceries,-80,CHF,6520
2026-02-02,Adecco Temporärbüro Auszahlung,Income,850,CHF,7370
"""


class FakeLLM:
    """Stands in for LLMClient; answers every item with a fixed category."""

    def __init__(self, category="salary", confidence=0.9, fail=False):
        self.calls = 0
        self.category, self.confidence, self.fail = category, confidence, fail
        self.available = True

    def model_for(self, tier):
        return "fake-model"

    def structured(self, *, user, **kw):
        self.calls += 1
        if self.fail:
            raise LLMUnavailable("down")
        return {"items": [{"key": it["key"], "is_foreign": False, "confidence": self.confidence, "reason": "fake",
                           "category": self.category if it["direction"] == "income" or self.category != "salary" else "travel"}
                          for it in user["items"]]}


def txns():
    return parse_transactions(CSV, "c1", "c1.csv")


def test_rules_handle_obvious_cases():
    assert rule_classify("Firma AG Lohnzahlung", "Income", 6000)["category"] == "salary"
    assert rule_classify("Kindertagesstätte", "Childcare", -100)["category"] == "childcare"
    assert rule_classify("VIAC Säule 3a Einzahlung", "Investing", -300)["category"] == "pillar_3a"
    assert rule_classify("Chalet Miete Verbier", "Travel", -2000) is None  # holiday rental is not rent
    assert rule_classify("Hausangestellte Lohn", "Staff", -4000) is None  # outgoing wage is not salary


def test_fallback_uses_bank_category_with_low_confidence_for_ambiguous():
    f = fallback_classify("Bancomat Bezug", "Misc", -100)
    assert f["source"] == "fallback" and f["confidence"] < 0.7


def test_llm_batch_is_cached_and_not_called_twice():
    llm = FakeLLM()
    first = classify_transactions(txns(), llm)
    assert llm.calls == 1
    adecco = [t for t in txns() if "Adecco" in t.description]
    assert all(first[t.id]["category"] == "salary" and first[t.id]["source"] == "llm" for t in adecco)
    second = classify_transactions(txns(), llm)
    assert llm.calls == 1, "second run must be served from the cache"
    assert {k: v["category"] for k, v in first.items()} == {k: v["category"] for k, v in second.items()}


def test_llm_answer_contradicting_the_sign_is_rejected():
    llm = FakeLLM(category="groceries")  # an expense category for an income item
    res = classify_transactions(txns(), llm)
    adecco = next(t for t in txns() if "Adecco" in t.description)
    assert res[adecco.id]["source"] == "fallback"


def test_llm_failure_falls_back_to_rules():
    res = classify_transactions(txns(), FakeLLM(fail=True))
    assert all(v["source"] in ("rule", "fallback") for v in res.values())


def test_validate_llm_items_drops_unknown_keys_and_categories():
    out = validate_llm_items(
        [{"key": "k0", "category": "salary", "confidence": 3, "is_foreign": False, "reason": "x"},
         {"key": "k9", "category": "salary", "confidence": 0.9, "is_foreign": False, "reason": "x"},
         {"key": "k1", "category": "crypto_gambling", "confidence": 0.9, "is_foreign": False, "reason": "x"}],
        {"k0": "a", "k1": "b"},
    )
    assert list(out) == ["a"] and out["a"]["confidence"] == 1.0


def test_manual_override_wins_and_clears_review():
    t = txns()
    db.execute("INSERT INTO overrides (client_id, txn_id, category, created_at) VALUES (?,?,?,?)",
               ("c1", t[2].id, "entertainment", db.now_iso()))
    res = classify_transactions(t, FakeLLM())
    assert res[t[2].id]["category"] == "entertainment"
    assert res[t[2].id]["source"] == "override" and not res[t[2].id]["needs_review"]


def test_low_confidence_needs_review():
    res = classify_transactions(txns(), FakeLLM(confidence=0.5))
    adecco = next(t for t in txns() if "Adecco" in t.description)
    assert res[adecco.id]["needs_review"]
