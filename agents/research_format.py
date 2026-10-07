"""Research output format.

Claude submits the narrative pieces (ResearchWriteup). This module assembles them, plus tables
rendered from the computed financial model, into the research record served by the API:

{
  "long_form_memo": "<2-3 page markdown memo in the style of the Substack BLSH piece>",
  "executive_summary": "<1-2 paragraph elevator pitch>",
  "financial_model": {"revenue_2026E": "$...", "revenue_2027E": "$...", "ebitda_margin": "...%",
                      "fcf": "$...", "valuation_range": "$...B"},
  "key_risks": ["..."],
  "conviction_level": 7,
  "reasoning": "Why this conviction level"
}
"""

from typing import Literal

from pydantic import BaseModel, Field

from agents.financial_model import fmt_money_mm, fmt_pct, fmt_price, summary_for_research
from agents.thesis import TradingThesisOut


class CompanyBackground(BaseModel):
    overview: str = Field(description="What the company (or index/instrument) is, in one paragraph")
    business_model: str = Field(description="How it makes money: segments, pricing, customers, unit economics")
    key_people: list[str] = Field(description="'Name, role: why they matter' from filings/news; empty if unknown")
    history_and_structure: str = Field(description="Key history, listing, ownership/control, major deals")


class KeyMetric(BaseModel):
    label: str = Field(description="e.g. 'Subscription & services mix'")
    value: str = Field(description="e.g. '68% of adjusted revenue (vs 57% a year ago)'")
    why_it_matters: str


class MacroScenario(BaseModel):
    name: str
    probability_pct: float
    description: str
    implications: str = Field(description="What it means for the coverage names / sector")


class FocusMetrics(BaseModel):
    revenue_growth: str
    margins: str
    user_adoption: str
    competitive_threats: str


class ImpliedValue(BaseModel):
    method: Literal["public_comps", "private_marks", "dcf_sanity_check"]
    multiple: float | None = Field(description="Multiple applied (e.g. EV/revenue), or null for the DCF check")
    metric: str = Field(description="What it is applied to, e.g. 'est. 2026 revenue $1.2B'")
    implied_value_mm: float | None = Field(description="Implied enterprise value, $mm")
    basis: str = Field(description="Which peers / marks / assumptions, with sources")


class PrivateCompanyValuation(BaseModel):
    last_mark_mm: float | None = Field(description="Latest private valuation (round, secondary or deal), $mm")
    last_mark_date: str
    last_mark_source: str = Field(description="Headline/filing in the data, or 'general knowledge, unverified'")
    estimated_revenue_mm: float | None = Field(description="Best estimate of current revenue or run-rate, $mm")
    revenue_basis: str
    methods: list[ImpliedValue] = Field(description="Value it all three ways: public comps, private marks, DCF sanity check")
    verdict: Literal["overpriced", "fair", "underpriced", "not enough data"] = Field(
        description="The last private mark vs what public comps and the DCF say")
    read_through: str = Field(description="What this private mark means for the public names in the same bucket: who looks rich "
                                          "or cheap against it, and any pair it suggests")


