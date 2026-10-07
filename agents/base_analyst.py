"""Deep dive: the expensive, thorough research run for one ticker (Claude Opus 5.5).

Runs only on triggers: initial coverage, a new SEC filing / earnings release, a light thesis update
flagging that fundamentals changed, or an explicit request.

LangGraph (data fetchers fan out in parallel, then join):

    START -> fetch_sec ----\
          -> fetch_market --\
          -> fetch_news -----> analyze (Claude) -> persist -> END
          -> fetch_macro ---/
          -> load_memory --/

`analyze` is one Claude conversation with two tools:
  1. submit_financial_model - Claude submits drivers/assumptions; Python computes the P&L, DCF, comps,
     SOTP and scenario valuation and returns them as the tool result (public companies only).
  2. submit_research - Claude writes the memo, company background and trading thesis against those numbers.
The tools and system prompt stay fixed across turns, so turn 2 reads the large data prompt from cache.
"""

import json
import logging
import operator
from datetime import date, datetime, timezone
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import select

from agents import llm
from agents.coverage_files import read_file, refresh_files
from agents.financial_model import ModelInputError, ModelInputs, compute_model, describe_for_claude
from agents.llm import AnalystError
from agents.philosophy import framework_block
from agents.registry import FOCUS_METRICS, TickerContext, get_context
from agents.research_format import ResearchWriteup, build_research_record
from agents.thesis import analyst_lessons, current_thesis, pod_snapshot, save_thesis
from data_sources.market_data import get_macro_data, get_market_data, get_peer_multiples
from data_sources.news_fetcher import get_news
from data_sources.sec_fetcher import get_sec_data
from database.db import session_scope
from database.models import FinancialModel, ResearchOutput, Ticker
from utils import config

logger = logging.getLogger(__name__)

MAX_TURNS = 6
MODEL_TOOL = "submit_financial_model"
RESEARCH_TOOL = "submit_research"


# --- Tools ---------------------------------------------------------------------------

