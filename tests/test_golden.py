"""Golden files: the full deterministic pipeline (no LLM, ESTV offline -> tax tables) on all 16 personas.

Regenerate after an intended change with:  UPDATE_GOLDEN=1 .venv/bin/pytest tests/test_golden.py
and review the diff of tests/golden/*.json before committing.
"""

import json
import os
from pathlib import Path

import pytest

from app import pipeline
from app.ingest.loader import list_profiles
from app.ingest.loader import product_index
from app.suitability import questionnaire

GOLDEN = Path(__file__).parent / "golden"
CLIENTS = [p.client_id for p in list_profiles() if not p.uploaded]


def summarise(client_id: str) -> dict:
    run_id = pipeline.analyse(client_id, "off")
    a = pipeline.latest_run(client_id)["analysis"]
    demo = questionnaire.demo_answers(a["profile"], {s["type"] for s in a["signals"]})
    pipeline.save_answers(client_id, {"profile": {}, **demo})
    pipeline.acknowledge(client_id)
    pipeline.recommend(client_id, "off")
    run = pipeline.latest_run(client_id)
    r, k, tp = run["recommendation"], a["kpis"], a["tax_plan"]
    assert run["run_id"] == run_id
    return {
        "profile": {f: a["profile"][f] for f in ("age", "employment_type", "has_pension_fund", "children", "canton")},
        "kpis": {f: round(k[f]) for f in ("annual_income", "annual_spending", "annual_saving", "trailing_monthly_surplus")},
        "signals": sorted(s["type"] for s in a["signals"]),
        "tax": {f: tp.get(f) for f in ("eligible", "cap", "recommended_annual", "tax_saved_per_year")},
        "risk_profile": r["suitability"]["risk_profile_label"],
        "baskets": {b["basket"]: [p["product_id"] for p in b["products"]] for b in r["baskets"]},
        "hidden": sorted(h["product_id"] for h in r["hidden"]),
        "projections": {n: round(p["final"]["medium"]) for n, p in r["projections"].items()},
    }


@pytest.mark.parametrize("client_id", CLIENTS)
def test_golden(client_id):
    got = summarise(client_id)
    path = GOLDEN / f"{client_id}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert got == json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("client_id", CLIENTS)
def test_recommendations_are_catalog_products_that_passed_the_gate(client_id):
    summarise(client_id)
    r = pipeline.latest_run(client_id)["recommendation"]
    catalog = product_index()
    for b in r["baskets"]:
        for p in b["products"]:
            assert p["product_id"] in catalog
            assert r["gate"][p["product_id"]]["passed"]
            assert p["rationale"] and (p["evidence"] or p["profile_basis"]), "every recommendation states its basis"
    for h in r["hidden"]:
        assert h["reasons"], f"{h['product_id']} hidden without a stated reason"
