"""Measure classification accuracy on the hand-labelled set (tests/eval/labelled_transactions.csv).

    .venv/bin/python scripts/eval_classifier.py            # rules + bank-category fallback only
    .venv/bin/python scripts/eval_classifier.py openai     # rules + OpenAI for the rest (costs a few cents)
    .venv/bin/python scripts/eval_classifier.py local      # rules + local LLM (LOCAL_LLM_BASE_URL)

Uses a throw-away database, so the LLM is really called (no cache).
"""

from __future__ import annotations

import csv
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.classify.service import classify_transactions  # noqa: E402
from app.ingest.loader import parse_transactions  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402

EVAL = ROOT / "tests" / "eval" / "labelled_transactions.csv"


def load() -> tuple[list, list[dict]]:
    with open(EVAL, encoding="utf-8") as fh:
        labels = list(csv.DictReader(fh))
    text = "date,description,category,amount,currency,balance\n" + "".join(
        f'2026-01-{(i % 28) + 1:02d},"{r["description"]}",{r["bank_category"]},{r["amount"]},CHF,0\n' for i, r in enumerate(labels)
    )
    return parse_transactions(text, "eval", EVAL.name), labels


def evaluate(mode: str = "off") -> dict:
    txns, labels = load()
    with tempfile.TemporaryDirectory() as tmp:
        db.init(Path(tmp) / "eval.db")
        res = classify_transactions(txns, LLMClient(mode, run_id="eval", client_id="eval"))
    by_desc = {t.description: res[t.id] for t in txns}
    hits, fx_hits, sources, mistakes = 0, 0, Counter(), []
    for r in labels:
        got = by_desc[r["description"]]
        sources[got["source"]] += 1
        ok = got["category"] == r["expected_category"]
        hits += ok
        fx_hits += got["is_foreign"] == (r["expected_foreign"] == "true")
        if not ok:
            mistakes.append((r["description"], r["expected_category"], got["category"], got["source"]))
    n = len(labels)
    return {"n": n, "accuracy": hits / n, "foreign_accuracy": fx_hits / n, "sources": dict(sources), "mistakes": mistakes}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "off"
    out = evaluate(mode)
    print(f"mode={mode}  n={out['n']}  category accuracy={out['accuracy']:.1%}  is_foreign accuracy={out['foreign_accuracy']:.1%}")
    print("classified by:", out["sources"])
    for desc, exp, got, src in out["mistakes"]:
        print(f"  {desc:45s} expected {exp:24s} got {got:24s} ({src})")
