# Skills

Skills, tools and capabilities used in this project.

## AI / LLM

- **Structured outputs (JSON schema)** — transaction classification and product ranking both
  return schema-validated JSON. Categories and product IDs are enums, so the model cannot return
  an unknown label or invent a product that is not in the catalog.
- **Provider-agnostic LLM client** (`app/llm/client.py`) — one implementation for the OpenAI API
  and for any OpenAI-compatible local endpoint (Ollama, vLLM), selected at runtime.
- **Rule pre-classification + LLM fallback** — deterministic rules handle the obvious
  transactions, the LLM only sees what is left. Cheaper, faster, and the app still works with the
  LLM switched off.
- **Response caching by content hash** — re-runs are free and deterministic.
- **Cost control and audit** — per-run CHF spend cap, and every call logged with provider, model,
  prompt version, input hash, tokens, latency and cost.
- **Prompt versioning** — `classify-v1` / `rank-v1` recorded on every run and printed in the
  advice record.

## Swiss financial domain

- **FinSA (FIDLEG)** suitability and appropriateness checks, client risk profiling, advice notice
  and acknowledgement, advice documentation.
- **Pillar 3a** — contribution caps with and without a pension fund, self-employed 20% rule,
  retroactive buy-backs for gap years, marginal-rate tax saving.
- **ESTV tax calculator** — client for the JSON API behind swisstaxcalculator.estv.admin.ch, with
  a cached offline fallback table.
- **Mortgage affordability** — imputed interest rate, maintenance, LTV limits, second-mortgage
  amortisation, the 33% affordability rule.
- **KVG health premiums** and income tax treated as obligations that may be paid from another
  account, so surplus is not overstated.

## Engineering

- **FastAPI, Pydantic v2** (Python 3.12) — typed request/response models, schema-validated CSV
  ingestion that fails loudly.
- **SQLite** for runs, caches, questionnaires, acknowledgements, LLM audit and the event log.
- **NumPy Monte Carlo** projections, seeded for reproducibility.
- **fpdf2** advice-record generation.
- **Vanilla HTML/CSS/JS + bundled Chart.js** — no build step, no npm, no CDN; the app runs
  fully offline.
- **Configuration over code** — every business number lives in `config/*.yaml|json`.