class ResearchWriteup(BaseModel):
    title: str = Field(description="Memo headline, e.g. 'Bullish (BLSH): Narrative Trade'")
    subtitle: str = Field(description="One sentence that frames the argument")
    executive_summary: str = Field(
        description="1-2 paragraph elevator pitch a PM can read in 30 seconds: the thesis, why it matters now, "
        "and the single number that proves it. No headings or bullets."
    )
    key_metric: KeyMetric
    stance: Literal["bullish", "neutral", "bearish"]
    conviction_level: int = Field(description="1 (very low) to 10 (very high): strength of evidence, not degree of bullishness")
    reasoning: str = Field(description="Why this conviction level: what supports it, what caps it, and what would move it")
    setup: list[str] = Field(description="The narrative thesis: 3-4 paragraphs, each a full paragraph")
    evidence: list[str] = Field(
        description="What the latest print / data actually showed: 4-8 bullets, each with specific numbers and the "
        "contrarian read where the headline misleads"
    )
    what_changed: str = Field(description="What changed since the previous memo and whether the view moved. For initial coverage: why now")
    what_i_got_wrong: list[str] = Field(description="Honest self-review vs. previous memos and the track record; empty for initial coverage")
    focus_metrics: FocusMetrics = Field(description="One tight, number-backed paragraph per focus metric")
    valuation_discussion: str = Field(
        description="1-3 paragraphs interpreting the computed valuation: which methods matter, what is priced in, entry discipline"
    )
    key_risks: list[str] = Field(description="4-7 risks, each 'Risk name: why it matters and what would signal it'")
    catalysts: list[str] = Field(description="What I'm watching: upcoming events/metrics with timing")
    bottom_line: str = Field(description="Closing paragraph in the first person: where this leaves me and what I'd do")
    macro_scenarios: list[MacroScenario] = Field(description="Tickers without a company model (index/private): 2-4 regimes. Otherwise an empty list")
    relative_value: str = Field(description="Where this name sits in each comps bucket: rich or cheap vs peers on raw and "
                                            "growth-adjusted multiples (use the computed relative_value ranking when there is a "
                                            "model), and which peer is the natural pair against it")
    private_valuation: PrivateCompanyValuation | None = Field(
        description="PRIVATE companies only: value via public comps, private marks and a DCF sanity check. Null for public "
                    "companies (their model does this) and for indices")
    data_gaps: list[str]
    company_background: CompanyBackground
    trading_thesis: TradingThesisOut = Field(
        description="The active trading view coming out of this deep dive: position, signal, entry zone, target, stop "
        "(anchored to the computed valuation), risk, catalysts and cross-ticker dependencies"
    )


FOCUS_LABELS = {
    "revenue_growth": "Revenue growth",
    "margins": "Margins",
    "user_adoption": "User adoption",
    "competitive_threats": "Competitive threats",
}


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def model_table_md(outputs: dict) -> str:
    rows = outputs["rows"]
    headers = ["$mm"] + [r["year"] for r in rows]
    lines = [
        ["Revenue"] + [fmt_money_mm(r["revenue"]) for r in rows],
        ["  growth"] + [fmt_pct(r["revenue_growth_pct"]) for r in rows],
        ["Gross margin"] + [fmt_pct(r["gross_margin_pct"]) for r in rows],
        ["Adj. EBITDA"] + [fmt_money_mm(r["adj_ebitda"]) for r in rows],
        ["  margin"] + [fmt_pct(r["adj_ebitda_margin_pct"]) for r in rows],
        ["Free cash flow"] + [fmt_money_mm(r["fcf"]) for r in rows],
        ["  margin"] + [fmt_pct(r["fcf_margin_pct"]) for r in rows],
    ]
    seg = [[s["name"]] + [fmt_money_mm(v) for v in s["revenue_mm"]] for s in outputs["segments"]]
    out = _table(headers, lines)
    if len(seg) > 1:
        out += "\n\n**Revenue build**\n\n" + _table(headers, seg)
    return out


def valuation_tables_md(outputs: dict) -> str:
    cap = outputs["capitalization"]
    lines = [
        f"Price {fmt_price(cap['share_price'])} x {cap['diluted_shares_mm']:,.1f}mm diluted shares = "
        f"{fmt_money_mm(cap['market_cap'])} market cap; net debt {fmt_money_mm(cap['net_debt'])}; "
        f"EV {fmt_money_mm(cap['enterprise_value'])}"
        + (f" ({cap['ev_to_revenue_current_year']:.1f}x {cap['current_year']} revenue" if cap["ev_to_revenue_current_year"] else "")
        + (f", {cap['ev_to_ebitda_current_year']:.1f}x adj. EBITDA)" if cap["ev_to_ebitda_current_year"] else (")" if cap["ev_to_revenue_current_year"] else "")),
        "",
        _table(
            ["Methodology", "EV", "Equity value", "Per share", "vs. current", "Basis"],
            [[m["method"], fmt_money_mm(m["enterprise_value"]), fmt_money_mm(m["equity_value"]), fmt_price(m["implied_price"]),
              fmt_pct(m["upside_pct"], 0) if m["upside_pct"] is not None else "n/a", m["basis"]]
             for m in outputs["football_field"]],
        ),
        "",
        _table(
            ["Scenario", "Probability", "Revenue path", "Adj. EBITDA margin path", "Per share"],
            [[s["name"].title(), f"{s['probability_pct']:.0f}%",
              " / ".join(fmt_money_mm(v) for v in s["revenue_path"]),
              " / ".join(fmt_pct(v, 0) for v in s["adj_ebitda_margin_pct"]),
              fmt_price(s["implied_price"])] for s in outputs["scenarios"]],
        ),
    ]
    return "\n".join(lines)


