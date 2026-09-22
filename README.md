# UBS-Swiss-AI-Challenge 22nd September 2026

Team document: https://docs.google.com/document/d/1j1pWXmOZwlGYhHIVCQdm_VhaaExKX5-2u7fW1QXmMyo/edit?usp=sharing

**Financial check-up**: a client-facing demo that reads a year of account transactions, finds what they say
about the client (surplus, idle cash, missing Pillar 3a, rent, children, debt, ...), checks FinSA suitability,
and recommends UBS products in baskets with projections, tax savings and a PDF advice record.

The LLM classifies, ranks and explains. Deterministic code does all maths, eligibility and compliance
gating, so results are reproducible and testable, and the LLM can only pick products that exist in the catalog
and passed the gate.

## Run it

```bash
./run.sh                 # creates .venv on first run, then serves http://localhost:8000
```

Python 3.12, no Docker, no database server. State lives in `var/app.db` (SQLite).

The start screen asks how data is processed:

| Mode | What happens |
|---|---|
| **Local (Switzerland)**, default | LLM calls go to an OpenAI-compatible endpoint you host (`LOCAL_LLM_BASE_URL`, e.g. Ollama or vLLM). If none is configured, the AI steps fall back to deterministic rules, and nothing leaves the machine. |
| OpenAI cloud | Uses the key from `.env` (`OPENAI_API_KEY`) or `api-key.sk`. Transaction descriptions are sent to OpenAI. Use it with the synthetic data only. |
| Rules only | No LLM at all. |

The badge in the top-right corner shows the active mode and lets you switch.

### Configuration

Copy `.env.example` to `.env`. Every setting is optional:

```
OPENAI_API_KEY=...                  # or keep the key in api-key.sk (git-ignored)
OPENAI_MODEL_FAST=gpt-5-mini        # transaction classification
OPENAI_MODEL_REASONING=gpt-5        # ranking + rationales
OPENAI_MAX_SPEND_PER_RUN_CHF=1.00   # hard cap per run, enforced before each call
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_MODEL=llama3.1:8b
```

All business numbers are in `config/`:

| File | Content |
|---|---|
| `settings.yaml` | thresholds (idle cash months, recurring tolerance, salary jump %), 3a caps, mortgage rules, projection shares, LLM prices |
| `products.yaml` | per-product attributes the catalog CSV lacks: basket, risk class, required knowledge, eligibility, **illustrative** low/medium/high returns, volatility, costs |
| `product_rules.yaml` | signal → product candidate rules |
| `tax_tables.json` | offline tax fallback, pre-computed from ESTV (`scripts/build_tax_tables.py`) |

## What it does

```
CSV ─► ingest ─► classify ─► cash flow ─► signals ─► Pillar 3a / tax ─► FinSA gate ─► rank ─► baskets ─► projections ─► PDF
       schema    rules →     monthly      17 rules    ESTV calculator    suitability    LLM or    3a, Invest,  low/med/high   advice record
       checks    cache →     KPIs,        with        (live, cached)     appropriate-   rules     Savings,     + Monte Carlo  + audit log
                 LLM →       recurring    evidence    else ESTV table    ness, eligib.            Cards, FX,
                 fallback    flows                                                                Mortgage ...
```

| Module | Responsibility |
|---|---|
| `app/ingest` | CSV schema validation with readable errors, Swiss number/date formats, counterparty normalisation, profile from the file name, product catalog + attribute check |
| `app/classify` | 40-category taxonomy; rule pre-classifier; batched LLM classification (50 per call) with a strict JSON schema, confidence and reason; SQLite cache per unique description; sign check; manual overrides |
| `app/features` | monthly cash flow, KPIs, recurring flows (±3-day interval or calendar-monthly), derived profile, signal detectors |
| `app/tax` | 3a eligibility and caps, ESTV tax calculator client (tax with vs without the contribution), table fallback, retroactive 3a |
| `app/suitability` | questionnaire, risk capacity × tolerance → risk profile, product gate, FinSA notice + acknowledgement |
| `app/recommend` | YAML rules → candidates → gate → LLM ranking (enum-constrained, validated) or deterministic ranking; mortgage affordability |
| `app/projection` | low/medium/high scenarios after costs + seeded Monte Carlo 10th–90th percentile |
| `app/reporting` | PDF advice record |
| `app/llm` | one client for OpenAI and local endpoints: structured outputs, spend cap, append-only audit log |
| `static/` | plain HTML/CSS/JS UI (Chart.js vendored, so it works offline) |

