# Demo script (about 8 minutes)

Before the demo: `./run.sh`, open http://localhost:8000. To start from a clean state, stop the server and delete
`var/app.db` (the ESTV answers and classifications are then fetched again on first use).

## 1. Privacy first (30 s)
The start screen asks how data is processed. Click **Start: all data handled in Switzerland by a local LLM**.
Point out that with no local model configured, nothing leaves the machine: AI steps fall back to rules. For the
richer version, click the badge (top right) and choose **OpenAI cloud**. The data is synthetic.

## 2. Pick a client (30 s)
Choose **Persona 13**, a high-net-worth salaried professional, and click **Start check-up**. The progress log shows
validation, rules vs. cache vs. LLM classification, signals and the ESTV tax call.

## 3. Overview (1 min)
* Hero figure: CHF 20.8k monthly surplus, 67% savings rate. Idle cash: CHF 196.6k above a 3-month reserve.
* Tax tip: no Pillar 3a so far; the ESTV calculator says CHF 7,258 would save about CHF 2,979 a year.
* Click **🔎 transactions** on any finding: the evidence is the actual transactions.

## 4. Income & spending (45 s)
Stacked spending by category, income vs spending, one month in detail, recurring flows (salary, bonus, mortgage,
Julius Bär transfers, private school).

## 5. Check transactions (45 s)
Each row shows who categorised it (rule, llm, fallback, you), the confidence and why. Change one category to show
the manual override, then click **Apply changes** to re-run the analysis.

## 6. Profile & risk (1.5 min)
* Fields are pre-filled, and each shows where the value came from (file name, transactions, default).
* Click **Fill demo answers**, then **Save my answers**.
* Risk capacity (from the finances) × risk tolerance (from the answers) gives the risk profile.
* Home financing check: refinancing review possible.
* Read the FinSA notice, tick the box and click **Acknowledge**. The timestamp is stored.

## 7. Recommendations (2 min)
* Baskets: Pillar 3a (with tax saved per year, until retirement, and the marginal rate), Investment, Savings,
  Mortgage & credit, Everyday banking.
* Rationales cite real figures; evidence chips open the transactions.
* Projection: low / medium / high return scenarios after costs, plus the Monte Carlo market range. The assumptions
  are listed under the chart.
* **Not shown to you and why**: Alternative investments and Lombard lending are hidden (qualified investors only;
  risk above profile).

## 8. Contrast personas (1 min)
* **Persona 11** (24, temp work, consumer debt): capacity is capped at 1. Pillar 3a is hidden ("repaying 8-12%
  debt saves more than the tax deduction"), and a prepaid card is recommended for budget control.
* **Persona 9** (retired, 68, still consulting): still 3a-eligible until 70 on the consulting income.
* **Persona 7** (self-employed contractor): 3a cap is 20% of net income (CHF 20,965), not CHF 7,258.

## 9. Advice record (30 s)
**Download PDF**: profile with sources, signals with evidence, suitability results, acknowledgement timestamp,
recommendations, hidden products, projections and assumptions, and the LLM audit trail (model, prompt version,
input hash, tokens, cost). The audit log is append-only.
