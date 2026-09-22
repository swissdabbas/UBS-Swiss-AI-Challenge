"""SQLite persistence: caches, runs, questionnaires, and immutable audit tables.

The audit tables (llm_audit, events, acknowledgements) are append-only: triggers
abort any UPDATE or DELETE, so the trail cannot be rewritten by the app.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

_lock = threading.RLock()
_db_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS classification_cache (
    key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    is_foreign INTEGER NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT,
    source TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS overrides (
    client_id TEXT NOT NULL,
    txn_id TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (client_id, txn_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    analysis_json TEXT,
    recommendation_json TEXT,
    llm_cost_chf REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS questionnaires (
    client_id TEXT PRIMARY KEY,
    answers_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS estv_cache (
    key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acknowledgements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    run_id TEXT,
    notice_version TEXT NOT NULL,
    notice_sha256 TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT,
    client_id TEXT,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_chf REAL,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    client_id TEXT,
    run_id TEXT,
    kind TEXT NOT NULL,
    payload_json TEXT
);
"""

IMMUTABLE_TABLES = ("llm_audit", "events", "acknowledgements")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(db_path: Path | None = None) -> None:
    global _db_path
    _db_path = db_path or config.path(config.settings()["app"]["db_path"])
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        for table in IMMUTABLE_TABLES:
            for op in ("UPDATE", "DELETE"):
                con.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
                )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    if _db_path is None:
        init()
    with _lock:
        con = sqlite3.connect(_db_path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def fetchone(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


def log_event(kind: str, client_id: str | None = None, run_id: str | None = None, payload: Any = None) -> None:
    execute(
        "INSERT INTO events (ts, client_id, run_id, kind, payload_json) VALUES (?,?,?,?,?)",
        (now_iso(), client_id, run_id, kind, json.dumps(payload, default=str) if payload is not None else None),
    )


def get_state(key: str, default: str | None = None) -> str | None:
    row = fetchone("SELECT value FROM app_state WHERE key=?", (key,))
    return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    execute("INSERT INTO app_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
