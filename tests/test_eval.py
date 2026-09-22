"""Regression guard for the deterministic classifier on the hand-labelled set.
The LLM path is measured with `scripts/eval_classifier.py openai` (not run in CI: it costs money)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eval_classifier import evaluate  # noqa: E402


def test_labelled_set_size():
    assert evaluate("off")["n"] >= 100


def test_rules_and_fallback_accuracy_floor():
    out = evaluate("off")
    assert out["accuracy"] >= 0.80, out["mistakes"]
    assert out["foreign_accuracy"] >= 0.90
