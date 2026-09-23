"""Transaction classification: cache -> rules -> LLM (batched, structured output) -> fallback.

Classification is done once per unique (description, bank category, sign) key,
cached in SQLite, so re-runs are free and deterministic. Manual overrides are
applied per transaction on top.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

from .. import config, db
from ..ingest.schemas import Transaction
from ..llm.client import LLMClient, LLMUnavailable
from . import taxonomy
from .rules import fallback_classify, is_foreign_marker, rule_classify

ProgressFn = Callable[[str], None]

SYSTEM_PROMPT = """You classify Swiss retail bank transactions (descriptions are mostly Swiss German).
For each item return the single best category from the fixed taxonomy, whether the spending
was most likely made abroad or with a foreign merchant (is_foreign), a confidence between 0 and 1,
and a short reason (max 15 words).

Taxonomy (id: meaning):
{taxonomy}

Guidance:
- Positive amounts are income; never give an income item an expense category or vice versa.
- Temporary-agency payouts (Adecco, Randstad) are salary; platform payouts (Uber, Wolt, Fiverr,
  Too Good To Go side jobs) are gig_income; client payments/fees are self_employment_income.
- Payments to other banks' investment platforms (Swissquote, Julius Baer, VIAC) are investments,
  unless explicitly Pillar 3a (then pillar_3a). Private-equity capital calls are investments.
- Holiday rentals (chalet, Ferienwohnung) are travel, not rent. Maintenance of holiday homes is property_costs.
- is_foreign is true only if the merchant or location is clearly outside Switzerland.
- Use confidence below 0.7 when the description is genuinely ambiguous.
"""


def _taxonomy_text() -> str:
    return "\n".join(f"- {cid}: {lab}" for cid, (lab, _k) in taxonomy.CATEGORIES.items())


def classification_key(t: Transaction) -> str:
    sign = "+" if t.amount > 0 else "-"
    raw = f"{t.counterparty.lower()}|{t.source_category}|{sign}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _schema(keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": keys},
                        "category": {"type": "string", "enum": taxonomy.CATEGORY_IDS},
                        "is_foreign": {"type": "boolean"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["key", "category", "is_foreign", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _cache_get(keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i : i + 500]
        rows = db.fetchall(
            f"SELECT * FROM classification_cache WHERE key IN ({','.join('?' * len(chunk))})", tuple(chunk)
        )
        for r in rows:
            out[r["key"]] = {
                "category": r["category"], "is_foreign": bool(r["is_foreign"]), "confidence": r["confidence"],
                "reason": r["reason"], "source": r["source"], "model": r["model"],
            }
    return out


def _cache_put(key: str, res: dict, model: Optional[str], prompt_version: Optional[str]) -> None:
    db.execute(
        """INSERT OR REPLACE INTO classification_cache
           (key, category, is_foreign, confidence, reason, source, model, prompt_version, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (key, res["category"], int(res["is_foreign"]), res["confidence"], res["reason"], res["source"],
         model, prompt_version, db.now_iso()),
    )


def validate_llm_items(items: list[dict], batch_keys: dict[str, str]) -> dict[str, dict]:
    """Keep only well-formed answers for keys we asked about; clamp confidence."""
    out: dict[str, dict] = {}
    for it in items:
        key = batch_keys.get(it.get("key", ""))
        cat = it.get("category")
        if key is None or cat not in taxonomy.CATEGORIES or key in out:
            continue
        out[key] = {
            "category": cat,
            "is_foreign": bool(it.get("is_foreign")),
            "confidence": max(0.0, min(1.0, float(it.get("confidence", 0.5)))),
            "reason": str(it.get("reason", ""))[:200],
            "source": "llm",
        }
    return out


def _sign_consistent(res: dict, amount: float) -> bool:
    k = taxonomy.kind(res["category"])
    return (k == "income") == (amount > 0) or k == "transfer"


def classify_transactions(
    txns: list[Transaction],
    llm: LLMClient,
    progress: ProgressFn = lambda _m: None,
) -> dict[str, dict]:
    """Return {txn_id: classification} for every transaction."""
    s = config.settings()["llm"]
    reps: dict[str, Transaction] = {}
    for t in txns:
        reps.setdefault(classification_key(t), t)

    results: dict[str, dict] = {}
    for k, t in reps.items():
        r = rule_classify(t.description, t.source_category, t.amount)
        if r:
            results[k] = r
    n_rules = len(results)
    cached = _cache_get([k for k in reps if k not in results])
    results.update(cached)
    todo = [k for k in reps if k not in results]
    progress(
        f"{len(reps)} unique descriptions: {n_rules} by rules, {len(cached)} from cache, {len(todo)} need the LLM"
    )

    batch_size = int(s["classify_batch_size"])
    pv = s["prompt_versions"]["classify"]
    system = SYSTEM_PROMPT.format(taxonomy=_taxonomy_text())
    llm_ok = llm.available
    if todo and not llm_ok:
        progress(f"No LLM in this mode: {len(todo)} descriptions categorised from the bank's own category")
    for b in range(0, len(todo), batch_size):
        batch = todo[b : b + batch_size]
        if not llm_ok:
            break
        short = {f"k{i}": k for i, k in enumerate(batch)}
        payload = [
            {
                "key": sk,
                "description": reps[k].description,
                "bank_category": reps[k].source_category,
                "direction": "income" if reps[k].amount > 0 else "outflow",
                "example_amount_chf": round(abs(reps[k].amount), 2),
            }
            for sk, k in short.items()
        ]
        progress(f"Classifying batch {b // batch_size + 1} ({len(batch)} items) with {llm.model_for('fast')}")
        try:
            out = llm.structured(
                purpose="classify", tier="fast", system=system, user={"items": payload},
                schema_name="transaction_classification", schema=_schema(list(short)), prompt_version=pv,
            )
        except LLMUnavailable as exc:
            progress(f"LLM unavailable ({exc}); using fallback rules for the rest")
            llm_ok = False
            break
        for key, res in validate_llm_items(out.get("items", []), short).items():
            if not _sign_consistent(res, reps[key].amount):
                continue  # rejected: contradicts the sign of the amount
            res["is_foreign"] = res["is_foreign"] or is_foreign_marker(reps[key].description)
            results[key] = res
            _cache_put(key, res, llm.model_for("fast"), pv)

    for k in reps:
        if k not in results:
            t = reps[k]
            results[k] = fallback_classify(t.description, t.source_category, t.amount)

    overrides = {
        r["txn_id"]: r["category"]
        for r in db.fetchall("SELECT txn_id, category FROM overrides WHERE client_id=?", (txns[0].client_id,))
    } if txns else {}

    threshold = float(s["confidence_threshold"])
    per_txn: dict[str, dict] = {}
    for t in txns:
        base = dict(results[classification_key(t)])
        if t.id in overrides:
            base.update(category=overrides[t.id], confidence=1.0, source="override", reason="Manually reviewed")
        base["needs_review"] = base["confidence"] < threshold
        per_txn[t.id] = base
    return per_txn


def audit_summary(per_txn: dict[str, dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in per_txn.values():
        out[c["source"]] = out.get(c["source"], 0) + 1
    out["needs_review"] = sum(1 for c in per_txn.values() if c["needs_review"])
    return out


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
