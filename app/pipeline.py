"""Orchestration of a client run, plus a tiny in-process background job runner.

analyse():    ingest -> classify -> cash flow -> profile -> signals -> Pillar 3a / tax
recommend():  FinSA context -> rules -> gate -> ranking -> baskets -> projections
Every run is stored in SQLite; the advice record is built from the stored run.
"""

from __future__ import annotations

import json
import secrets
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from . import config, db
from .classify import taxonomy
from .classify.service import audit_summary, classify_transactions
from .features import cashflow, signals as sig
from .features.profile import derive_profile
from .ingest.loader import load_client
from .llm.client import LLMClient, describe_mode
from .projection.scenarios import basket_projections
from .recommend.engine import recommend as run_recommend
from .suitability import questionnaire
from .suitability.gate import client_context
from .tax.service import estimated_annual_tax, pillar3a_plan

# ---------------------------------------------------------------- settings

def get_mode() -> str:
    return db.get_state("processing_mode") or config.settings()["llm"]["default_mode"]


def set_mode(mode: str) -> None:
    db.set_state("processing_mode", mode)
    db.log_event("processing_mode", payload={"mode": mode, "provider": describe_mode(mode)})


def get_answers(client_id: str) -> dict:
    row = db.fetchone("SELECT answers_json, updated_at FROM questionnaires WHERE client_id=?", (client_id,))
    if not row:
        return {}
    a = json.loads(row["answers_json"])
    a["_updated_at"] = row["updated_at"]
    return a


def save_answers(client_id: str, answers: dict) -> None:
    clean = {k: answers.get(k, {}) for k in ("profile", "risk", "knowledge")}
    db.execute(
        "INSERT INTO questionnaires (client_id, answers_json, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(client_id) DO UPDATE SET answers_json=excluded.answers_json, updated_at=excluded.updated_at",
        (client_id, json.dumps(clean), db.now_iso()),
    )
    db.log_event("questionnaire_saved", client_id, payload=clean)


def latest_ack(client_id: str) -> dict | None:
    return db.fetchone(
        "SELECT * FROM acknowledgements WHERE client_id=? AND notice_version=? ORDER BY id DESC LIMIT 1",
        (client_id, questionnaire.NOTICE_VERSION),
    )


def acknowledge(client_id: str) -> dict:
    run = latest_run(client_id)
    db.execute(
        "INSERT INTO acknowledgements (client_id, run_id, notice_version, notice_sha256, acknowledged_at) VALUES (?,?,?,?,?)",
        (client_id, run["run_id"] if run else None, questionnaire.NOTICE_VERSION, questionnaire.notice_sha256(), db.now_iso()),
    )
    db.log_event("finsa_acknowledged", client_id, run["run_id"] if run else None, {"notice_version": questionnaire.NOTICE_VERSION})
    return latest_ack(client_id)


def ack_is_current(client_id: str) -> bool:
    ack, answers = latest_ack(client_id), get_answers(client_id)
    return bool(ack and answers and ack["acknowledged_at"] >= answers["_updated_at"])


# -------------------------------------------------------------------- runs

