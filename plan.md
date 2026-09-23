# Build plan: UBS Client Recommendation Demo

This plan is written so you can hand it directly to a coding agent. Anything I couldn't determine from your description is marked **[OPEN-n]** and listed at the end. The agent should not guess on those; it should stop and ask.

---

## 1. What you need to provide

**OpenAI credentials and configuration (in a `.env` file, never committed):**

```
OPENAI_API_KEY=sk-...
OPENAI_ORG_ID=org-...              # only if your key belongs to multiple orgs
OPENAI_PROJECT_ID=proj_...         # only if you use project-scoped keys
OPENAI_MODEL_FAST=...              # cheap model for bulk transaction classification
OPENAI_MODEL_REASONING=...         # stronger model for analysis, mapping, explanations
OPENAI_MAX_SPEND_PER_RUN_CHF=...   # optional safety cap the app enforces
```

Both model names are placeholders. Fill in whichever current models your key has access to. The app reads them from config and never hardcodes them.

**Data files:**
- The 10 client files: transaction history plus profile. Please send **column headers and 3–5 sample rows** of each file type before the agent starts.
- The UBS product CSV (investment products and credit cards).
- The UBS funds CSV (active, passive, 3rd pillar).

---

## 2. Architecture

The app is deliberately overengineered but runs locally with one command.

```
docker compose up
 ├── frontend   Next.js (TypeScript) + Tailwind + Recharts      :3000
 ├── backend    FastAPI (Python 3.12) + Pydantic v2             :8000
 ├── db         PostgreSQL 16 (clients, txns, runs, audit log)
 └── worker     background job runner for LLM pipelines (arq/Redis or Celery)
```

**Core design principle:** the LLM classifies, reasons, and explains, while deterministic code does all math, eligibility checks, and compliance gating. This makes results reproducible, testable, and defensible under FinSA. The LLM can never invent a product, because product selection is constrained to IDs that exist in the catalog.

**Backend modules:**

| Module | Responsibility |
|---|---|
| `ingest/` | CSV loading, schema validation (Pydantic), normalization (dates, CHF/FX amounts, counterparty names) |
| `classify/` | LLM transaction classification with structured outputs, caching, and confidence scores |
| `features/` | Deterministic signal detection (recurring flows, surplus, life events, etc.) |
| `tax/` | Swiss tax calculation and Pillar 3a savings |
| `suitability/` | FinSA risk profile, knowledge/experience check, and gating |
| `recommend/` | Rule engine for signals → product candidates, plus an LLM ranking and explanation agent |
| `projection/` | Return projections and Monte Carlo simulation |
| `reporting/` | Advice documentation (PDF) and audit trail |
| `agents/` | OpenAI Agents SDK orchestration and tool definitions |

---

## 3. Pipeline, step by step

### Step 1: Ingest
- The agent loads the client CSVs, product CSV, and fund CSV, validating each against a declared schema. It fails loudly with a readable error if a column is missing.
- Counterparty names are normalized (e.g. "MIGROS ZH 1234" becomes "Migros").
- FX transactions are flagged using a currency column if one exists **[OPEN-1]**.

### Step 2: Classify income and spending (OpenAI)
- Transactions are sent in batches of about 50 using **structured outputs** with a fixed JSON schema. The category is an enum from a fixed taxonomy: salary, bonus, rent, mortgage, insurance, childcare, school, groceries, travel, FX spend, investments, pillar 3a, transfers, and so on. Each result carries a confidence score and a short reason.
- Before calling the LLM, a rule-based pre-classifier handles obvious cases (e.g. counterparty "Kita …" becomes childcare), which saves cost.
- Results are cached by transaction hash, so re-runs are free and deterministic.
- Anything below a confidence threshold is shown in the UI as "needs review" and can be manually overridden.

### Step 3: Signal detection (deterministic)

