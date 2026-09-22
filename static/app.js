/* Financial check-up demo UI: plain JS, hash routing, Chart.js for charts. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (x, d = 0) => Number(x).toLocaleString("de-CH", { maximumFractionDigits: d, minimumFractionDigits: d });
const chf = (x, d = 0) => (x === null || x === undefined ? "–" : `CHF ${num(x, d)}`);
const pct = (x, d = 0) => (x === null || x === undefined ? "–" : `${(x * 100).toFixed(d)}%`);
const compact = (x) => {
  const a = Math.abs(x);
  if (a >= 1e6) return `CHF ${(x / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `CHF ${(x / 1e3).toFixed(1)}k`;
  return chf(x);
};
const monthLabel = (m) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
};
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7"].map(css);
const OTHER = "#b5b3ab";
const RAMP = { low: "#86b6ef", medium: "#2a78d6", high: "#104281" }; // ordinal blue ramp (250 / 450 / 650)
const ICONS = {
  recurring_income: "💼", monthly_surplus: "📈", spending_exceeds_income: "⚠️", idle_cash: "💤", foreign_spend: "✈️",
  recurring_rent: "🏠", family: "👪", pillar_3a_gap: "🏦", salary_jump: "⬆️", new_employer: "🔄", move: "📦",
  debt_stress: "💳", existing_mortgage: "🏡", external_investments: "📊", high_net_worth: "💎", self_employed: "🧰", retired: "🌅",
};
const BASKET_ICONS = { "Pillar 3a": "🏦", Investment: "📈", Savings: "🐷", Cards: "💳", FX: "💱", "Mortgage & credit": "🏡", "Everyday banking": "🏧" };

const S = { status: null, spec: null, clientId: null, analysis: null, txns: null, charts: [], pendingOverrides: 0 };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const err = new Error(data?.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg, error = false) {
  const t = document.createElement("div");
  t.className = "toast" + (error ? " error" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), error ? 6000 : 3000);
}

function destroyCharts() {
  S.charts.forEach((c) => c.destroy());
  S.charts = [];
}

// ------------------------------------------------------------------ modal / jobs
function openModal(html) {
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function closeModal() {
  $("#modal").classList.add("hidden");
}
$("#modal-close").onclick = closeModal;
$("#modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };

async function runJob(path, title, body) {
  openModal(`<h2>${esc(title)}</h2><div class="progress"><div class="track"><i></i></div><ul id="job-log"></ul></div>`);
  $("#modal-close").classList.add("hidden");
  try {
    const { job_id } = await api(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
    for (;;) {
      const j = await api(`/api/jobs/${job_id}`);
      $("#job-log").innerHTML = j.messages.map((m) => `<li>${esc(m.msg)}</li>`).join("");
      if (j.status === "done") { closeModal(); return j; }
      if (j.status === "error") throw new Error(j.error);
      await new Promise((r) => setTimeout(r, 600));
    }
  } catch (e) {
    $(".progress .track")?.remove();
    $("#job-log").insertAdjacentHTML("beforeend", `<li style="color:var(--critical)">${esc(e.message)}</li>`);
    throw e;
  } finally {
    $("#modal-close").classList.remove("hidden");
  }
}

// ------------------------------------------------------------------ privacy gate
function modeLabel(m) {
  return { local: "Local processing · Switzerland", openai: "OpenAI cloud", off: "Rules only" }[m.mode] || m.mode;
}
function renderModeBadge() {
  const m = S.status.mode;
  $("#mode-text").textContent = modeLabel(m) + (m.available ? ` · ${m.model_fast}` : "");
  $("#mode-badge").classList.toggle("cloud", m.mode === "openai");
}
function showGate() {
  const m = S.status.modes;
  const choice = (key, main, title, text) => `
    <button class="choice ${main ? "main" : ""}" data-mode="${key}">
      <strong>${esc(title)}</strong><span>${esc(text)}</span>
    </button>`;
  $("#gate-choices").innerHTML =
    choice("local", true, "Start: all data handled in Switzerland by a local LLM",
      m.local.available ? `${m.local.provider}, model ${m.local.model_fast}. Nothing is sent abroad.` : m.local.note) +
    choice("openai", false, "Use OpenAI cloud models (demo data only)",
      m.openai.available ? `Models ${m.openai.model_fast} / ${m.openai.model_reasoning}. Transaction descriptions leave Switzerland.` : m.openai.note) +
    choice("off", false, "Rules only, no AI", "Deterministic categorisation and ranking.");
  $$("#gate-choices .choice").forEach((b) => (b.onclick = async () => {
    await api("/api/mode", { method: "POST", body: JSON.stringify({ mode: b.dataset.mode }) });
    S.status = await api("/api/status");
    renderModeBadge();
    $("#gate").classList.add("hidden");
  }));
  $("#gate").classList.remove("hidden");
}
$("#mode-badge").onclick = showGate;

// ------------------------------------------------------------------ routing
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "c" && parts[1]) return { client: decodeURIComponent(parts[1]), page: parts[2] || "overview" };
  return { client: null, page: "clients" };
}
const go = (client, page) => (location.hash = client ? `#/c/${encodeURIComponent(client)}/${page}` : "#/clients");

async function loadAnalysis(clientId, force = false) {
  if (!force && S.analysis && S.analysis.client_id === clientId) return S.analysis;
  S.analysis = await api(`/api/clients/${encodeURIComponent(clientId)}/analysis`);
  S.txns = null;
  return S.analysis;
}
async function loadTxns() {
  if (!S.txns) S.txns = await api(`/api/clients/${encodeURIComponent(S.clientId)}/transactions`);
  return S.txns;
}

function updateNav(r) {
  const has = !!(S.analysis && S.analysis.client_id === r.client);
  $$("#steps a").forEach((a) => {
    const step = a.dataset.step;
    a.classList.toggle("active", step === r.page);
    if (step !== "clients") {
      a.classList.toggle("disabled", !has);
      a.href = has ? `#/c/${encodeURIComponent(r.client)}/${step}` : "#";
    }
  });
  const p = has ? S.analysis.profile : null;
  $("#client-chip").textContent = p ? `${p.display_name} · ${p.occupation} · ${p.age_band}` : "";
}

async function render() {
  destroyCharts();
  const r = route();
  const view = $("#view");
  view.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    if (r.client) {
      S.clientId = r.client;
      try {
        await loadAnalysis(r.client);
      } catch (e) {
        if (e.status === 404) { go(null); return; }
        throw e;
      }
    }
    updateNav(r);
    const pages = { clients: viewClients, overview: viewOverview, cashflow: viewCashflow, review: viewReview,
                    suitability: viewSuitability, recommendations: viewRecommendations, report: viewReport };
    await (pages[r.page] || viewClients)(view);
    window.scrollTo(0, 0);
  } catch (e) {
    view.innerHTML = `<div class="card empty">Something went wrong: ${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", render);

function head(step, title, text, actions = "") {
  return `<div class="page-head"><div><div class="eyebrow">${esc(step)}</div><h1>${esc(title)}</h1>
    ${text ? `<p>${text}</p>` : ""}</div><div class="row">${actions}</div></div>`;
}

async function analyseClient(id) {
  await runJob(`/api/clients/${encodeURIComponent(id)}/analyse`, "Analysing transactions");
  await loadAnalysis(id, true);
}

// ------------------------------------------------------------------ 1 clients
async function viewClients(view) {
  const clients = await api("/api/clients");
  view.innerHTML = head("Step 1", "Choose a client",
      `${clients.length} synthetic client profiles with one year of account transactions. Pick one to start the check-up.`) +
    `<div class="grid auto" id="client-grid"></div>
     <div class="section card"><h2>Add a client</h2>
       <p class="muted small">CSV with columns <code>date, description, category, amount, currency, balance</code>.</p>
       <form id="upload" class="form-grid">
         <label class="field">CSV file<input type="file" name="file" accept=".csv" required></label>
         <label class="field">Name<input name="name" placeholder="e.g. Anna"></label>
         <label class="field">Age<input name="age" type="number" value="40" min="16" max="100"></label>
         <label class="field">Marital status<select name="marital_status"><option>single</option><option>married</option><option>divorced</option><option>widowed</option></select></label>
         <label class="field">Occupation<input name="occupation" placeholder="e.g. Engineer"></label>
         <div class="row" style="align-self:end"><button class="btn">Upload and validate</button></div>
       </form></div>`;
  $("#client-grid").innerHTML = clients.map((c) => `
    <div class="card client-card" data-id="${esc(c.client_id)}" data-run="${c.last_run ? 1 : 0}" tabindex="0">
      <div class="top"><div class="avatar">${esc(c.display_name.replace(/[^0-9A-Z]/g, "").slice(0, 3) || "C")}</div>
        ${c.last_run ? `<span class="chip good">${c.last_run.status === "recommended" ? "Advice ready" : "Analysed"}</span>` : `<span class="chip">New</span>`}</div>
      <h3>${esc(c.display_name)}</h3>
      <div class="meta">${esc(c.occupation)} · ${esc(c.age_band)} · ${esc(c.marital_status)}</div>
      <div><span class="chip">${esc(c.segment)}</span></div>
      <div class="row" style="margin-top:6px">
        <button class="btn small ${c.last_run ? "ghost" : "primary"}" data-act="analyse">${c.last_run ? "Re-analyse" : "Start check-up"}</button>
        ${c.last_run ? `<button class="btn small primary" data-act="open">Open</button>` : ""}
      </div>
    </div>`).join("");
  $$(".client-card").forEach((card) => {
    card.onclick = async (e) => {
      const id = card.dataset.id;
      const act = e.target.dataset?.act || (card.dataset.run === "1" ? "open" : "analyse");
      try {
        if (act === "analyse") await analyseClient(id);
        go(id, "overview");
      } catch (err) { toast(err.message, true); }
    };
  });
  $("#upload").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await fetch("/api/clients/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail);
      toast("File validated and added");
      await analyseClient(data.client_id);
      go(data.client_id, "overview");
    } catch (err) { toast(err.message, true); }
  };
}

// ------------------------------------------------------------------ 2 overview
function evidenceButton(ids, label) {
  return `<button class="chip" data-evidence="${esc(ids.join(","))}" data-title="${esc(label)}">🔎 ${ids.length} transaction${ids.length === 1 ? "" : "s"}</button>`;
}
function bindEvidence(root) {
  $$("[data-evidence]", root).forEach((b) => (b.onclick = () => showEvidence(b.dataset.title, b.dataset.evidence.split(",").filter(Boolean))));
}
async function showEvidence(title, ids) {
  const { transactions } = await loadTxns();
  const set = new Set(ids);
  const rows = transactions.filter((t) => set.has(t.id));
  openModal(`<h2>${esc(title)}</h2><p class="muted small">Evidence: the transactions that triggered this finding.</p>
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Description</th><th>Category</th><th class="num">Amount</th><th>Id</th></tr></thead>
    <tbody>${rows.map((t) => `<tr><td class="tabular">${t.date}</td><td>${esc(t.description)}${t.is_foreign ? ' <span class="chip">abroad</span>' : ""}</td>
      <td>${esc(t.category_label)}</td><td class="num ${t.amount > 0 ? "pos" : ""}">${num(t.amount, 2)}</td><td class="muted small">${t.id}</td></tr>`).join("")}
    </tbody></table></div>`);
}

async function viewOverview(view) {
  const a = S.analysis, k = a.kpis, tp = a.tax_plan;
  const idle = a.signals.find((s) => s.type === "idle_cash");
  const cs = a.classification_summary;
  view.innerHTML = head("Step 2", `Your overview`,
      `${esc(a.profile.display_name)}, ${esc(a.profile.occupation.toLowerCase())}. Twelve months of transactions
       (${k.window_start} to ${k.window_end}), analysed in ${esc(a.mode.provider)} mode.`,
      `<button class="btn ghost small" id="rerun">Re-analyse</button>`) +
    `<div class="tiles">
      <div class="tile hero-tile"><div class="label">Monthly surplus</div><div class="value">${compact(k.trailing_monthly_surplus)}</div><div class="sub">average of the last ${k.trailing_months.length} full months</div></div>
      <div class="tile"><div class="label">Savings rate</div><div class="value">${pct(k.trailing_savings_rate)}</div><div class="sub">of income, last ${k.trailing_months.length} months</div></div>
      <div class="tile"><div class="label">Income</div><div class="value">${compact(k.annual_income)}</div><div class="sub">last 12 months</div></div>
      <div class="tile"><div class="label">Spending</div><div class="value">${compact(k.annual_spending)}</div><div class="sub">last 12 months</div></div>
      <div class="tile"><div class="label">Already saved</div><div class="value">${compact(k.annual_saving)}</div><div class="sub">3a, pension, investments</div></div>
      <div class="tile"><div class="label">Idle cash</div><div class="value">${compact(idle ? idle.metrics.idle_cash : 0)}</div><div class="sub">balance ${compact(k.balance)} minus ${S.status.idle_cash_months}-month reserve</div></div>
    </div>
    ${tp.eligible && tp.additional_annual > 0 && !a.signals.some((s) => ["debt_stress", "spending_exceeds_income"].includes(s.type)) ? `
    <div class="card section" style="border-color:#f7c9c9">
      <div class="row" style="justify-content:space-between">
        <div><div class="eyebrow">Tax tip</div><h2 style="margin:0">Pay ${chf(tp.additional_annual)} more into Pillar 3a and save about ${chf(tp.additional_tax_saved_per_year)} tax a year</h2>
        <p class="muted small" style="margin:.3em 0 0">Recommended ${chf(tp.recommended_annual)} a year (maximum ${chf(tp.cap)}), ${chf(tp.tax_saved_per_year)} tax saved in total per year. Source: ${esc(tp.source)}.</p></div>
      </div></div>` : ""}
    <div class="section"><h2>What we noticed</h2>
      <p class="muted small">Each finding is computed by fixed rules and linked to the transactions behind it.</p>
      <div class="grid auto" id="signals"></div></div>
    <div class="section card row" style="justify-content:space-between">
      <div><strong>${num(Object.values(cs).reduce((x, y) => x + y, 0) - (cs.needs_review || 0))} transactions categorised</strong>
        <span class="muted"> · ${cs.rule || 0} by rules, ${cs.llm || 0} by AI, ${cs.fallback || 0} by bank category, ${cs.override || 0} by you</span>
        ${cs.needs_review ? `<br><span class="chip warn">${cs.needs_review} need your review</span>` : ""}</div>
      <div class="row"><a class="btn ghost" href="#/c/${encodeURIComponent(a.client_id)}/review">Check transactions</a>
      <a class="btn primary" href="#/c/${encodeURIComponent(a.client_id)}/cashflow">Next: income &amp; spending</a></div>
    </div>`;
  $("#signals").innerHTML = a.signals.map((s) => `
    <div class="card signal"><div class="head"><div class="ico">${ICONS[s.type] || "•"}</div><h3 style="margin:0">${esc(s.title)}</h3></div>
      <p>${esc(s.summary)}</p><div class="foot">${evidenceButton(s.evidence, s.title)}</div></div>`).join("") ||
    `<div class="card empty">No notable signals.</div>`;
  bindEvidence(view);
  $("#rerun").onclick = async () => { try { await analyseClient(a.client_id); render(); } catch (e) { toast(e.message, true); } };
}

// ------------------------------------------------------------------ 3 cash flow
function baseChartOptions(extra = {}) {
  const muted = css("--muted"), line = css("--line");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${chf(c.parsed.y)}` }, padding: 10, bodyFont: { family: css("--font") } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: muted }, border: { color: css("--axis") } },
      y: { grid: { color: line }, border: { display: false }, ticks: { color: muted, callback: (v) => num(v) } },
    },
    ...extra,
  };
}
function legendHtml(items) {
  return `<div class="legend">${items.map((i) => `<span class="key"><span class="${i.kind || "sw"}" style="background:${i.color}"></span>${esc(i.label)}</span>`).join("")}</div>`;
}

async function viewCashflow(view) {
  const a = S.analysis, months = a.months, labels = a.category_labels;
  const totals = {};
  months.forEach((m) => Object.entries(m.by_category).forEach(([c, v]) => (totals[c] = (totals[c] || 0) + v)));
  const top = Object.entries(totals).sort((x, y) => y[1] - x[1]).slice(0, 7).map(([c]) => c);
  const complete = months.filter((m) => m.complete);
  const defaultMonth = (complete[complete.length - 1] || months[months.length - 1]).month;
  const full = complete.length >= 3 ? complete : months; // partial first/last months would distort the charts
  const partial = months.filter((m) => !m.complete).map((m) => monthLabel(m.month));
  const xlabels = full.map((m) => monthLabel(m.month));

  view.innerHTML = head("Step 3", "Income & spending", `Where your money comes from and where it goes, month by month.
      ${partial.length && full !== months ? `Charts show complete months only (${partial.join(", ")} are partial).` : ""}`) +
    `<div class="grid cols-2">
      <div class="card"><h2>Spending by category</h2>${legendHtml([...top.map((c, i) => ({ label: labels[c], color: SERIES[i] })), { label: "Other", color: OTHER }])}
        <div class="chart-box"><canvas id="c-stack"></canvas></div></div>
      <div class="card"><h2>Income vs spending</h2>${legendHtml([{ label: "Income", color: SERIES[0], kind: "ln" }, { label: "Spending", color: SERIES[1], kind: "ln" }])}
        <div class="chart-box"><canvas id="c-lines"></canvas></div></div>
    </div>
    <div class="card section"><div class="row" style="justify-content:space-between"><h2 style="margin:0">One month in detail</h2>
      <select id="month-pick" class="btn ghost small">${months.map((m) => `<option value="${m.month}" ${m.month === defaultMonth ? "selected" : ""}>${monthLabel(m.month)}${m.complete ? "" : " (partial)"}</option>`).join("")}</select></div>
      <div class="grid cols-2" id="month-detail" style="margin-top:12px"></div></div>
    <div class="card section"><h2>Recurring flows</h2><p class="muted small">Same counterparty at a regular interval (±3 days) or once in almost every month.</p>
      <div class="table-wrap"><table><thead><tr><th>Counterparty</th><th>Direction</th><th>Category</th><th>Cadence</th><th class="num">Average</th><th class="num">Count</th><th class="num">Per year</th><th>Evidence</th></tr></thead>
      <tbody>${a.flows.map((f) => `<tr><td>${esc(f.counterparty)}</td><td>${f.direction === "in" ? "Income" : "Payment"}</td><td>${esc(labels[f.category] || f.category)}</td>
        <td>${f.cadence}${f.stable_amount ? "" : ' <span class="chip">varies</span>'}</td><td class="num">${num(f.avg_amount)}</td><td class="num">${f.count}</td><td class="num">${num(f.annualised)}</td>
        <td>${evidenceButton(f.evidence, f.counterparty)}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">No recurring flows.</td></tr>`}</tbody></table></div></div>`;

  const surface = css("--card");
  const datasets = [...top, "__other"].map((c, i) => ({
    label: c === "__other" ? "Other" : labels[c],
    data: full.map((m) => c === "__other"
      ? Object.entries(m.by_category).filter(([k]) => !top.includes(k)).reduce((s, [, v]) => s + v, 0)
      : m.by_category[c] || 0),
    backgroundColor: c === "__other" ? OTHER : SERIES[i],
    borderColor: surface, borderWidth: { top: 2, left: 0, right: 0, bottom: 0 }, borderSkipped: "start",
    maxBarThickness: 24,
    borderRadius: (ctx) => {
      const ds = ctx.chart.data.datasets;
      let topIdx = -1;
      ds.forEach((d, j) => { if ((d.data[ctx.dataIndex] || 0) > 0) topIdx = j; });
      return ctx.datasetIndex === topIdx ? { topLeft: 4, topRight: 4 } : 0;
    },
  }));
  S.charts.push(new Chart($("#c-stack"), { type: "bar", data: { labels: xlabels, datasets },
    options: baseChartOptions({ scales: { x: { stacked: true, grid: { display: false }, ticks: { color: css("--muted") } },
      y: { stacked: true, grid: { color: css("--line") }, border: { display: false }, ticks: { color: css("--muted"), callback: (v) => num(v) } } } }) }));
  const lineDs = (label, key, color) => ({ label, data: full.map((m) => m[key]), borderColor: color, backgroundColor: color, borderWidth: 2,
    pointRadius: 4, pointBorderColor: surface, pointBorderWidth: 2, tension: 0.25 });
  S.charts.push(new Chart($("#c-lines"), { type: "line", data: { labels: xlabels,
    datasets: [lineDs("Income", "income", SERIES[0]), lineDs("Spending", "spending", SERIES[1])] }, options: baseChartOptions() }));

  const detail = (month) => {
    const m = months.find((x) => x.month === month);
    const bars = (obj, color) => {
      const entries = Object.entries(obj).sort((x, y) => y[1] - x[1]);
      const max = Math.max(1, ...entries.map((e) => e[1]));
      return entries.map(([c, v]) => `<div style="display:grid;grid-template-columns:150px 1fr 90px;gap:8px;align-items:center;margin:5px 0;font-size:.86rem">
        <span>${esc(labels[c] || c)}</span><span style="height:10px;border-radius:0 4px 4px 0;background:${color};width:${(v / max) * 100}%"></span><span class="num tabular" style="text-align:right">${num(v)}</span></div>`).join("") || `<p class="muted">None</p>`;
    };
    $("#month-detail").innerHTML = `<div><h3>Spending ${chf(m.spending)}</h3>${bars(m.by_category, SERIES[1])}</div>
      <div><h3>Income ${chf(m.income)}</h3>${bars(m.income_by_category, SERIES[0])}
      <p class="small ink2" style="margin-top:14px">Surplus ${chf(m.surplus)} · savings rate ${pct(m.savings_rate)} · set aside ${chf(m.saving)}</p></div>`;
  };
  detail(defaultMonth);
  $("#month-pick").onchange = (e) => detail(e.target.value);
  bindEvidence(view);
}

// ------------------------------------------------------------------ 4 review
async function viewReview(view) {
  const { transactions, categories, threshold } = await loadTxns();
  const byKind = {};
  categories.forEach((c) => (byKind[c.kind] = byKind[c.kind] || []).push(c));
  const options = (sel) => Object.entries(byKind).map(([k, cs]) => `<optgroup label="${k}">${cs.map((c) =>
    `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</optgroup>`).join("");
  const nReview = transactions.filter((t) => t.needs_review).length;
  view.innerHTML = head("Step 4", "Check your transactions",
      `Every transaction was categorised by a rule, the AI model or, as a fallback, the bank's own category. Items below
       ${Math.round(threshold * 100)}% confidence are marked for review; you can correct any category.`) +
    `<div class="card"><div class="row" style="justify-content:space-between">
      <div class="row"><label class="row small"><input type="checkbox" id="only-review" ${nReview ? "checked" : ""}> Needs review only (${nReview})</label>
      <input id="search" placeholder="Search description" style="padding:6px 10px;border:1px solid var(--axis);border-radius:8px;font:inherit"></div>
      <div class="row"><span id="pending" class="chip warn hidden"></span><button class="btn primary small hidden" id="apply">Apply changes</button></div></div>
      <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Date</th><th>Description</th><th class="num">Amount</th><th>Category</th><th>Confidence</th><th>By</th><th>Why</th></tr></thead>
      <tbody id="tx-body"></tbody></table></div></div>`;
  const draw = () => {
    const only = $("#only-review").checked, q = $("#search").value.toLowerCase();
    const rows = transactions.filter((t) => (!only || t.needs_review) && (!q || t.description.toLowerCase().includes(q)));
    $("#tx-body").innerHTML = rows.slice(0, 600).map((t) => `<tr class="${t.needs_review ? "review" : ""}" data-id="${t.id}">
      <td class="tabular">${t.date}</td><td>${esc(t.description)}${t.is_foreign ? ' <span class="chip">abroad</span>' : ""}<div class="muted small">bank: ${esc(t.source_category)}</div></td>
      <td class="num ${t.amount > 0 ? "pos" : ""}">${num(t.amount, 2)}</td><td><select>${options(t.category)}</select></td>
      <td><div class="conf ${t.confidence < threshold ? "low" : ""}"><span class="bar"><i style="width:${Math.round(t.confidence * 100)}%"></i></span><span class="small tabular">${Math.round(t.confidence * 100)}%</span></div></td>
      <td><span class="chip">${t.classified_by}</span></td><td class="small ink2">${esc(t.reason)}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">Nothing to review. Untick the filter to see all transactions.</td></tr>`;
    $$("#tx-body select").forEach((sel) => (sel.onchange = async () => {
      const tr = sel.closest("tr");
      try {
        await api(`/api/clients/${encodeURIComponent(S.clientId)}/transactions/${tr.dataset.id}/override`, { method: "POST", body: JSON.stringify({ category: sel.value }) });
        tr.className = "changed";
        const t = transactions.find((x) => x.id === tr.dataset.id);
        Object.assign(t, { category: sel.value, needs_review: false, classified_by: "override", confidence: 1 });
        S.pendingOverrides++;
        $("#pending").textContent = `${S.pendingOverrides} change(s) saved`;
        $("#pending").classList.remove("hidden");
        $("#apply").classList.remove("hidden");
      } catch (e) { toast(e.message, true); }
    }));
  };
  $("#only-review").onchange = draw;
  $("#search").oninput = draw;
  $("#apply").onclick = async () => {
    try { await analyseClient(S.clientId); S.pendingOverrides = 0; toast("Analysis updated with your corrections"); render(); } catch (e) { toast(e.message, true); }
  };
  draw();
}

// ------------------------------------------------------------------ 5 suitability
function fieldHtml(f, value, src) {
  let input;
  if (f.type === "select") input = `<select name="${f.id}">${f.options.map((o) => `<option value="${o}" ${String(o) === String(value) ? "selected" : ""}>${esc(o.replace(/_/g, " "))}</option>`).join("")}</select>`;
  else if (f.type === "bool") input = `<select name="${f.id}"><option value="true" ${value ? "selected" : ""}>yes</option><option value="false" ${!value ? "selected" : ""}>no</option></select>`;
  else input = `<input name="${f.id}" type="${f.type === "number" ? "number" : "text"}" value="${esc(value)}">`;
  return `<label class="field">${esc(f.label)}${input}<span class="src">${esc(src || "")}</span></label>`;
}

async function viewSuitability(view) {
  S.spec = S.spec || (await api("/api/questionnaire"));
  const d = await api(`/api/clients/${encodeURIComponent(S.clientId)}/suitability`);
  const spec = S.spec, p = d.profile, ans = d.answers || {}, res = d.result;
  const risk = ans.risk || {}, know = ans.knowledge || {};
  view.innerHTML = head("Step 5", "Your profile & risk",
      "Swiss law (FinSA) requires us to check that advice fits your situation, goals and experience. Your answers stay with this record.",
      `<button class="btn ghost small" id="demo-fill" title="Fills plausible answers for this demo persona">Fill demo answers</button>`) +
    `<form id="q">
      <div class="card"><h2>About you</h2><p class="muted small">Pre-filled from your data; please correct anything that is wrong.</p>
        <div class="form-grid">${spec.profile_fields.map((f) => fieldHtml(f, (ans.profile || {})[f.id] ?? p[f.id], p.sources[f.id])).join("")}</div></div>
      <div class="card"><h2>Your attitude to risk</h2>${spec.risk_questions.map((q) => `<div class="question"><div class="q">${esc(q.text)}</div>
        <div class="options">${q.options.map(([t, v]) => `<label class="opt"><input type="radio" name="risk_${q.id}" value="${v}" ${String(risk[q.id]) === String(v) ? "checked" : ""}><span>${esc(t)}</span></label>`).join("")}</div></div>`).join("")}</div>
      <div class="card"><h2>Your knowledge and experience</h2><div class="table-wrap"><table class="kgrid"><thead><tr><th>Product type</th>
        ${spec.knowledge_levels.map(([, l]) => `<th>${esc(l)}</th>`).join("")}</tr></thead><tbody>
        ${spec.knowledge_categories.map(([c, l]) => `<tr><td>${esc(l)}</td>${spec.knowledge_levels.map(([lv]) =>
          `<td><input type="radio" name="k_${c}" value="${lv}" aria-label="${esc(l)}: ${lv}" ${know[c] === lv ? "checked" : ""}></td>`).join("")}</tr>`).join("")}
        </tbody></table></div></div>
      <div class="row section"><button class="btn primary" type="submit">Save my answers</button>
        ${d.answers_updated_at ? `<span class="muted small">Last saved ${esc(d.answers_updated_at)}</span>` : ""}</div>
    </form>
    <div id="result"></div>`;

  $("#demo-fill").onclick = () => {
    const demo = d.demo_answers;
    Object.entries(demo.risk).forEach(([q, v]) => { const el = $(`input[name="risk_${q}"][value="${v}"]`); if (el) el.checked = true; });
    Object.entries(demo.knowledge).forEach(([c, v]) => { const el = $(`input[name="k_${c}"][value="${v}"]`); if (el) el.checked = true; });
    toast("Demo answers filled in; review them and save");
  };
  $("#q").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const profile = {}, riskA = {}, knowA = {};
    spec.profile_fields.forEach((f) => {
      const v = fd.get(f.id);
      profile[f.id] = f.type === "number" ? Number(v) : f.type === "bool" ? v === "true" : v;
    });
    spec.risk_questions.forEach((q) => { if (fd.get(`risk_${q.id}`)) riskA[q.id] = Number(fd.get(`risk_${q.id}`)); });
    spec.knowledge_categories.forEach(([c]) => { if (fd.get(`k_${c}`)) knowA[c] = fd.get(`k_${c}`); });
    const missing = spec.risk_questions.length - Object.keys(riskA).length + spec.knowledge_categories.length - Object.keys(knowA).length;
    if (missing) toast(`${missing} question(s) still open; the risk profile needs all answers`, true);
    try {
      await runJob(`/api/clients/${encodeURIComponent(S.clientId)}/suitability`, "Saving answers and updating your figures",
        { profile, risk: riskA, knowledge: knowA });
      await loadAnalysis(S.clientId, true);
      render();
    } catch (err) { toast(err.message, true); }
  };

  if (!d.answers_updated_at) return;
  const labels = ["Conservative", "Cautious", "Balanced", "Growth", "Dynamic"];
  const meter = (score) => `<div class="meter">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= (score || 0) ? "on" : ""}"></i>`).join("")}</div>
    <div class="meter-labels"><span>${labels[0]}</span><span style="text-align:right">${labels[4]}</span></div>`;
  const cap = res.risk_capacity, m = res.mortgage;
  $("#result").innerHTML = `<div class="section grid cols-3">
      <div class="card"><div class="label muted small">Risk capacity (from your finances)</div><h2>${cap.score} / 5</h2>${meter(cap.score)}
        <ul class="small ink2" style="padding-left:18px;margin:10px 0 0">${Object.entries(cap.components).map(([k, v]) => `<li>${k.replace(/_/g, " ")}: ${v.score}/5 (${esc(v.basis)})</li>`).join("")}
        ${cap.caps.map((c) => `<li><strong>${esc(c)}</strong></li>`).join("")}</ul></div>
      <div class="card"><div class="label muted small">Risk tolerance (your answers)</div><h2>${res.risk_tolerance ?? "–"} / 5</h2>${meter(res.risk_tolerance)}
        <p class="small ink2">Investment horizon: ${res.horizon_years ?? "–"} years · financial assets seen: ${chf(res.financial_assets)}</p></div>
      <div class="card" style="border-color:var(--ink)"><div class="label muted small">Your risk profile = lower of the two</div>
        <h2>${res.risk_profile_label || "Incomplete"}</h2>${meter(res.risk_profile)}
        <p class="small ink2">Products riskier than this, or needing knowledge you have not declared, will be hidden with the reason.</p></div>
    </div>
    ${(m.purchase_ok || m.refinance_ok || m.reasons.length) ? `<div class="card section"><h3>Home financing check</h3>
      <p class="small ink2">Income supports a property up to ${chf(m.max_price_by_income)}; your savings cover the 20% equity for up to ${chf(m.max_price_by_equity)}.
      ${m.eligible ? `<span class="chip good">${m.mode === "refinancing" ? "Refinancing review possible" : "Purchase affordable"}</span>` : ""}</p>
      ${m.reasons.length ? `<ul class="small ink2">${m.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}</div>` : ""}
    <div class="card section"><h2>Information for you (FinSA)</h2><div class="notice">${esc(S.spec.notice.text)}</div>
      ${d.ack_current
        ? `<p class="chip good" style="margin-top:12px">✓ Acknowledged on ${esc(d.acknowledgement.acknowledged_at)} (notice ${esc(d.acknowledgement.notice_version)})</p>
           <div class="row" style="margin-top:12px"><button class="btn primary" id="get-recs">Show my recommendations</button></div>`
        : `<label class="check"><input type="checkbox" id="ack-box"><span>I have read and understood this information. My answers are complete and correct.</span></label>
           <button class="btn primary" id="ack" disabled>Acknowledge</button>
           ${d.acknowledgement ? `<p class="muted small">Your answers changed after the last acknowledgement (${esc(d.acknowledgement.acknowledged_at)}), so please confirm again.</p>` : ""}`}
    </div>`;
  if ($("#ack-box")) {
    $("#ack-box").onchange = (e) => ($("#ack").disabled = !e.target.checked || !res.complete);
    $("#ack").onclick = async () => {
      try { await api(`/api/clients/${encodeURIComponent(S.clientId)}/acknowledge`, { method: "POST" }); render(); } catch (e) { toast(e.message, true); }
    };
    if (!res.complete) $("#ack").insertAdjacentHTML("afterend", `<span class="muted small" style="margin-left:10px">Answer all questions first.</span>`);
  }
  if ($("#get-recs")) $("#get-recs").onclick = async () => {
    try { await runJob(`/api/clients/${encodeURIComponent(S.clientId)}/recommend`, "Preparing your recommendations"); go(S.clientId, "recommendations"); }
    catch (e) { toast(e.message, true); }
  };
}

// ------------------------------------------------------------------ 6 recommendations
function projectionChart(canvas, pr) {
  const labels = pr.years.map((y) => (y === 0 ? "Now" : `${y}y`));
  const ds = [];
  if (pr.monte_carlo) {
    ds.push({ label: "10th percentile", data: pr.monte_carlo.p10, borderWidth: 0, pointRadius: 0, fill: false, borderColor: "transparent" });
    ds.push({ label: "90th percentile", data: pr.monte_carlo.p90, borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: "rgba(42,120,214,0.10)", borderColor: "transparent" });
  }
  ds.push({ label: "Paid in", data: pr.contributions, borderColor: css("--muted"), borderWidth: 1.5, pointRadius: 0, borderDash: [4, 3] });
  ["low", "medium", "high"].forEach((k) => ds.push({ label: `${k[0].toUpperCase() + k.slice(1)} (${pr.assumptions[`return_${k}_pct`]}% p.a.)`,
    data: pr.scenarios[k], borderColor: RAMP[k], backgroundColor: RAMP[k], borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0.2 }));
  const opts = baseChartOptions();
  opts.scales.y.beginAtZero = true;
  S.charts.push(new Chart(canvas, { type: "line", data: { labels, datasets: ds }, options: opts }));
}

async function viewRecommendations(view) {
  let r;
  try { r = await api(`/api/clients/${encodeURIComponent(S.clientId)}/recommendations`); }
  catch (e) {
    if (e.status !== 404) throw e;
    view.innerHTML = head("Step 6", "Your recommendations", "") + `<div class="card empty">Complete your profile, the risk questions and the FinSA
      acknowledgement first.<br><br><a class="btn primary" href="#/c/${encodeURIComponent(S.clientId)}/suitability">Go to step 5</a></div>`;
    return;
  }
  const su = r.suitability, tp = r.tax_plan;
  view.innerHTML = head("Step 6", "Your recommendations",
      `Grouped into baskets and ranked for you. Only products that passed the suitability and appropriateness checks are shown.`,
      `<button class="btn ghost small" id="refresh">Refresh</button>`) +
    `<div class="card"><div class="row"><span class="chip red">Risk profile: ${esc(su.risk_profile_label)}</span><span class="chip">Horizon ${su.horizon_years} years</span>
      <span class="chip">Ranked by ${esc(r.ranked_by)}</span>${r.rejected_by_validation.length ? `<span class="chip warn">${r.rejected_by_validation.length} AI suggestion(s) rejected by validation</span>` : ""}</div>
      ${r.summary ? `<p class="ink2" style="margin:12px 0 0">${esc(r.summary)}</p>` : ""}</div>
    <div id="baskets"></div>
    ${r.hidden.length ? `<div class="card section"><details><summary>Not shown to you (${r.hidden.length}) and why</summary>
      <ul class="hidden-list small" style="margin-top:12px">${r.hidden.map((h) => `<li><strong>${esc(h.name)}</strong> <span class="muted">(${esc(h.basket || "")})</span><br>
      <span class="ink2">${h.reasons.map(esc).join("<br>")}</span></li>`).join("")}</ul></details></div>` : ""}
    <div class="row section"><a class="btn primary" href="#/c/${encodeURIComponent(S.clientId)}/report">Next: advice record</a></div>`;

  $("#baskets").innerHTML = r.baskets.map((b, bi) => {
    const pr = r.projections[b.basket];
    const is3a = b.basket === "Pillar 3a" && tp.eligible;
    const isMortgage = b.basket === "Mortgage & credit";
    return `<div class="card basket"><div class="basket-head"><div class="ico">${BASKET_ICONS[b.basket] || "•"}</div>
        <div><h2 style="margin:0">${esc(b.basket)}</h2><span class="muted small">${b.products.length} product${b.products.length > 1 ? "s" : ""}</span></div></div>
      ${is3a ? `<div class="tiles" style="margin-bottom:12px">
        <div class="tile"><div class="label">Recommended per year</div><div class="value">${chf(tp.recommended_annual)}</div><div class="sub">${chf(tp.recommended_monthly)} a month · max ${chf(tp.cap)}</div></div>
        <div class="tile"><div class="label">Tax saved per year</div><div class="value">${chf(tp.tax_saved_per_year)}</div><div class="sub">${chf(tp.additional_tax_saved_per_year)} more than today</div></div>
        <div class="tile"><div class="label">Tax saved until ${tp.years_to_retirement > 0 ? "retirement" : "end"}</div><div class="value">${compact(tp.cumulative_tax_saved)}</div><div class="sub">${tp.years_to_retirement} years, nominal</div></div>
        <div class="tile"><div class="label">Marginal tax rate</div><div class="value">${pct(tp.marginal_rate, 1)}</div><div class="sub">${esc(tp.location.city)} (${esc(tp.location.canton)})</div></div></div>
        <p class="muted small">Tax source: ${esc(tp.source)}.${tp.retroactive.possible_up_to ? ` Optional: a retroactive contribution for ${tp.retroactive.year} of up to ${chf(tp.retroactive.possible_up_to)}${tp.retroactive.tax_saved_estimate ? ` could save about ${chf(tp.retroactive.tax_saved_estimate)} more tax` : ""}. ${esc(tp.retroactive.note)}` : ""}</p>` : ""}
      ${isMortgage ? `<p class="small ink2">Affordability: income supports up to ${chf(su.mortgage.max_price_by_income)}, equity up to ${chf(su.mortgage.max_price_by_equity)}
        (${pct(su.mortgage.assumptions.imputed_rate)} imputed interest, ${pct(su.mortgage.assumptions.maintenance_rate)} maintenance, max ${pct(su.mortgage.assumptions.affordability_max_share)} of income).</p>` : ""}
      ${b.products.map((p, i) => `${i === 3 ? `<details><summary class="small">${b.products.length - 3} more option(s) in this basket</summary>` : ""}
        <div class="product ${i ? "secondary" : ""}"><div class="rank">${p.rank}</div><div>
        <h3>${esc(p.name)} ${i === 0 ? '<span class="chip red">Top pick</span>' : ""}</h3>
        <div class="muted small">${esc(p.product_type)} · ${esc(p.price)}</div>
        <p>${esc(p.rationale)}</p>
        <div class="facts">${p.holding_period_years ? `<span class="chip">Hold ${p.holding_period_years} years</span>` : ""}
          ${p.risk_class ? `<span class="chip">Risk ${p.risk_class}/5</span>` : ""}
          ${p.returns ? `<span class="chip">Return ${p.returns.low}–${p.returns.high}% p.a. (illustrative)</span>` : ""}
          ${p.cited_signals.map((s) => `<span class="chip">${ICONS[s] || ""} ${esc(s.replace(/_/g, " "))}</span>`).join("")}
          ${p.evidence.length ? evidenceButton(p.evidence, p.name) : ""}
          ${(p.profile_basis || []).map((x) => `<span class="chip">👤 ${esc(x)}</span>`).join("")}
          <a class="chip" href="${esc(p.url)}" target="_blank" rel="noopener">Product page ↗</a></div></div></div>
        ${i === b.products.length - 1 && i >= 3 ? "</details>" : ""}`).join("")}
      ${pr ? `<div class="section"><h3>Projection: ${esc(pr.product_name)}</h3>
        ${legendHtml([{ label: `High (${pr.assumptions.return_high_pct}%)`, color: RAMP.high, kind: "ln" }, { label: `Medium (${pr.assumptions.return_medium_pct}%)`, color: RAMP.medium, kind: "ln" },
          { label: `Low (${pr.assumptions.return_low_pct}%)`, color: RAMP.low, kind: "ln" }, { label: "Paid in", color: css("--muted"), kind: "ln dash" },
          ...(pr.monte_carlo ? [{ label: "Market range, 10th–90th percentile (Monte Carlo)", color: "rgba(42,120,214,0.18)", kind: "band" }] : [])])}
        <div class="chart-box"><canvas id="proj-${bi}"></canvas></div>
        <table class="assump" style="margin-top:10px"><tr><td class="muted">Start amount</td><td>${chf(pr.assumptions.initial)}</td><td class="muted">Monthly</td><td>${chf(pr.assumptions.monthly)}</td>
          <td class="muted">Years</td><td>${pr.assumptions.years}</td></tr>
          <tr><td class="muted">Running costs</td><td>${pr.assumptions.running_costs_pct}% p.a.</td><td class="muted">Volatility</td><td>${pr.assumptions.volatility_pct}%</td>
          <td class="muted">After ${pr.assumptions.years} years</td><td>${compact(pr.final.low)} / ${compact(pr.final.medium)} / ${compact(pr.final.high)}</td></tr></table>
        <p class="muted small">${esc(pr.assumptions.note)} Paid in total: ${chf(pr.total_contributed)}.</p></div>` : ""}
    </div>`;
  }).join("") || `<div class="card empty">No products passed the checks for your profile.</div>`;
  r.baskets.forEach((b, bi) => { const pr = r.projections[b.basket]; if (pr) projectionChart($(`#proj-${bi}`), pr); });
  bindEvidence(view);
  $("#refresh").onclick = async () => {
    try { await runJob(`/api/clients/${encodeURIComponent(S.clientId)}/recommend`, "Updating your recommendations"); render(); } catch (e) { toast(e.message, true); }
  };
}

// ------------------------------------------------------------------ 7 report
async function viewReport(view) {
  const a = await api(`/api/clients/${encodeURIComponent(S.clientId)}/audit`);
  view.innerHTML = head("Step 7", "Your advice record",
      "A PDF with your profile, the findings and their evidence, the suitability checks, recommendations, projections and the audit trail.") +
    `<div class="card row" style="justify-content:space-between"><div><strong>Advice record for run ${esc(a.run_id)}</strong>
      <div class="muted small">Generated on download from the stored run.</div></div>
      <a class="btn primary" href="/api/clients/${encodeURIComponent(S.clientId)}/report.pdf">Download PDF</a></div>
    <div class="card section"><h2>AI calls (audit log)</h2><p class="muted small">Append-only: the database rejects any change or deletion.</p>
      <div class="table-wrap"><table><thead><tr><th>Time</th><th>Purpose</th><th>Provider</th><th>Model</th><th>Prompt</th><th>Input hash</th><th class="num">Tokens</th><th class="num">CHF</th><th>Status</th></tr></thead>
      <tbody>${a.llm_calls.map((c) => `<tr><td class="tabular small">${esc(c.ts)}</td><td>${esc(c.purpose)}</td><td>${esc(c.provider)}</td><td>${esc(c.model)}</td><td>${esc(c.prompt_version)}</td>
        <td class="small muted">${esc(c.input_sha256.slice(0, 12))}…</td><td class="num">${c.prompt_tokens ?? 0}/${c.completion_tokens ?? 0}</td><td class="num">${(c.cost_chf ?? 0).toFixed(4)}</td>
        <td>${c.status === "ok" ? '<span class="chip good">ok</span>' : `<span class="chip warn" title="${esc(c.error)}">${esc(c.status)}</span>`}</td></tr>`).join("") ||
        `<tr><td colspan="9" class="muted">No AI calls for this client (rules and cache only).</td></tr>`}</tbody></table></div></div>
    <div class="card section"><h2>Events</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Event</th><th>Run</th></tr></thead>
      <tbody>${a.events.map((e) => `<tr><td class="tabular small">${esc(e.ts)}</td><td>${esc(e.kind.replace(/_/g, " "))}</td><td class="small muted">${esc(e.run_id || "")}</td></tr>`).join("")}</tbody></table></div></div>`;
}

// ------------------------------------------------------------------ boot
(async function boot() {
  Chart.defaults.font.family = css("--font");
  Chart.defaults.color = css("--muted");
  S.status = await api("/api/status");
  renderModeBadge();
  if (!S.status.chosen) showGate();
  render();
})();