def relative_value_md(outputs: dict) -> str:
    """Rich-to-cheap ranking per comps bucket (the subject in bold)."""
    blocks = []
    for b in outputs.get("relative_value") or []:
        fit, name = b.get("fit"), b.get("multiple_name", "EV/Revenue")
        head = f"**{b['bucket']}**{' (primary)' if b.get('primary') else ''} on **{name}**"
        if b.get("multiple_reason"):
            head += f" ({b['multiple_reason']})"
        if b.get("median_multiple"):
            head += f": median {b['median_multiple']:.1f}x"
        if fit:
            head += f"; fair = {fit['intercept']:.1f}x + {fit['slope']:.2f}x per point of LTM revenue growth (n={fit['n']})"
        pct0 = lambda v: fmt_pct(v, 0) if v is not None else "n/a"  # noqa: E731
        rows = [[("**" + (r["symbol"] or "") + "**") if r["is_subject"] else (r["symbol"] or ""),
                 f"{r['multiple']:.1f}x" if r.get("multiple") else "n/m",
                 f"{r['fair_multiple']:.1f}x" if r.get("fair_multiple") else "n/a",
                 pct0(r.get("gap_pct")), r["verdict"] or ("check basis" if r.get("flag") else "n/a"),
                 pct0(r.get("revenue_growth_pct")), pct0(r.get("ebitda_growth_pct")), pct0(r.get("ebitda_margin_pct"))]
                for r in b["rows"]]
        blocks += [head, _table([f"Name", name, "Fair", "Premium / discount", "Verdict",
                                 "Rev growth (LTM)", "EBITDA growth (LTM)", "EBITDA margin (LTM)"], rows)]
    pm = outputs.get("private_market") or {}
    if pm.get("marks"):
        blocks += ["**Private-market marks**", _table(
            ["Mark", "Type", "Date", "Valuation", "Implied EV/Rev", "Source"],
            [[k["name"], k["kind"].replace("_", " "), k["date"], fmt_money_mm(k["valuation_mm"]),
              f"{k['implied_ev_to_revenue']:.1f}x" if k.get("implied_ev_to_revenue") else "n/a", k["source"]] for k in pm["marks"]])]
    return "\n\n".join(blocks)