| Signal | Detection logic |
|---|---|
| Recurring income | Same counterparty, periodic interval (±3 days), low amount variance |
| Monthly surplus and savings rate | (income − spending) / income, per month and as a trailing 6-month average |
| Idle cash | Balance above X months of expenses **[OPEN-2: is there a balance column? What is X?]** |
| Foreign-currency spending | Share and volume of non-CHF spend, plus estimated FX fees paid |
| Recurring rent | Monthly payment to a landlord or property management, flagged as a mortgage prospect |
| Family | Payments to daycare, schools, or child-related merchants |
| Missing Pillar 3a | Employment income present, but no 3a contributions detected |
| Salary jump | Salary increase above a threshold (e.g. >10%) month over month |
| New employer | Change of salary payer |
| Move | New rent recipient, a moving company payment, or new utility providers |

Every detected signal stores its **evidence**, meaning the IDs of the transactions that triggered it. This evidence is shown in the UI and in the advice document.

### Step 4: Tax and Pillar 3a
- The maximum 3a contribution comes from config and is not hardcoded. For employees with a pension fund it is CHF 7,258 (2025/2026 figure; please verify). Self-employed people without a pension fund can contribute 20% of net income up to a higher cap.
- Tax saved is calculated as contribution × marginal tax rate (federal + cantonal + municipal, plus church tax if applicable).
- The tax rate source is **[OPEN-3]**. Options are:
  - (a) the official ESTV tax calculator, which requires scraping or unofficial endpoints and is fragile for a demo;
  - (b) pre-computed tax tables for the relevant cantons and municipalities, which are reliable offline;
  - (c) the LLM with web search, which is **not recommended** because the numbers wouldn't be reproducible.
- The output includes the recommended annual 3a amount, the tax saved per year, and the cumulative tax saved over the holding period.
- Retroactive 3a contributions (possible since 2025 for gap years) should appear as an optional recommendation.

### Step 5: FinSA suitability gate (before any recommendation is shown)
- The client risk profile covers risk capacity (derived from income, surplus, and assets) and risk tolerance (from questionnaire answers).
- The knowledge and experience check is done per product category.
- Suitability check: whether each product fits the client's risk profile, investment objectives, and financial situation. Appropriateness check: whether the client has enough knowledge and experience for the product.
- The client must explicitly acknowledge the advice notice, and the timestamp is stored.
- Products that fail the gate are **hidden with a stated reason**, not just re-ranked.
- Where the profile data comes from is **[OPEN-4]**.

### Step 6: Product mapping
- A YAML rule file maps signals to product categories. For example:
  - high FX spend → multi-currency or travel cards;
  - surplus plus idle cash → savings plans or funds;
  - no 3a → 3a products;
  - rent plus stable salary → mortgage prospect;
  - family → savings for children.
- The candidates are then filtered by the suitability gate.
- The **OpenAI ranking agent** receives the client signals and the eligible candidates. It returns a ranked list using only product IDs from an enum, with a rationale for each product that cites the evidence. Its output is validated, and anything outside the catalog is rejected.
- Products are grouped into **baskets**: Investment, Cards, FX, and Pillar 3a.
- Whether a mortgage recommendation is in scope is **[OPEN-5]**. You mentioned rent as a mortgage prospect, but no mortgage products were listed.

### Step 7: Projections per basket
- The recommended holding period is derived from the product type, the risk profile, and the time to retirement for 3a (which requires age from the profile).
- Expected return is shown as a graph with a median line and 10th/90th percentile bands from a Monte Carlo simulation, using monthly contributions from the detected surplus.
- The return assumptions are **[OPEN-6]**. They could come from historical performance in the fund CSV, a fixed capital-market-assumptions table per asset class, or both.
- Fees (TER, custody) are deducted in projections if they are present in the CSV.
- The 3a basket additionally shows tax saved, both per year and cumulatively.

### Step 8: Documentation
- The app generates a PDF advice record per client run. It contains:
  - the client profile and signals with evidence;
  - the suitability and appropriateness results;
  - the recommended products with rationale;
  - the projections and their assumptions;
  - the acknowledgement timestamp;
  - the model name, version, and prompt version.
