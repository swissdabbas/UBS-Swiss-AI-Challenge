"""Signals -> candidate products (YAML rules) -> FinSA gate -> ranking (LLM or deterministic).

The LLM can only pick product ids from an enum of gate-approved candidates and cite
signal types from an enum of detected signals; its output is validated again here.
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..ingest.loader import product_index
from ..ingest.schemas import Product
from ..llm.client import LLMClient, LLMUnavailable
from ..suitability.gate import check_product

BASKETS = ["Pillar 3a", "Investment", "Savings", "Cards", "FX", "Mortgage & credit", "Everyday banking"]
MAX_EVIDENCE = 12


def _predicate_ok(pred: Any, ctx: dict) -> bool:
    p = ctx["profile"]
    if pred == "stable_employment_income":
        return "recurring_income" in ctx["signal_types"] and p["employment_type"] == "employee"
    if isinstance(pred, dict):
        (name, value), = pred.items()
        if name == "age_max":
            return p["age"] <= int(value)
        if name == "age_min":
            return p["age"] >= int(value)
    raise ValueError(f"unknown predicate in product_rules.yaml: {pred!r}")


def _predicate_basis(pred: Any, ctx: dict) -> str:
    p = ctx["profile"]
    if pred == "stable_employment_income":
        return "regular salary from an employer"
    (name, value), = pred.items()
    src = p["sources"].get("age", "profile")
    return f"age {p['age']} ({'at most' if name == 'age_max' else 'at least'} {value}; {src})"


def rule_matches(when: dict, ctx: dict) -> bool:
    types = ctx["signal_types"]
    if not all(s in types for s in when.get("signals", [])):
        return False
    if when.get("any_signals") and not any(s in types for s in when["any_signals"]):
        return False
    if any(s in types for s in when.get("not_signals", [])):
        return False
    return all(_predicate_ok(pr, ctx) for pr in when.get("predicates", []))


def candidates(signals: list[dict], ctx: dict) -> dict[str, dict]:
    rules = config.load_yaml("product_rules.yaml")["rules"]
    catalog = product_index()
    by_type = {s["type"]: s for s in signals}
    out: dict[str, dict] = {}
    for order, rule in enumerate(rules):
        if not rule_matches(rule["when"], ctx):
            continue
        rule_signals = [t for t in rule["when"].get("signals", []) + rule["when"].get("any_signals", []) if t in by_type]
        for pid in rule["products"]:
            if pid not in catalog:
                raise ValueError(f"product_rules.yaml rule {rule['id']} references unknown product {pid}")
            c = out.setdefault(pid, {"rules": [], "priority": 99, "order": order, "pos": rule["products"].index(pid),
                                     "reasons": [], "signals": [], "profile_basis": []})
            c["rules"].append(rule["id"])
            c["priority"] = min(c["priority"], int(rule["priority"]))
            c["reasons"].append(rule["reason"])
            c["signals"] += [t for t in rule_signals if t not in c["signals"]]
            for pr in rule["when"].get("predicates", []):
                basis = _predicate_basis(pr, ctx)
                if basis not in c["profile_basis"]:
                    c["profile_basis"].append(basis)
    return out


def holding_period(prod: Product, ctx: dict, tax_plan: dict) -> float | None:
    s = config.settings()
    if prod.basket == "Pillar 3a":
        return float(tax_plan.get("years_to_retirement") or 0) or None
    if prod.basket == "Investment":
        default = s["suitability"]["horizon_years_by_profile"].get(ctx["risk_profile"] or 3, 7)
        return float(max(prod.min_horizon_years, min(ctx["horizon_years"] or default, default + 5)))
    if prod.basket == "Savings" and prod.returns:
        return float(s["projection"]["savings_horizon_years"])
    return None


# ------------------------------------------------------------------ ranking

RANK_SYSTEM = """You are a client-facing advice assistant for a Swiss bank. The client already passed a FinSA
suitability and appropriateness check for every product listed. Rank the listed products within each basket
(rank 1 = most relevant) and write a rationale of one to three sentences addressed to the client ("you").
Every rationale must cite concrete figures from the signals (amounts in CHF, rates, counts).
Rules: use only product ids from the list; cite only signal types from the list; do not promise returns;
do not invent fees or product features beyond the characteristics given; keep a calm, factual tone.
Leave out a product only if it is clearly redundant with a better one in the same basket."""


def _rank_schema(product_ids: list[str], signal_types: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "enum": product_ids},
                        "rank": {"type": "integer"},
                        "rationale": {"type": "string"},
                        "cited_signals": {"type": "array", "items": {"type": "string", "enum": signal_types}},
                    },
                    "required": ["product_id", "rank", "rationale", "cited_signals"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["recommendations", "summary"],
        "additionalProperties": False,
    }


def validate_ranking(out: dict, eligible: dict[str, dict], signals_by_type: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Keep catalog- and gate-approved products only; attach evidence deterministically."""
    catalog = product_index()
    accepted, rejected, seen = [], [], set()
    for item in out.get("recommendations", []):
        pid = item.get("product_id")
        if pid not in catalog:
            rejected.append({"product_id": pid, "reason": "not in the product catalog"})
            continue
        if pid not in eligible:
            rejected.append({"product_id": pid, "reason": "not an eligible candidate for this client"})
            continue
        if pid in seen:
            continue
        seen.add(pid)
        cited = [t for t in item.get("cited_signals", []) if t in signals_by_type] or eligible[pid]["signals"]
        evidence = [e for t in cited for e in signals_by_type[t]["evidence"][:MAX_EVIDENCE]]
        accepted.append({
            "product_id": pid,
            "rank": int(item.get("rank") or 99),
            "rationale": str(item.get("rationale", "")).strip()[:800],
            "cited_signals": cited,
            "evidence": list(dict.fromkeys(evidence)),
            "ranked_by": "llm",
        })
    return accepted, rejected