def assemble_memo(w: ResearchWriteup, agent_name: str, agent_type: str, run_date: str, outputs: dict | None) -> str:
    parts = [
        f"# {w.title}",
        f"*{w.subtitle}*",
        f"{agent_name} (AI) · {run_date} · Stance: **{w.stance.title()}** · Conviction: **{w.conviction_level}/10**",
        "## Executive summary",
        w.executive_summary,
        f"**Key number - {w.key_metric.label}:** {w.key_metric.value}. {w.key_metric.why_it_matters}",
        "## The setup",
        *w.setup,
        "## What the latest print showed" if agent_type == "company" else "## What the data is saying",
        "\n".join(f"- {b}" for b in w.evidence),
        "## What changed since my last note",
        w.what_changed,
    ]
    if w.what_i_got_wrong:
        parts += ["### What I got wrong (or got lucky on)", "\n".join(f"- {b}" for b in w.what_i_got_wrong)]
    parts.append("## Growth, margins, adoption, competition")
    parts += [f"**{label}.** {getattr(w.focus_metrics, key)}" for key, label in FOCUS_LABELS.items()]
    if outputs:
        parts += ["## The model", model_table_md(outputs), "## Valuation", valuation_tables_md(outputs), w.valuation_discussion]
        parts += ["## Relative value vs peers", relative_value_md(outputs), w.relative_value]
    elif w.private_valuation:
        pv = w.private_valuation
        parts += ["## Valuation: public comps, private marks, DCF",
                  f"Last private mark: **{fmt_money_mm(pv.last_mark_mm)}** ({pv.last_mark_date}; {pv.last_mark_source}). "
                  f"Estimated revenue {fmt_money_mm(pv.estimated_revenue_mm)} ({pv.revenue_basis}). Verdict on the mark: **{pv.verdict}**.",
                  _table(["Method", "Multiple", "Applied to", "Implied EV", "Basis"],
                         [[m.method.replace("_", " "), f"{m.multiple:.1f}x" if m.multiple else "n/a", m.metric,
                           fmt_money_mm(m.implied_value_mm), m.basis] for m in pv.methods]),
                  f"**Read-through for the public names:** {pv.read_through}",
                  w.valuation_discussion, "## Relative value vs peers", w.relative_value]
    else:
        parts.append("## Scenarios and positioning")
        if w.macro_scenarios:
            parts.append(_table(["Scenario", "Probability", "What it looks like", "Implications"],
                                [[s.name, f"{s.probability_pct:.0f}%", s.description, s.implications] for s in w.macro_scenarios]))
        parts.append(w.valuation_discussion)
        if w.relative_value:
            parts += ["## Relative value vs peers", w.relative_value]
    parts += [
        "## Key risks",
        "\n".join(f"{i}. {r}" for i, r in enumerate(w.key_risks, 1)),
        "## What I'm watching",
        "\n".join(f"- {c}" for c in w.catalysts),
        f"## Conviction: {w.conviction_level}/10",
        w.reasoning,
        "## Bottom line",
        w.bottom_line,
    ]
    if w.data_gaps:
        parts += ["---", "*Data gaps: " + "; ".join(w.data_gaps) + "*"]
    return "\n\n".join(p for p in parts if p)


def build_research_record(w: ResearchWriteup, agent_name: str, agent_type: str, run_date: str, outputs: dict | None) -> dict:
    """The stored research document: the requested top-level format plus a `details` block."""
    conviction = max(1, min(10, w.conviction_level))
    w = w.model_copy(update={"conviction_level": conviction})
    return {
        "long_form_memo": assemble_memo(w, agent_name, agent_type, run_date, outputs),
        "executive_summary": w.executive_summary,
        "financial_model": summary_for_research(outputs) if outputs else None,
        "key_risks": w.key_risks,
        "conviction_level": conviction,
        "reasoning": w.reasoning,
        "details": {
            "title": w.title,
            "subtitle": w.subtitle,
            "stance": w.stance,
            "key_metric": w.key_metric.model_dump(),
            "evidence": w.evidence,
            "what_changed": w.what_changed,
            "what_i_got_wrong": w.what_i_got_wrong,
            "focus_metrics": w.focus_metrics.model_dump(),
            "catalysts": w.catalysts,
            "data_gaps": w.data_gaps,
            "macro_scenarios": [s.model_dump() for s in w.macro_scenarios],
            "relative_value": w.relative_value,
            "private_valuation": w.private_valuation.model_dump() if w.private_valuation else None,
            "company_background": w.company_background.model_dump(),
            "valuation": {
                "football_field": outputs["football_field"],
                "probability_weighted_price": outputs["probability_weighted_price"],
                "valuation_range": outputs["valuation_range"],
                "share_price": outputs["capitalization"]["share_price"],
                "relative_value": outputs.get("relative_value"),
                "private_market": outputs.get("private_market"),
            } if outputs else None,
        },
    }
