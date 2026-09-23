"""HTTP API + static UI.  Run:  .venv/bin/uvicorn app.main:app --port 8000"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, pipeline
from .classify import taxonomy
from .ingest import loader
from .ingest.loader import IngestError
from .llm.client import MODES, describe_mode
from .reporting import pdf
from .suitability import questionnaire
from .suitability.gate import client_context

db.init()
app = FastAPI(title="UBS client recommendation demo", version="1.0")
STATIC = config.ROOT / "static"


@app.exception_handler(IngestError)
async def ingest_error(_req, exc: IngestError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


def _run_or_404(client_id: str) -> dict:
    run = pipeline.latest_run(client_id)
    if not run:
        raise HTTPException(404, "No analysis yet for this client.")
    return run


def _client_or_404(client_id: str) -> None:
    try:
        loader.client_path(client_id)
    except KeyError:
        raise HTTPException(404, f"Unknown client {client_id}") from None


# ----------------------------------------------------------------- session

class ModeIn(BaseModel):
    mode: str


@app.get("/api/status")
def status():
    mode = pipeline.get_mode()
    return {
        "mode": describe_mode(mode),
        "chosen": db.get_state("processing_mode") is not None,
        "modes": {m: describe_mode(m) for m in MODES},
        "products": len(loader.load_products()),
        "clients": len(loader.list_profiles()),
        "idle_cash_months": config.settings()["signals"]["idle_cash_months"],
    }


@app.post("/api/mode")
def set_mode(body: ModeIn):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    pipeline.set_mode(body.mode)
    return describe_mode(body.mode)


# ----------------------------------------------------------------- clients

@app.get("/api/clients")
def clients():
    out = []
    for p in loader.list_profiles():
        run = db.fetchone("SELECT run_id, status, created_at FROM runs WHERE client_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                          (p.client_id,))
        out.append({**p.model_dump(), "last_run": run})
    return out


@app.post("/api/clients/upload")
async def upload(
    file: UploadFile = File(...),
    name: str = Form(""),
    age: int = Form(40),
    marital_status: str = Form("single"),
    occupation: str = Form("Unknown"),
):
    text = (await file.read()).decode("utf-8-sig")
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(file.filename or "upload").stem)[:40] or "upload"
    client_id = f"upload_{stem}"
    loader.parse_transactions(text, client_id, file.filename or "upload.csv")  # validate before saving
    d = config.path(config.settings()["app"]["uploads_dir"])
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{client_id}.csv").write_text(text, encoding="utf-8")
    meta = {"display_name": name or stem, "age": age, "marital_status": marital_status, "occupation": occupation}
    (d / f"{client_id}.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8")
    db.log_event("client_uploaded", client_id, payload=meta)
    return {"client_id": client_id}


@app.post("/api/clients/{client_id}/analyse")
def analyse(client_id: str):
    _client_or_404(client_id)
    mode = pipeline.get_mode()
    return {"job_id": pipeline.start_job("analyse", client_id, lambda pr: pipeline.analyse(client_id, mode, pr))}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return j


@app.get("/api/clients/{client_id}/analysis")
def analysis(client_id: str):
    run = _run_or_404(client_id)
    a = dict(run["analysis"])
    a.pop("transactions")
    a["status"] = run["status"]
    a["llm_cost_chf"] = run["llm_cost_chf"]
    a["category_labels"] = {k: v[0] for k, v in taxonomy.CATEGORIES.items()}
    return a


@app.get("/api/clients/{client_id}/transactions")
def transactions(client_id: str):
    run = _run_or_404(client_id)
    return {"transactions": run["analysis"]["transactions"],
            "categories": [{"id": k, "label": v[0], "kind": v[1]} for k, v in taxonomy.CATEGORIES.items()],
            "threshold": config.settings()["llm"]["confidence_threshold"]}


class OverrideIn(BaseModel):
    category: str


@app.post("/api/clients/{client_id}/transactions/{txn_id}/override")
def override(client_id: str, txn_id: str, body: OverrideIn):
    if body.category not in taxonomy.CATEGORIES:
        raise HTTPException(400, "unknown category")
    run = _run_or_404(client_id)
    if not any(t["id"] == txn_id for t in run["analysis"]["transactions"]):
        raise HTTPException(404, "unknown transaction")
    db.execute("INSERT INTO overrides (client_id, txn_id, category, created_at) VALUES (?,?,?,?) "
               "ON CONFLICT(client_id, txn_id) DO UPDATE SET category=excluded.category, created_at=excluded.created_at",
               (client_id, txn_id, body.category, db.now_iso()))
    db.log_event("classification_override", client_id, run["run_id"], {"txn_id": txn_id, "category": body.category})
    return {"ok": True}


# ------------------------------------------------------------- suitability

@app.get("/api/questionnaire")
def questionnaire_spec():
    return questionnaire.spec()


@app.get("/api/clients/{client_id}/suitability")
def suitability(client_id: str):
    run = _run_or_404(client_id)
    a = run["analysis"]
    answers = pipeline.get_answers(client_id)
    ctx = client_context(a["profile"], a["kpis"], a["signals"], answers)
    ack = pipeline.latest_ack(client_id)
    return {
        "profile": a["profile"],
        "answers": {k: v for k, v in answers.items() if not k.startswith("_")},
        "answers_updated_at": answers.get("_updated_at"),
        "demo_answers": questionnaire.demo_answers(a["profile"], {s["type"] for s in a["signals"]}),
        "result": {k: ctx[k] for k in ("risk_capacity", "risk_tolerance", "risk_profile", "risk_profile_label",
                                       "horizon_years", "financial_assets", "mortgage", "complete")},
        "acknowledgement": ack,
        "ack_current": pipeline.ack_is_current(client_id),
    }


class AnswersIn(BaseModel):
    profile: dict = {}
    risk: dict = {}
    knowledge: dict = {}


@app.post("/api/clients/{client_id}/suitability")
def save_suitability(client_id: str, body: AnswersIn):
    _client_or_404(client_id)
    pipeline.save_answers(client_id, body.model_dump())
    mode = pipeline.get_mode()  # profile answers (canton, age, ...) change tax figures: re-analyse
    return {"job_id": pipeline.start_job("analyse", client_id, lambda pr: pipeline.analyse(client_id, mode, pr))}


@app.post("/api/clients/{client_id}/acknowledge")
def acknowledge(client_id: str):
    _client_or_404(client_id)
    if not pipeline.get_answers(client_id):
        raise HTTPException(400, "Complete the questionnaire before acknowledging the notice.")
    return pipeline.acknowledge(client_id)


# --------------------------------------------------------- recommendations

@app.post("/api/clients/{client_id}/recommend")
def recommend(client_id: str):
    _run_or_404(client_id)
    mode = pipeline.get_mode()
    return {"job_id": pipeline.start_job("recommend", client_id, lambda pr: pipeline.recommend(client_id, mode, pr))}


@app.get("/api/clients/{client_id}/recommendations")
def recommendations(client_id: str):
    run = _run_or_404(client_id)
    if not run["recommendation"]:
        raise HTTPException(404, "No recommendations yet.")
    return {**run["recommendation"], "run_id": run["run_id"], "tax_plan": run["analysis"]["tax_plan"]}


@app.get("/api/clients/{client_id}/report.pdf")
def report(client_id: str):
    run = _run_or_404(client_id)
    data = pdf.build(run)
    db.log_event("advice_record_generated", client_id, run["run_id"], {"bytes": len(data)})
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="advice-record-{client_id}-{run["run_id"]}.pdf"'})


@app.get("/api/clients/{client_id}/audit")
def audit(client_id: str):
    run = _run_or_404(client_id)
    calls = db.fetchall("SELECT id, ts, purpose, provider, model, prompt_version, input_sha256, prompt_tokens, "
                        "completion_tokens, cost_chf, latency_ms, status, error FROM llm_audit WHERE client_id=? ORDER BY id DESC LIMIT 200",
                        (client_id,))
    events = db.fetchall("SELECT id, ts, run_id, kind, payload_json FROM events WHERE client_id=? ORDER BY id DESC LIMIT 200",
                         (client_id,))
    return {"run_id": run["run_id"], "llm_calls": calls, "events": events}


# ------------------------------------------------------------------ static

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