def deterministic_rationale(prod: Product, cand: dict, signals_by_type: dict[str, dict]) -> str:
    facts = " ".join(signals_by_type[t]["summary"] for t in cand["signals"][:2] if t in signals_by_type)
    if not facts and cand["profile_basis"]:
        facts = "Based on your profile: " + "; ".join(cand["profile_basis"]) + "."
    first_feature = prod.characteristics.split(";")[0].strip()
    return f"{cand['reasons'][0]} {facts} {prod.name}: {first_feature}.".replace("..", ".").strip()


def rank(eligible: dict[str, dict], signals: list[dict], ctx: dict, llm: LLMClient, progress=lambda m: None) -> dict:
    catalog = product_index()
    signals_by_type = {s["type"]: s for s in signals}
    result: dict[str, Any] = {"ranked_by": "rules", "rejected": [], "summary": None}
    ranked: list[dict] = []
    if eligible and llm.available:
        payload = {
            "client": {
                "age": ctx["profile"]["age"], "occupation": ctx["profile"]["occupation"],
                "employment": ctx["profile"]["employment_type"], "risk_profile": ctx["risk_profile_label"],
                "horizon_years": ctx["horizon_years"],
            },
            "signals": [{"type": s["type"], "summary": s["summary"]} for s in signals],
            "products": [
                {"id": pid, "name": catalog[pid].name, "basket": catalog[pid].basket,
                 "characteristics": catalog[pid].characteristics[:400], "price": catalog[pid].price,
                 "why_candidate": c["reasons"], "profile_basis": c["profile_basis"], "priority_hint": c["priority"]}
                for pid, c in eligible.items()
            ],
        }
        progress(f"Ranking {len(eligible)} eligible products with {llm.model_for('reasoning')}")
        try:
            out = llm.structured(
                purpose="rank", tier="reasoning", system=RANK_SYSTEM, user=payload, schema_name="product_ranking",
                schema=_rank_schema(sorted(eligible), sorted(signals_by_type)),
                prompt_version=config.settings()["llm"]["prompt_versions"]["rank"],
            )
            ranked, result["rejected"] = validate_ranking(out, eligible, signals_by_type)
            result["summary"] = str(out.get("summary", ""))[:1200] or None
            result["ranked_by"] = f"llm ({llm.model_for('reasoning')})"
        except LLMUnavailable as exc:
            progress(f"Ranking agent unavailable ({exc}); using deterministic ranking")
            ranked = []
    if not ranked:
        # most important rule first; within a rule, products whose risk best fits the client
        # (diversified, mid-risk products for everyone above "Balanced"), then the rule's own order
        target = min(ctx["risk_profile"] or 1, 3)
        order = sorted(eligible.items(), key=lambda kv: (
            kv[1]["priority"], kv[1]["order"], abs(max(catalog[kv[0]].risk_class, 1) - target), kv[1]["pos"]))
        for i, (pid, c) in enumerate(order, start=1):
            ranked.append({
                "product_id": pid, "rank": i,
                "rationale": deterministic_rationale(catalog[pid], c, signals_by_type),
                "cited_signals": c["signals"],
                "evidence": list(dict.fromkeys(e for t in c["signals"] for e in signals_by_type[t]["evidence"][:MAX_EVIDENCE])),
                "ranked_by": "rules",
            })
    result["ranked"] = ranked
    return result