def strict_schema(model) -> dict:
    """Pydantic JSON schema -> strict tool schema: $refs inlined, every object closed and fully required."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, list):
            return [resolve(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = resolve(defs[node["$ref"].split("/")[-1]])
            return {**target, **({"description": node["description"]} if "description" in node else {})}
        out = {}
        for key, value in node.items():
            if key in ("title", "default"):
                continue
            out[key] = {k: resolve(v) for k, v in value.items()} if key == "properties" else resolve(value)
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out

    return resolve(schema)


def _tool(name: str, description: str, model) -> dict:
    # Not `strict`: these schemas are too large for constrained decoding ("compiled grammar is too large").
    # Every input is validated with Pydantic in run_conversation and errors go back to Claude to fix.
    return {
        "name": name,
        "description": description,
        "input_schema": strict_schema(model),
        "eager_input_streaming": True,  # large inputs stream as generated; we validate them ourselves
    }


MODEL_TOOL_DEF = _tool(
    MODEL_TOOL,
    "Submit the financial model inputs (segment revenue build, margins, capitalization, DCF assumptions, "
    "bull/bear scenarios, primary comps bucket, next-print estimate). The engine validates them and returns the "
    "computed P&L, DCF, comps, sum-of-the-parts, scenario prices and the research-JSON financial_model summary. "
    "If it returns an error, fix the inputs and call it again.",
    ModelInputs,
)
RESEARCH_TOOL_DEF = _tool(
    RESEARCH_TOOL,
    "Submit the finished research: executive summary, long-form memo sections, company background, key risks, "
    "conviction, and the trading thesis (position, signal, levels). Call this once, last.",
    ResearchWriteup,
)


# --- Prompts ---------------------------------------------------------------------

STYLE_GUIDE = """Writing style (match the PM's own published research):
- First person ("I"), plain declarative sentences, no hype or filler. Quantify: one well-chosen number beats three adjectives.
- Lead with the setup the market is missing. Read past the headline to the driver underneath (e.g. a GAAP loss driven by mark-to-market while the recurring line grew 97%).
- Separate thesis from tape: how much of a move is the idiosyncratic story vs. sector beta.
- Be explicit about what is already priced in and about entry discipline. It is fine to like the business and not the price, and to say so.
- Be candid about what you got wrong or got lucky on.
- Bucket comps by valuation logic, not industry label; say which bucket the market is using to price the stock today and which one the thesis says it should migrate toward."""

MODEL_GUIDE = """Financial model conventions (match the PM's BLSH / PUBM workbooks):
- Calendar-year columns: the 2 most recent actual years, then 4 forecast years (e.g. 2024A, 2025A, 2026E ... 2029E). Calendarize non-December fiscal years. The current year is an estimate even if two quarters are reported.
- Build revenue bottom-up by segment from operating drivers: volume x spread/take rate, impressions x revenue per million, bookings x take rate, customers x revenue per customer. Put the drivers in each segment's driver rows. Actual years must tie to the filings / XBRL table.
- Margins, SBC, D&A and capex are % of revenue per year. Prefer treating SBC as a real cash cost in the DCF.
- Bull and bear move the key driver, not everything at once, and each states its basis (e.g. TAM growth, management targets, a taper). Probabilities sum to 100.
- Pick a WACC that fits the risk (small-cap ad tech / crypto-beta names typically 10-15%). If terminal value exceeds ~75% of EV, the answer is mostly the terminal assumption: say so.
- Comps multiples supplied are LTM from the market-data feed (with revenue growth); peers with missing multiples are n/a. Choose the primary bucket the market prices the stock on today. Give segment SOTP multiples from the relevant bucket medians where it makes sense.
- Value it BOTH ways, on BOTH lenses: intrinsic (DCF) and relative (comps), in public markets and private markets. In private_market give the funding rounds / secondaries of private peers and precedent acquisitions or take-privates (cite the headline or filing; label anything else 'general knowledge, unverified') and the EV/revenue a private or strategic buyer would pay. The engine adds a private-market row to the football field.
- The engine ranks every name in each comps bucket rich-to-cheap on EV/revenue vs a growth-adjusted fair multiple (regressed across the bucket). This is central to how the PM invests: say who is over- vs under-priced against whom, and whether the subject is the cheap or rich leg of a pair.
- Next print: estimate the next reported quarter against company guidance and explain the gap."""

THESIS_GUIDE = """Trading thesis (the active position view the PM manages day to day):
- A trade, not a rating: the narrative, what the market is missing (market_missing) and the trade structure that isolates it (trade_structure: pair or hedge leg to strip out beta, or no_trade when there is no edge).
- Position long/short/flat with a position-management action and a one-line headline ("Market is missing X; long vs. Y", "Edge gone, step aside").
- Entry zone, target and stop anchored to the computed valuation (DCF, comps, scenario prices) and to where the stock trades now. Levels must be coherent with the position.
- Cross-ticker dependencies: name specific linkages to other names in the pod (see the pod snapshot) or outside it."""


def build_system_prompt(ctx: TickerContext) -> str:
    role = {
        "company": "the dedicated equity research analyst",
        "macro": "the team's macro strategist",
        "sector": "the team's sector analyst",
    }[ctx.agent_type]
    competitors = ", ".join(ctx.competitors) or "n/a"
    siblings = f"You also cover: {', '.join(ctx.sibling_symbols)}." if ctx.sibling_symbols else ""
    if ctx.has_model:
        workflow = (f"Workflow: first call {MODEL_TOOL} with your model inputs. The engine returns the computed model and "
                    f"valuation; fix and resubmit if it reports errors. Then call {RESEARCH_TOOL}, using the computed figures "
                    "exactly (do not recompute them yourself).")
    else:
        workflow = (f"Workflow: call {RESEARCH_TOOL} with your research. There is no company model for this "
                    f"{'private company' if ctx.ticker_type == 'private' else 'index/instrument'}; use macro_scenarios for 2-4 "
                    "regimes and say what each means for the pod's coverage.")
        if ctx.ticker_type == "private":
            workflow += (" Fill private_valuation: value it three ways (public comps from the supplied buckets applied to your "
                         "best revenue estimate, private marks from rounds/secondaries/deals, and a DCF sanity check), judge whether "
                         "the last mark is over- or under-priced, and give the read-through for the public names in the bucket.")
    return f"""You are {ctx.analyst_name}, {role} in a small research pod. This is a full deep dive on {ctx.symbol}: {ctx.description}
Your analyst focus: {ctx.analyst_focus}
{siblings}

You report to a portfolio manager. You get fresh data (SEC filings, XBRL financials, market data, comps, news), your previous research and trading thesis, notes from the pod's meetings, and lessons you have taken from them. You produce a financial model, a research memo (a 2-3 page long-form memo plus a 1-2 paragraph executive summary: the 30-second thesis, why it matters now, the key number), the company background, and the trading thesis.

What the portfolio manager cares about most: {", ".join(FOCUS_METRICS)}. Address each one explicitly.

{framework_block()}
Ticker focus: {ctx.focus_notes or 'n/a'}
Competitors / peers: {competitors}

Evidence rules:
- Ground claims in the supplied data and cite figures with their period and source. If the data does not cover something, say so and list it in data_gaps rather than filling in numbers from memory. General industry knowledge is fine for context; label it as such.
- News sentiment labels come from a simple word list; read the headlines yourself.
- SEC sections may be truncated excerpts; the XBRL tables are the most reliable numbers you have.
- Conviction (1-10) reflects the strength of evidence, not how bullish you are. A neutral stance is fine when evidence is balanced; say what would change your mind.

{STYLE_GUIDE}

{MODEL_GUIDE if ctx.has_model else ""}

{THESIS_GUIDE}

Memo length: setup is 3-4 full paragraphs; the narrative sections together run roughly 1,500-2,000 words (2-3 pages with the tables the system inserts). The executive summary is 1-2 paragraphs, no bullets.

{workflow}"""


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str, sort_keys=True)


def _format_sec(sec: dict) -> str:
    if not sec.get("available"):
        return f"SEC data unavailable: {sec.get('error', 'not applicable')}"
    parts = [
        f"Company: {sec['company_name']} (CIK {sec['cik']})",
        "Recent filings: " + _dump(sec.get("recent_filings", [])),
        "XBRL financials (USD; periods are calendar-year frames; Q4 derived as FY less Q1-Q3):\n" + _dump(sec.get("financials", {})),
    ]
    for key, label in (("annual_report", "Annual report / prospectus"), ("quarterly_report", "Latest quarterly report")):
        doc = sec.get(key)
        if not doc:
            continue
        parts.append(f"=== {label}: {doc['form']} filed {doc['filing_date']} ({doc['url']}) ===")
        for name, section in doc["sections"].items():
            note = f" [excerpt: first {len(section['text'])} of {section['total_chars']} chars]" if section["truncated"] else ""
            parts.append(f"--- {name}{note} ---\n{section['text']}")
    release = sec.get("earnings_release")
    if release:
        parts.append(f"=== Latest earnings release: {release['form']} filed {release['filing_date']} ===\n{release['text']}")
    if sec.get("errors"):
        parts.append("SEC fetch errors: " + "; ".join(sec["errors"]))
    return "\n\n".join(parts)


def _format_news(news: dict) -> str:
    if not news.get("available"):
        return f"News unavailable: {news.get('error')}"
    lines = [f"Sentiment summary: {_dump(news['sentiment_summary'])}"]
    for a in news["articles"]:
        lines.append(f"- [{(a['published_at'] or '')[:10]}] {a['title']} ({a['source']}; {a['sentiment']})\n  {a['description'] or ''}")
    return "\n".join(lines)


def build_user_prompt(ctx: TickerContext, data: dict, memory: dict, track_record: list[dict], trigger: str) -> str:
    sections = [f"Today's date: {date.today().isoformat()}. Deep dive on {ctx.symbol}. Trigger: {trigger}."]
    for key, tag in (("market", "market_data"), ("comps", "comps_by_bucket multiples=\"LTM, from market-data feed\""),
                     ("macro", "macro_indicators")):
        if key in data:
            sections.append(f"<{tag}>\n{_dump(data[key])}\n</{tag.split()[0]}>")
    if "sec" in data:
        sections.append(f"<sec_filings>\n{_format_sec(data['sec'])}\n</sec_filings>")
    if "news" in data:
        sections.append(f"<news_last_30_days>\n{_format_news(data['news'])}\n</news_last_30_days>")
    sections.append(f"<pod_snapshot description=\"the pod's current calls on every covered ticker\">\n{_dump(memory['pod'])}\n</pod_snapshot>")
    if memory["previous"]:
        sections.append(f"<your_previous_research newest_first=\"true\">\n{_dump(memory['previous'])}\n</your_previous_research>")
    else:
        sections.append("<your_previous_research>None - this is initial coverage.</your_previous_research>")
    if memory["thesis"]:
        sections.append(f"<your_current_trading_thesis>\n{_dump(memory['thesis'])}\n</your_current_trading_thesis>")
    if memory["meeting_notes"]:
        sections.append(f"<recent_meeting_notes>\n{memory['meeting_notes']}\n</recent_meeting_notes>")
    if memory["lessons"]:
        sections.append(f"<your_lessons_from_meetings>\n{_dump(memory['lessons'])}\n</your_lessons_from_meetings>")
    if track_record:
        sections.append(f"<your_track_record description=\"your prior next-quarter revenue estimates vs. reported\">\n{_dump(track_record)}\n</your_track_record>")
    return "\n\n".join(sections)


# --- Memory & track record ---------------------------------------------------------

def compact_previous(memo: dict, full: bool) -> dict:
    details = memo.get("details") or {}
    base = {"stance": details.get("stance"), "conviction_level": memo.get("conviction_level"),
            "financial_model": memo.get("financial_model")}
    if full:
        base.update({
            "executive_summary": memo.get("executive_summary"),
            "reasoning": memo.get("reasoning"),
            "key_risks": memo.get("key_risks"),
            "catalysts": details.get("catalysts"),
            "evidence": details.get("evidence"),
            "valuation": details.get("valuation"),
        })
    return base


def build_track_record(ticker_id: int, sec: dict | None) -> list[dict]:
    """Prior next-print revenue estimates matched against XBRL actuals (the Historical Tracker tab)."""
    quarterly = {r["period"]: r["revenue"] for r in ((sec or {}).get("financials") or {}).get("quarterly", [])}
    with session_scope() as s:
        models = s.scalars(select(FinancialModel).where(FinancialModel.ticker_id == ticker_id).order_by(FinancialModel.date)).all()
        latest = {}
        for fm in models:
            next_print = ((fm.assumptions or {}).get("inputs") or {}).get("next_print")
            if not next_print:
                continue
            actual_usd = quarterly.get(next_print["period"])
            estimate = next_print["revenue_estimate_mm"]
            actual = actual_usd / 1e6 if actual_usd is not None else None
            latest[next_print["period"]] = {
                "estimated_on": fm.date.date().isoformat(),
                "period": next_print["period"],
                "metric": "Revenue ($mm)",
                "estimate": round(estimate, 1),
                "actual": round(actual, 1) if actual is not None else None,
                "variance_pct": round((actual / estimate - 1) * 100, 1) if actual is not None and estimate else None,
                "company_guidance": next_print.get("company_guidance"),
            }
    return list(latest.values())


# --- LangGraph state & nodes ----------------------------------------------------------

def _merge(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class AnalystState(TypedDict, total=False):
    symbol: str
    trigger: str
    data: Annotated[dict, _merge]
    errors: Annotated[list[str], operator.add]
    memory: dict
    record: dict
    trading_thesis: dict
    model_inputs: dict
    model_outputs: dict
    meta: dict
    research_id: int


def _ctx(state: AnalystState) -> TickerContext:
    return get_context(state["symbol"])


def fetch_sec(state: AnalystState) -> dict:
    ctx = _ctx(state)
    if ctx.ticker_type != "public" or not ctx.sec_ticker:
        return {}
    sec = get_sec_data(ctx.sec_ticker)
    errors = [f"sec: {sec['error']}"] if not sec.get("available") else [f"sec: {e}" for e in sec.get("errors", [])]
    return {"data": {"sec": sec}, "errors": errors}


def fetch_market(state: AnalystState) -> dict:
    ctx = _ctx(state)
    data, errors = {}, []
    if ctx.price_symbol:
        data["market"] = get_market_data(ctx.price_symbol, include_overview=ctx.ticker_type == "public")
        errors += [f"market: {e}" for e in data["market"]["errors"]]
    if ctx.comps_buckets:
        data["comps"] = get_peer_multiples(ctx.comps_buckets)
        missing = [r["symbol"] for rows in data["comps"].values() for r in rows if r["as_of"] is None]
        if missing:
            errors.append(f"comps: no multiples yet for {', '.join(missing)} (Alpha Vantage daily budget; fills in over days)")
    return {"data": data, "errors": errors}


def fetch_news(state: AnalystState) -> dict:
    ctx = _ctx(state)
    news = get_news(ctx.symbol, ctx.news_query, ctx.news_keywords)
    return {"data": {"news": news}, "errors": [] if news.get("available") else [f"news: {news.get('error')}"]}


def fetch_macro(state: AnalystState) -> dict:
    ctx = _ctx(state)
    if not ctx.uses_macro_indicators:
        return {}
    macro = get_macro_data()
    return {"data": {"macro": macro}, "errors": [f"macro: {e}" for e in macro["errors"]]}


def load_memory(state: AnalystState) -> dict:
    ctx = _ctx(state)
    with session_scope() as s:
        rows = s.scalars(select(ResearchOutput).where(ResearchOutput.ticker_id == ctx.ticker_id)
                         .order_by(ResearchOutput.date.desc()).limit(5)).all()
        previous = [{**compact_previous(r.research_memo, full=(i == 0)), "date": r.date.date().isoformat()} for i, r in enumerate(rows)]
    thesis = current_thesis(ctx.ticker_id)
    notes = (read_file(ctx.symbol, "meeting_notes.md") or {}).get("content", "")
    return {"memory": {
        "previous": previous,
        "thesis": thesis["thesis"] if thesis else None,
        "meeting_notes": notes[:6000] if "_No meetings yet._" not in notes else "",
        "lessons": analyst_lessons(ctx.analyst_id),
        "pod": pod_snapshot(exclude_ticker_id=ctx.ticker_id),
    }}


def run_conversation(ctx: TickerContext, data: dict, memory: dict, track_record: list[dict], trigger: str) -> dict:
    """Drive the two-tool conversation. Returns writeup, model inputs/outputs and usage."""
    tools = [MODEL_TOOL_DEF, RESEARCH_TOOL_DEF] if ctx.has_model else [RESEARCH_TOOL_DEF]
    system = build_system_prompt(ctx)
    messages = [{"role": "user", "content": build_user_prompt(ctx, data, memory, track_record, trigger)}]
    comps = data.get("comps") or {}
    model_inputs = model_outputs = writeup = None
    usage, model_used, cost = {}, None, 0.0

    for turn in range(MAX_TURNS):
        with llm.get_client().beta.messages.stream(
            model=config.DEEP_MODEL,
            max_tokens=config.RESEARCH_MAX_TOKENS,
            betas=[llm.FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": config.RESEARCH_EFFORT},
            cache_control={"type": "ephemeral"},  # turn 2+ reads the data prompt from cache
            system=system,
            tools=tools,
            messages=messages,
        ) as stream:
            message = stream.get_final_message()
        cost += llm.record_cost("deep_dive", message, ctx.symbol)
        llm.check_stop(message)
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[key] = usage.get(key, 0) + (getattr(message.usage, key, 0) or 0)
        model_used = message.model
        logger.info("%s deep dive turn %d: stop=%s in=%s cache_read=%s out=%s ($%.3f so far)", ctx.symbol, turn + 1,
                    message.stop_reason, message.usage.input_tokens, message.usage.cache_read_input_tokens,
                    message.usage.output_tokens, cost)
        messages.append({"role": "assistant", "content": message.content})

        tool_uses = [b for b in message.content if b.type == "tool_use"]
        if not tool_uses:
            needed = MODEL_TOOL if ctx.has_model and model_outputs is None else RESEARCH_TOOL
            messages.append({"role": "user", "content": f"Please call {needed} now."})
            continue

        results = []
        for block in tool_uses:
            try:
                if block.name == MODEL_TOOL:
                    model_inputs = ModelInputs.model_validate(block.input)
                    model_outputs = compute_model(model_inputs, comps, symbol=ctx.symbol)
                    content = _dump(describe_for_claude(model_outputs))
                elif block.name == RESEARCH_TOOL:
                    if ctx.has_model and model_outputs is None:
                        raise ModelInputError(f"Call {MODEL_TOOL} first; the memo must use the computed model.")
                    writeup = ResearchWriteup.model_validate(block.input)
                    content = "Research received."
                else:
                    raise ModelInputError(f"Unknown tool {block.name}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
            except (ValidationError, ModelInputError) as exc:
                logger.info("%s: %s rejected: %s", ctx.symbol, block.name, exc)
                if block.name == MODEL_TOOL:
                    model_outputs = None
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": f"Input rejected, fix and call again: {exc}"})
        if writeup is not None:
            break
        messages.append({"role": "user", "content": results})

    if writeup is None:
        raise AnalystError(f"No research submitted after {MAX_TURNS} turns")
    return {"writeup": writeup, "model_inputs": model_inputs, "model_outputs": model_outputs,
            "usage": usage, "model": model_used, "cost_usd": cost}


def analyze(state: AnalystState) -> dict:
    ctx = _ctx(state)
    data = state.get("data", {})
    if not any((d.get("available") if isinstance(d, dict) and "available" in d else bool(d)) for d in data.values()):
        raise AnalystError(f"No data sources returned anything for {ctx.symbol}: {state.get('errors')}")
    track_record = build_track_record(ctx.ticker_id, data.get("sec"))
    result = run_conversation(ctx, data, state["memory"], track_record, state.get("trigger", "manual"))
    writeup = result["writeup"]
    record = build_research_record(writeup, ctx.analyst_name, "company" if ctx.has_model else "macro",
                                   date.today().isoformat(), result["model_outputs"])
    record["details"]["track_record"] = track_record
    return {
        "record": record,
        "trading_thesis": writeup.trading_thesis.model_dump(),
        "model_inputs": result["model_inputs"].model_dump() if result["model_inputs"] else None,
        "model_outputs": result["model_outputs"],
        "meta": {"model": result["model"], "usage": result["usage"], "cost_usd": result["cost_usd"]},
    }


def _snapshot(data: dict) -> dict:
    """Compact record of the inputs (no filing text) for auditing research later."""
    snap = {k: data[k] for k in ("market", "macro", "comps") if k in data}
    if "news" in data:
        snap["news"] = {
            "sentiment_summary": data["news"].get("sentiment_summary"),
            "articles": [{k: a[k] for k in ("title", "source", "url", "published_at", "sentiment")} for a in data["news"].get("articles", [])],
        }
    sec = data.get("sec")
    if sec:
        snap["sec"] = {
            "available": sec.get("available"),
            "financials": sec.get("financials"),
            "documents": {
                k: {"form": sec[k]["form"], "filing_date": sec[k]["filing_date"], "url": sec[k]["url"]}
                for k in ("annual_report", "quarterly_report", "earnings_release") if sec.get(k)
            },
        }
    return snap


def persist(state: AnalystState) -> dict:
    ctx = _ctx(state)
    record = {**state["record"], "date": date.today().isoformat()}
    record["details"]["data_errors"] = state.get("errors", [])
    meta = state["meta"]
    usage = meta["usage"]
    sec_fin = (state.get("data", {}).get("sec") or {}).get("financials") or {}
    with session_scope() as s:
        research = ResearchOutput(
            ticker_id=ctx.ticker_id,
            research_memo=record,
            conviction_level=record["conviction_level"],
            rating=record["details"]["stance"],
            model_used=meta["model"],
            input_tokens=usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            data_snapshot={**_snapshot(state.get("data", {})), "usage": usage, "cost_usd": meta["cost_usd"]},
        )
        s.add(research)
        s.flush()
        if state.get("model_outputs"):
            s.add(FinancialModel(
                ticker_id=ctx.ticker_id,
                research_id=research.id,
                revenue_projections=state["model_outputs"]["rows"],
                margin_analysis={"historical_annual": sec_fin.get("annual", []), "historical_quarterly": sec_fin.get("quarterly", [])},
                assumptions={"inputs": state["model_inputs"], "outputs": state["model_outputs"]},
            ))
        s.get(Ticker, ctx.ticker_id).last_deep_dive_at = datetime.now(timezone.utc)
        research_id = research.id
    save_thesis(ctx, state["trading_thesis"], "deep_dive", state.get("trigger", ""), meta["model"])
    refresh_files(ctx.symbol)
    return {"research_id": research_id}


def _build_graph():
    g = StateGraph(AnalystState)
    fetchers = {"fetch_sec": fetch_sec, "fetch_market": fetch_market, "fetch_news": fetch_news,
                "fetch_macro": fetch_macro, "load_memory": load_memory}
    for name, fn in fetchers.items():
        g.add_node(name, fn)
        g.add_edge(START, name)
    g.add_node("analyze", analyze)
    g.add_node("persist", persist)
    g.add_edge(list(fetchers), "analyze")  # wait for every fetcher before analyzing
    g.add_edge("analyze", "persist")
    g.add_edge("persist", END)
    return g.compile()


GRAPH = _build_graph()


def run_deep_dive(symbol: str, trigger: str = "manual") -> dict:
    """Run the full deep dive for one ticker. Returns a short summary. Raises on failure."""
    ctx = get_context(symbol)
    if ctx is None:
        raise AnalystError(f"Unknown ticker {symbol}")
    final = GRAPH.invoke({"symbol": ctx.symbol, "trigger": trigger, "data": {}, "errors": []})
    record = final["record"]
    return {
        "ticker": ctx.symbol,
        "research_id": final["research_id"],
        "stance": record["details"]["stance"],
        "conviction": record["conviction_level"],
        "signal": final["trading_thesis"]["signal"],
        "headline": final["trading_thesis"]["headline"],
        "financial_model": record["financial_model"],
        "cost_usd": round(final["meta"]["cost_usd"], 4),
        "data_errors": final.get("errors", []),
    }