- An immutable audit log in Postgres records every LLM call, including input hash, output, model, and cost.

---

## 4. Frontend pages

1. **Client selector**: the 10 profiles, with an upload option for new CSVs.
2. **Overview**: key figures (income, spending, surplus, savings rate, idle cash) and detected signals as cards with evidence drill-down.
3. **Income and spending**: monthly stacked bar charts by category, an income vs. spending line chart, and a table of recurring flows.
4. **Classification review**: a transaction table with the LLM category, confidence, and manual override.
5. **Suitability**: a risk questionnaire and knowledge/experience form, followed by the FinSA notice and acknowledgement.
6. **Recommendations**: baskets with ranked products, a rationale for each, a holding period, a projection chart, and tax saved (for 3a).
7. **Advice document**: PDF download.

---

## 5. Milestones with acceptance criteria

| # | Milestone | Done when |
|---|---|---|
| M1 | Skeleton | `docker compose up` shows the frontend, backend health check, and database |
| M2 | Ingest | All 13 CSVs load, validate, and appear in the UI |
| M3 | Classification | All transactions for one client are classified, cached, and shown as charts |
| M4 | Signals | All 10 signals are computed with evidence, with unit tests per signal |
| M5 | Tax | Tax saved per client is verified against the chosen source for at least 3 cases |
| M6 | Suitability | Gate blocks unsuitable products, and acknowledgement is stored |
| M7 | Recommendations | Ranked baskets contain catalog products only, with a validation test |
| M8 | Projections | Monte Carlo charts render per basket, and assumptions are visible |
| M9 | Documentation | PDF record and audit log are complete |
| M10 | Polish | All 10 clients run end to end, a demo script exists, and the README is written |

Testing: pytest covers signals, tax, suitability, and projections. Golden-file tests use the 10 profiles. An LLM eval set of about 100 hand-labelled transactions measures classification accuracy.

---

## 6. Open questions (the agent must not assume these)

1. **CSV structure:** Please share the headers and sample rows. Is there a currency column, an account balance column, and a counterparty or IBAN field?
see the provided files, also check the other branches
2. **Idle cash definition:** What balance threshold counts as idle (e.g. more than 3 months of expenses)?
plot of income and spending is over one month
3. **Tax source:** ESTV calculator, pre-computed tables, or something else? Which cantons and municipalities do the 10 clients live in?
ESTV calculator if available, otherwise simply rough estimates based on canton and municipality
4. **Client profile content:** Does the profile CSV contain age, canton, marital status, children, religion, employment type, pension-fund membership, and existing risk profile or investment experience? If not, should the UI collect these through a questionnaire?
Yes, but please check the profile csv first, make a proper pull
5. **Mortgages:** Should mortgages be recommended? If so, where does that product data come from?
mortgage products should be recommended for certain clients, check the history and age
6. **Return assumptions:** Should they come from historical data in the fund CSV, or should you provide expected returns and volatility per asset class?
return based on products expected return (give low, medium, high) expected return 
7. **Data privacy:** Is the transaction data real or synthetic? Real client data sent to OpenAI raises banking-secrecy and data-protection issues. It would then need anonymization before sending, Zero Data Retention on your OpenAI account, or a Swiss or EU-hosted deployment. Which applies?
should simply be in the begingin a button (all data handled in switzerland by local LLM)
8. **Language:** Should the UI be in English only, or also in German, French, and Italian?
English
9. **Users:** Is the demo client-facing or advisor-facing? This changes how the FinSA acknowledgement flow works.
client facing
10. **Stack:** Are you fine with Python/FastAPI plus Next.js, or do you have a mandated stack?
simply html and python (simply but nice UI)

API key under api-key.sk
Data of funds will be provided shortly 

If you'd like, I can turn this into a doc you can share and keep editing.