def build_baskets(ranked: list[dict], eligible: dict[str, dict], ctx: dict, tax_plan: dict) -> list[dict]:
    catalog = product_index()
    baskets: dict[str, list[dict]] = {}
    for item in ranked:
        prod = catalog[item["product_id"]]
        entry = {
            **item,
            "name": prod.name,
            "basket": prod.basket,
            "category": prod.category,
            "product_type": prod.product_type,
            "characteristics": prod.characteristics,
            "price": prod.price,
            "url": prod.url,
            "risk_class": prod.risk_class,
            "priority": eligible[item["product_id"]]["priority"],
            "rules": eligible[item["product_id"]]["rules"],
            "profile_basis": eligible[item["product_id"]]["profile_basis"],
            "holding_period_years": holding_period(prod, ctx, tax_plan),
            "returns": prod.returns.model_dump() if prod.returns else None,
        }
        baskets.setdefault(prod.basket, []).append(entry)
    out = []
    for name in BASKETS:
        if name in baskets:
            items = sorted(baskets[name], key=lambda e: e["rank"])
            for i, e in enumerate(items, start=1):
                e["rank"] = i
            out.append({"basket": name, "products": items})
    return out


def recommend(signals: list[dict], ctx: dict, tax_plan: dict, llm: LLMClient, progress=lambda m: None) -> dict:
    catalog = product_index()
    cands = candidates(signals, ctx)
    gate = {pid: check_product(catalog[pid], ctx) for pid in cands}
    eligible = {pid: c for pid, c in cands.items() if gate[pid]["passed"]}
    hidden = [
        {"product_id": pid, "name": catalog[pid].name, "basket": catalog[pid].basket, "reasons": g["reasons"],
         "checks": g["checks"], "rules": cands[pid]["rules"]}
        for pid, g in gate.items() if not g["passed"]
    ]
    progress(f"{len(cands)} candidates from rules, {len(eligible)} pass the FinSA gate, {len(hidden)} hidden")
    ranking = rank(eligible, signals, ctx, llm, progress)
    return {
        "candidates": {pid: {k: v for k, v in c.items() if k not in ("order", "pos")} for pid, c in cands.items()},
        "gate": gate,
        "hidden": hidden,
        "baskets": build_baskets(ranking["ranked"], eligible, ctx, tax_plan),
        "ranked_by": ranking["ranked_by"],
        "rejected_by_validation": ranking["rejected"],
        "summary": ranking["summary"],
    }
