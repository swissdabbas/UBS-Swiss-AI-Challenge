"""Prepare a showcase: run the full journey for every client so the UI opens on finished results.

For each client: analysis -> demo questionnaire answers -> FinSA acknowledgement -> recommendations.
The demo answers are the same ones the "Fill demo answers" button uses.

    .venv/bin/python scripts/precompute_demo.py            # OpenAI (uses the key; ~CHF 0.03 per client)
    .venv/bin/python scripts/precompute_demo.py off        # rules only, no LLM
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, pipeline  # noqa: E402
from app.ingest.loader import list_profiles  # noqa: E402
from app.suitability import questionnaire  # noqa: E402


def prepare(client_id: str, mode: str) -> str:
    t0 = time.monotonic()
    pipeline.analyse(client_id, mode)
    a = pipeline.latest_run(client_id)["analysis"]
    demo = questionnaire.demo_answers(a["profile"], {s["type"] for s in a["signals"]})
    pipeline.save_answers(client_id, {"profile": {}, **demo})
    pipeline.analyse(client_id, mode)  # answers can change tax inputs; classifications come from the cache
    pipeline.acknowledge(client_id)
    pipeline.recommend(client_id, mode)
    r = pipeline.latest_run(client_id)
    rec = r["recommendation"]
    n = sum(len(b["products"]) for b in rec["baskets"])
    return (f"{client_id:10s} {rec['suitability']['risk_profile_label']:12s} {n:2d} products in "
            f"{len(rec['baskets'])} baskets, {len(rec['hidden'])} hidden, ranked by {rec['ranked_by']}, "
            f"CHF {r['llm_cost_chf']:.4f}, {time.monotonic() - t0:.0f}s")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "openai"
    db.init()
    pipeline.set_mode(mode)
    clients = [p.client_id for p in list_profiles()]
    print(f"Preparing {len(clients)} clients in mode '{mode}'", flush=True)
    failures = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(prepare, c, mode): c for c in clients}
        for f in as_completed(futures):
            try:
                print(f.result(), flush=True)
            except Exception as exc:  # report and continue with the others
                failures += 1
                print(f"{futures[f]:10s} FAILED: {exc}", flush=True)
    total = sum(r["llm_cost_chf"] or 0 for r in db.fetchall("SELECT llm_cost_chf FROM runs"))
    print(f"Done: {len(clients) - failures}/{len(clients)} ready, total LLM cost CHF {total:.4f}")


if __name__ == "__main__":
    main()