### Signals (all with evidence = transaction ids)

Recurring income · monthly surplus and savings rate (trailing 6 full months) · spending exceeds income · idle
cash (balance above 3 months of spending) · foreign & travel spend · recurring rent · children · Pillar 3a gap
(missing or below the cap) · salary jump · new employer · move · consumer debt · existing mortgage ·
investments held elsewhere · high net worth · self-employed · retired.

### Suitability gate

A product is **hidden with its reasons** (shown to the client) if any check fails:

* **Eligibility:** intermediary/research products, business products for employees, age limits, 3a
  eligibility, qualified-investor thresholds, card income guidelines, credit checks when in debt, and mortgage
  affordability (5% imputed rate + 1% maintenance + amortisation ≤ 33% of income, 20% equity).
* **Suitability:** product risk above the client's profile, horizon too short, consumer debt or deficit.
* **Appropriateness:** declared knowledge below what the product needs.

Recommendations require a complete questionnaire and an acknowledgement of the FinSA notice that is newer
than the last change to the answers. The acknowledgement timestamp and notice hash are stored and printed in the
advice record.

## Tests

```bash
.venv/bin/pytest                               # 118 tests, offline, ~10 s
ESTV_LIVE=1 .venv/bin/pytest tests/test_tax.py # also re-verify the 3 recorded ESTV cases live
.venv/bin/python scripts/eval_classifier.py openai   # classification accuracy on the labelled set
```

* Unit tests per signal, tax rule, gate check and projection formula.
* Golden files (`tests/golden/`) for all 16 personas: the full pipeline in rules mode. After an intended
  change, run `UPDATE_GOLDEN=1 .venv/bin/pytest tests/test_golden.py` and review the diff.
* Validation tests: the LLM ranker cannot introduce products outside the catalog or the gate.
* Audit tables reject `UPDATE`/`DELETE`.
* `tests/eval/labelled_transactions.csv`: 113 hand-labelled transactions. Current accuracy: rules + bank-category
  fallback 94.7%; rules + gpt-5-mini 94.7% (most LLM "misses" are defensible, e.g. SBB ticket as transport).

## Data notes

* 16 synthetic personas (not 10), one year each (2025-09-22 to 2026-09-22), all in CHF, columns
  `date, description, category, amount, currency, balance`.
* There is **no separate profile CSV**. Age band, gender, occupation/segment and marital status come from the
  file name. Canton, church tax, children, pension fund and wealth held elsewhere are derived from transactions
  or defaults, and the client confirms them in step 5.
* There is **no fund CSV**. Expected returns are illustrative assumptions in `config/products.yaml`, shown
  next to every projection.
* Every transaction is in CHF, so FX fees actually paid cannot be measured. "Foreign spend" is detected from
  merchant names and locations.
* The catalog has no dedicated mortgage product. Mortgage advice maps to the catalog's **Financing** product
  (real-estate financing), for refinancing reviews of existing mortgages and for renters who pass affordability.

## Limitations

* The ESTV API is public but undocumented. Answers are cached, and the offline table covers canton capitals only.
* Net salary is converted to gross with a fixed 0.88 factor. For married couples only this account's income is
  known.
* Product attributes (risk class, returns, card income guidelines) are demo assumptions, not UBS figures.
* The processing mode is a global setting, not per user.