def latest_run(client_id: str) -> dict | None:
    row = db.fetchone("SELECT * FROM runs WHERE client_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (client_id,))
    if not row:
        return None
    row["analysis"] = json.loads(row.pop("analysis_json") or "null")
    row["recommendation"] = json.loads(row.pop("recommendation_json") or "null")
    return row


def analyse(client_id: str, mode: str, progress: Callable[[str], None] = lambda m: None) -> str:
    run_id = "r" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(3)
    llm = LLMClient(mode, run_id, client_id)
    progress(f"Loading {client_id}")
    base, txns = load_client(client_id)
    progress(f"{len(txns)} transactions loaded and validated")
    classes = classify_transactions(txns, llm, progress)
    rows = cashflow.enrich(txns, classes)
    months = cashflow.monthly(rows)
    kpis = cashflow.kpis(rows, months)
    flows = cashflow.recurring_flows(rows)
    answers = get_answers(client_id)
    profile = derive_profile(base, rows, answers.get("profile"))
    progress("Checking income tax and health insurance are covered")
    cashflow.add_missing_obligations(kpis, months, rows, profile, *estimated_annual_tax(profile, kpis, rows))
    progress("Detecting signals")
    signals = sig.detect_all(rows, kpis, months, flows, profile)
    progress("Computing Pillar 3a and tax savings (ESTV tax calculator)")
    tax_plan = pillar3a_plan(profile, kpis, rows)
    analysis = {
        "run_id": run_id,
        "client_id": client_id,
        "created_at": db.now_iso(),
        "mode": describe_mode(mode),
        "prompt_versions": config.settings()["llm"]["prompt_versions"],
        "profile": profile,
        "kpis": kpis,
        "months": months,
        "flows": flows,
        "signals": signals,
        "tax_plan": tax_plan,
        "classification_summary": audit_summary(classes),
        "transactions": [
            {"id": t.id, "date": t.date.isoformat(), "description": t.description, "counterparty": t.counterparty,
             "source_category": t.source_category, "amount": t.amount, "balance": t.balance,
             "category": classes[t.id]["category"], "category_label": taxonomy.label(classes[t.id]["category"]),
             "kind": taxonomy.kind(classes[t.id]["category"]), "is_foreign": classes[t.id]["is_foreign"],
             "confidence": round(classes[t.id]["confidence"], 3), "classified_by": classes[t.id]["source"],
             "reason": classes[t.id]["reason"], "needs_review": classes[t.id]["needs_review"]}
            for t in txns
        ],
    }
    db.execute(
        "INSERT INTO runs (run_id, client_id, created_at, mode, status, analysis_json, llm_cost_chf) VALUES (?,?,?,?,?,?,?)",
        (run_id, client_id, analysis["created_at"], mode, "analysed", json.dumps(analysis, default=str), llm.spent_chf),
    )
    db.log_event("analysis_completed", client_id, run_id, {"signals": [s["type"] for s in signals], "llm_cost_chf": llm.spent_chf})
    progress(f"Analysis done ({len(signals)} signals, LLM cost CHF {llm.spent_chf:.4f})")
    return run_id


def recommend(client_id: str, mode: str, progress: Callable[[str], None] = lambda m: None) -> str:
    run = latest_run(client_id)
    if not run:
        raise ValueError("Run the analysis first.")
    answers = get_answers(client_id)
    a = run["analysis"]
    ctx = client_context(a["profile"], a["kpis"], a["signals"], answers)
    if not ctx["complete"]:
        raise ValueError("Please complete the risk and knowledge questionnaire first.")
    if not ack_is_current(client_id):
        raise ValueError("Please read and acknowledge the FinSA notice first.")
    llm = LLMClient(mode, run["run_id"], client_id)
    llm.spent_chf = run.get("llm_cost_chf") or 0.0
    rec = run_recommend(a["signals"], ctx, a["tax_plan"], llm, progress)
    progress("Projecting baskets")
    rec["projections"] = basket_projections(rec["baskets"], a["kpis"], a["signals"], a["tax_plan"])
    rec["suitability"] = {k: ctx[k] for k in ("risk_capacity", "risk_tolerance", "risk_profile", "risk_profile_label",
                                              "horizon_years", "knowledge", "financial_assets", "mortgage")}
    rec["acknowledgement"] = latest_ack(client_id)
    rec["answers"] = {k: v for k, v in answers.items() if not k.startswith("_")}
    rec["created_at"] = db.now_iso()
    rec["mode"] = describe_mode(mode)
    db.execute("UPDATE runs SET recommendation_json=?, status=?, llm_cost_chf=? WHERE run_id=?",
               (json.dumps(rec, default=str), "recommended", llm.spent_chf, run["run_id"]))
    db.log_event("recommendation_completed", client_id, run["run_id"],
                 {"products": [p["product_id"] for b in rec["baskets"] for p in b["products"]], "ranked_by": rec["ranked_by"],
                  "hidden": [h["product_id"] for h in rec["hidden"]]})
    progress(f"Recommendations ready ({rec['ranked_by']})")
    return run["run_id"]


# -------------------------------------------------------------------- jobs

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def start_job(kind: str, client_id: str, fn: Callable[[Callable[[str], None]], str]) -> str:
    job_id = secrets.token_hex(6)
    job = {"id": job_id, "kind": kind, "client_id": client_id, "status": "running", "messages": [], "run_id": None,
           "error": None, "started_at": db.now_iso()}
    with _jobs_lock:
        _jobs[job_id] = job

    def progress(msg: str) -> None:
        job["messages"].append({"ts": db.now_iso(), "msg": msg})

    def target() -> None:
        try:
            job["run_id"] = fn(progress)
            job["status"] = "done"
        except Exception as exc:  # reported to the UI
            job["status"] = "error"
            job["error"] = str(exc)
            job["messages"].append({"ts": db.now_iso(), "msg": f"Error: {exc}"})
            traceback.print_exc()

    threading.Thread(target=target, daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)
