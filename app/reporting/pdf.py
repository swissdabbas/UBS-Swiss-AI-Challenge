"""FinSA advice record (PDF) for one client run, including the LLM audit trail."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

from .. import db

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
RED = (230, 0, 0)
GREY = (110, 110, 110)
LINE = (220, 220, 220)
SCEN_COLORS = {"low": (160, 160, 160), "medium": (230, 0, 0), "high": (60, 60, 60)}


def _chf(x) -> str:
    if x is None:
        return "-"
    return f"CHF {x:,.0f}".replace(",", "'")


class Report(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.title_text = title
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(True, 18)
        if (FONT_DIR / "DejaVuSans.ttf").exists():
            self.add_font("body", "", str(FONT_DIR / "DejaVuSans.ttf"))
            self.add_font("body", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
            self.unicode = True
        else:
            self.unicode = False
        self.fam = "body" if self.unicode else "helvetica"

    def txt(self, s) -> str:
        s = "" if s is None else str(s)
        if self.unicode:
            return s
        for a, b in (("–", "-"), ("—", "-"), ("’", "'"), ("“", '"'), ("”", '"'), ("…", "..."), ("≥", ">="), ("≤", "<=")):
            s = s.replace(a, b)
        return s.encode("latin-1", "replace").decode("latin-1")

    def header(self):
        self.set_font(self.fam, "B", 9)
        self.set_text_color(*RED)
        self.cell(0, 6, self.txt("Advice record"), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*RED)
        self.line(16, self.get_y(), 194, self.get_y())
        self.ln(3)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-12)
        self.set_font(self.fam, "", 7)
        self.set_text_color(*GREY)
        self.cell(0, 5, self.txt(f"{self.title_text}  ·  page {self.page_no()}"), align="R")

    def h1(self, s):
        self.set_font(self.fam, "B", 16)
        self.multi_cell(0, 8, self.txt(s), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def h2(self, s):
        if self.get_y() > 250:
            self.add_page()
        self.ln(3)
        self.set_font(self.fam, "B", 12)
        self.set_text_color(*RED)
        self.cell(0, 7, self.txt(s), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def p(self, s, size=9, color=(0, 0, 0), bold=False):
        self.set_font(self.fam, "B" if bold else "", size)
        self.set_text_color(*color)
        self.multi_cell(0, 4.6, self.txt(s), new_x="LMARGIN", new_y="NEXT", align="L")
        self.set_text_color(0, 0, 0)

    def table(self, headers, rows, widths, size=8):
        self.set_font(self.fam, "B", size)
        self.set_fill_color(245, 245, 245)
        for h, w in zip(headers, widths):
            self.cell(w, 5.5, self.txt(h), border="B", fill=True, align="L")
        self.ln()
        self.set_font(self.fam, "", size)
        lh = size * 0.5
        for r in rows:
            vals = [self.txt(v) for v in r]
            h = max([len(self.multi_cell(w, lh, v, dry_run=True, output="LINES", align="L")) * lh for v, w in zip(vals, widths)] + [5])
            if self.get_y() + h > 278:
                self.add_page()
                self.set_font(self.fam, "", size)
            y0, x = self.get_y(), self.l_margin
            for v, w in zip(vals, widths):
                self.set_xy(x, y0)
                self.multi_cell(w, lh, v, align="L")
                x += w
            self.set_xy(self.l_margin, y0 + h)
            self.set_draw_color(*LINE)
            self.line(self.l_margin, self.get_y(), self.l_margin + sum(widths), self.get_y())
        self.ln(1)

    def chart(self, proj: dict, w=178, h=58):
        if self.get_y() + h + 12 > 275:
            self.add_page()
        x0, y0 = self.l_margin + 12, self.get_y() + 2
        cw, ch = w - 14, h
        series = dict(proj["scenarios"])
        mc = proj.get("monte_carlo")
        allv = [v for s in series.values() for v in s] + proj["contributions"] + (mc["p90"] if mc else [])
        vmax = max(allv) or 1
        n = len(proj["years"]) - 1 or 1
        X = lambda i: x0 + cw * i / n  # noqa: E731
        Y = lambda v: y0 + ch - ch * v / vmax  # noqa: E731
        self.set_draw_color(*LINE)
        self.set_font(self.fam, "", 6)
        for k in range(5):
            v = vmax * k / 4
            self.line(x0, Y(v), x0 + cw, Y(v))
            self.set_xy(self.l_margin - 2, Y(v) - 2)
            self.cell(13, 4, self.txt(f"{v/1000:,.0f}k"), align="R")
        if mc:
            self.set_draw_color(240, 180, 180)
            self.set_dash_pattern(dash=1, gap=1)
            for key in ("p10", "p90"):
                for i in range(n):
                    self.line(X(i), Y(mc[key][i]), X(i + 1), Y(mc[key][i + 1]))
            self.set_dash_pattern()
        self.set_draw_color(120, 120, 220)
        for i in range(n):
            self.line(X(i), Y(proj["contributions"][i]), X(i + 1), Y(proj["contributions"][i + 1]))
        for name, vals in series.items():
            self.set_draw_color(*SCEN_COLORS[name])
            self.set_line_width(0.5 if name == "medium" else 0.3)
            for i in range(n):
                self.line(X(i), Y(vals[i]), X(i + 1), Y(vals[i + 1]))
        self.set_line_width(0.2)
        self.set_xy(x0, y0 + ch + 1)
        self.set_font(self.fam, "", 6.5)
        self.set_text_color(*GREY)
        self.cell(cw, 4, self.txt(f"Years 0-{n}.  Lines: high (dark), medium (red), low (grey), contributions (blue); "
                                  f"dotted: Monte Carlo 10th/90th percentile."), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.set_y(y0 + ch + 7)


def build(run: dict) -> bytes:
    a, rec = run["analysis"], run.get("recommendation")
    p, k = a["profile"], a["kpis"]
    tx = {t["id"]: t for t in a["transactions"]}
    doc = Report(f"{p['display_name']} · run {a['run_id']}")
    doc.add_page()
    doc.h1(f"Advice record: {p['display_name']}")
    doc.p(f"Run {a['run_id']} · analysis {a['created_at']} · recommendations {rec['created_at'] if rec else 'not yet generated'}", 8, GREY)
    m = a["mode"]
    doc.p(f"Processing: {m['provider']} · classification model {m['model_fast']} · ranking model "
          f"{(rec or {}).get('ranked_by', m['model_reasoning'])} · prompt versions "
          f"{', '.join(f'{k2}={v}' for k2, v in a['prompt_versions'].items())}", 8, GREY)

    doc.h2("1. Client profile")
    fields = ["age", "marital_status", "children", "zip", "city", "canton", "confession", "employment_type",
              "has_pension_fund", "other_assets"]
    doc.table(["Field", "Value", "Source"], [[f.replace("_", " "), p.get(f), p["sources"].get(f, "")] for f in fields],
              [40, 45, 93])
    doc.p(f"Occupation: {p['occupation']} · segment: {p['segment']} · data window {k['window_start']} to {k['window_end']}.", 8)

    doc.h2("2. Key figures (last 12 months; surplus and savings rate: last 6 full months)")
    rate = k.get("trailing_savings_rate")
    doc.table(["Income", "Spending", "Saved", "Surplus / month", "Savings rate", "Balance"],
              [[_chf(k["annual_income"]), _chf(k["annual_spending"]), _chf(k["annual_saving"]),
                _chf(k["trailing_monthly_surplus"]), f"{rate:.0%}" if rate is not None else "-", _chf(k["balance"])]],
              [29, 29, 30, 32, 29, 29])

    doc.h2("3. Detected signals and evidence")
    for s in a["signals"]:
        doc.p(f"{s['title']}", 9.5, bold=True)
        doc.p(s["summary"], 8.5)
        ev = [tx[e] for e in s["evidence"][:5] if e in tx]
        if ev:
            doc.table(["Transaction id", "Date", "Description", "Amount"],
                      [[t["id"], t["date"], t["description"], _chf(t["amount"])] for t in ev], [30, 24, 94, 30], 7)
        if len(s["evidence"]) > 5:
            doc.p(f"+ {len(s['evidence']) - 5} more evidence transactions: {', '.join(s['evidence'][5:20])}"
                  f"{' ...' if len(s['evidence']) > 20 else ''}", 7, GREY)

    tp = a["tax_plan"]
    doc.h2("4. Pillar 3a and tax")
    if tp.get("eligible"):
        doc.table(["Maximum", "Paid (12m)", "Recommended / year", "Tax saved / year", "Marginal rate", f"Cumulative ({tp['years_to_retirement']}y)"],
                  [[_chf(tp["cap"]), _chf(tp["paid_12m"]), _chf(tp["recommended_annual"]), _chf(tp["tax_saved_per_year"]),
                    f"{tp['marginal_rate']:.1%}", _chf(tp["cumulative_tax_saved"])]], [28, 28, 32, 30, 28, 32])
        doc.p(f"Cap basis: {tp['cap_basis']}. Tax source: {tp['source']}. Location {tp['location']['zip']} "
              f"{tp['location']['city']} ({tp['location']['canton']}), tax year {tp['tax_year']}.", 8)
        r = tp["retroactive"]
        if r["possible_up_to"]:
            doc.p(f"Optional: retroactive contribution for {r['year']} up to {_chf(r['possible_up_to'])}"
                  f"{' (tax saved about ' + _chf(r['tax_saved_estimate']) + ')' if r.get('tax_saved_estimate') else ''}. {r['note']}", 8)
    else:
        doc.p(f"Not eligible for Pillar 3a: {tp['reason']}", 9)

    if not rec:
        doc.h2("5. Recommendations")
        doc.p("The client has not yet completed the suitability questionnaire and acknowledgement.", 9)
    else:
        su = rec["suitability"]
        doc.h2("5. Suitability and appropriateness (FinSA)")
        comp = su["risk_capacity"]["components"]
        doc.table(["Risk capacity component", "Score", "Basis"],
                  [[c.replace("_", " "), v["score"], v["basis"]] for c, v in comp.items()] +
                  [["risk capacity (overall)", su["risk_capacity"]["score"], "; ".join(su["risk_capacity"]["caps"]) or "-"],
                   ["risk tolerance (questionnaire)", su["risk_tolerance"], "mean of 5 answers"],
                   ["risk profile", su["risk_profile"], f"{su['risk_profile_label']} = min(capacity, tolerance)"],
                   ["investment horizon", f"{su['horizon_years']} y", "questionnaire"]],
                  [55, 18, 105])
        doc.p("Declared knowledge and experience: " + ", ".join(f"{c.replace('_', ' ')}: {v}" for c, v in su["knowledge"].items()), 8)
        ack = rec.get("acknowledgement")
        doc.p(f"FinSA notice {ack['notice_version']} (sha256 {ack['notice_sha256'][:16]}...) acknowledged by the client at "
              f"{ack['acknowledged_at']}." if ack else "No acknowledgement stored.", 8.5, bold=True)

        doc.h2("6. Recommended products")
        if rec.get("summary"):
            doc.p(rec["summary"], 8.5)
        for b in rec["baskets"]:
            doc.p(b["basket"], 10, RED, bold=True)
            doc.table(["#", "Product", "Rationale", "Holding", "Evidence"],
                      [[i["rank"], i["name"], i["rationale"],
                        f"{i['holding_period_years']:g} y" if i.get("holding_period_years") else "-",
                        ", ".join(i["cited_signals"]) + f" ({len(i['evidence'])} txns)"] for i in b["products"]],
                      [7, 38, 90, 15, 28], 7.5)
        doc.p(f"Ranking: {rec['ranked_by']}. Products outside the catalog rejected by validation: "
              f"{len(rec.get('rejected_by_validation', []))}.", 7.5, GREY)
        if rec["hidden"]:
            doc.h2("7. Products not shown and why")
            doc.table(["Product", "Reason(s)"], [[h["name"], " ".join(h["reasons"])] for h in rec["hidden"]], [50, 128], 7.5)

        if rec.get("projections"):
            doc.h2("8. Projections and assumptions")
            for name, pr in rec["projections"].items():
                a_ = pr["assumptions"]
                doc.p(f"{name}: {pr['product_name']}", 9.5, bold=True)
                doc.p(f"Start {_chf(a_['initial'])}, then {_chf(a_['monthly'])} a month for {a_['years']} years. "
                      f"Expected return low/medium/high {a_['return_low_pct']}% / {a_['return_medium_pct']}% / "
                      f"{a_['return_high_pct']}% p.a., running costs {a_['running_costs_pct']}% p.a., volatility "
                      f"{a_['volatility_pct']}%. {a_['note']}", 8)
                doc.chart(pr)
                yrs = sorted({0, len(pr["years"]) // 2, len(pr["years"]) - 1})
                mc = pr.get("monte_carlo")
                doc.table(["Year", "Paid in", "Low", "Medium", "High", "MC 10%", "MC 90%"],
                          [[y, _chf(pr["contributions"][y]), _chf(pr["scenarios"]["low"][y]), _chf(pr["scenarios"]["medium"][y]),
                            _chf(pr["scenarios"]["high"][y]), _chf(mc["p10"][y]) if mc else "-", _chf(mc["p90"][y]) if mc else "-"]
                           for y in yrs], [14, 27, 27, 27, 27, 28, 28], 7.5)
                if pr.get("tax_saved"):
                    doc.p(f"Tax saved: {_chf(pr['tax_saved']['per_year'])} a year, {_chf(pr['tax_saved']['cumulative'][-1])} "
                          f"over {len(pr['years']) - 1} years (nominal). Source: {pr['tax_saved']['source']}.", 8)

    doc.h2("9. Audit trail (LLM calls)")
    # classifications are cached across runs, so list every call made for this client
    calls = db.fetchall("SELECT ts, run_id, purpose, provider, model, prompt_version, input_sha256, prompt_tokens, "
                        "completion_tokens, cost_chf, status FROM llm_audit WHERE client_id=? ORDER BY id", (a["client_id"],))
    if calls:
        doc.table(["Time (UTC)", "Run", "Purpose", "Model", "Prompt", "Input sha256", "Tokens", "CHF", "Status"],
                  [[c["ts"][:19].replace("T", " "), c["run_id"] or "", c["purpose"], c["model"], c["prompt_version"],
                    c["input_sha256"][:12], f"{c['prompt_tokens']}/{c['completion_tokens']}", f"{c['cost_chf']:.4f}", c["status"]]
                   for c in calls],
                  [27, 30, 14, 18, 17, 22, 18, 14, 13], 6.5)
        doc.p(f"Total LLM cost for this client: CHF {sum(c['cost_chf'] or 0 for c in calls):.4f}.", 7.5, GREY)
    else:
        doc.p("No LLM calls for this client (rules only).", 8.5)
    doc.p(f"Transactions classified: {a['classification_summary']}. The audit log is append-only (enforced by database triggers).", 7.5, GREY)
    doc.ln(3)
    doc.p("Demo record generated from synthetic data. Return scenarios and product attributes are illustrative "
          "assumptions, not UBS figures; tax figures come from the ESTV calculator or pre-computed ESTV tables.", 7, GREY)
    return bytes(doc.output())
