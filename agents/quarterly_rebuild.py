"""Quarterly model rebuild: a separate, model-only run for analysts with model_frequency: quarterly.

    python -m agents.quarterly_rebuild --analyst internet --max-usd 1.0
    POST /admin/quarterly-rebuild {"analyst": "internet", "max_usd_per_ticker": 1.0}

For each public ticker the analyst covers: fresh data (12 quarters of XBRL, market data, comps, the latest 10-Q
and earnings release), the current model as the baseline, and one Claude conversation with only the model tool -
no memo - so it costs a fraction of a deep dive. The quarterly model is saved as a new model version, then a
light thesis update re-anchors the trading levels on it.

When the PM has uploaded their own workbook (agents.model_workbook), it stays the model: a quarterly PM workbook
is updated in place (the agent's inputs and cell edits are written into it), and a ticker whose PM workbook is
annual is skipped - restructuring the PM's model is the PM's call.

Spend is tagged "separate_*": outside the daily budget and the build-out pool. Each ticker has a hard cap
(default $1): output tokens are bounded so one turn can't cross it, and a retry turn only runs if what is left
covers it. Tickers run one at a time.
"""

import argparse
import json
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select

from agents import llm
from agents import model_workbook as mw
from agents.base_analyst import (MODEL_GUIDE, MODEL_TOOL, QUARTERLY_MODEL_GUIDE, QUARTERLY_MODEL_TOOL_DEF, _dump,
                                 check_workbook_structure, fetch_market, fetch_sec, load_memory, open_workbook_session,
                                 workbook_block)
from agents.coverage_files import refresh_files
from agents.financial_model import ModelInputError, QuarterlyModelInputs, compute_quarterly_model, describe_for_claude, is_quarterly
from agents.registry import TickerContext, active_contexts, get_context
from database.db import session_scope
from database.models import CostEntry, FinancialModel, ResearchOutput
from utils import config

logger = logging.getLogger(__name__)

KIND = "quarterly_model"
MAX_TURNS = 3
EFFORT = "medium"  # the model inputs are structured numbers, not prose; high effort mostly buys thinking tokens
SECTION_CHARS = 20000  # per filing section: MD&A and segment notes fit; the rest is truncated
THESIS_RESERVE_USD = 0.10  # kept back from the cap for the thesis re-anchor
WORKBOOK_CHARS = 30000  # the PM's workbook digest, trimmed to keep the run under its cap


def _max_tokens(budget_usd: float) -> int:
    """Output tokens (thinking included) that fit in what is left after the uncached prompt."""
    _, rate_out, _ = llm.PRICING.get(config.DEEP_MODEL, llm.DEFAULT_PRICE)
    return max(4000, min(32000, int(budget_usd / rate_out * 1_000_000)))


def _sec_block(sec: dict) -> str:
    """Filing data the model needs: XBRL tables, the latest 10-Q (else the annual report) and the earnings release."""
    if not sec.get("available"):
        return f"SEC data unavailable: {sec.get('error', 'not applicable')}"
    parts = [f"Company: {sec['company_name']} (CIK {sec['cik']})",
             "XBRL financials (USD; calendar-year frames; Q4 derived as FY less Q1-Q3):\n" + _dump(sec.get("financials", {}))]
    doc = sec.get("quarterly_report") or sec.get("annual_report")
    if doc:
        parts.append(f"=== {doc['form']} filed {doc['filing_date']} ===")
        for name, section in doc["sections"].items():
            text = section["text"][:SECTION_CHARS]
            parts.append(f"--- {name}{' [excerpt]' if len(text) < section['total_chars'] else ''} ---\n{text}")
    release = sec.get("earnings_release")
    if release:
        parts.append(f"=== Latest earnings release: {release['form']} filed {release['filing_date']} ===\n{release['text'][:SECTION_CHARS]}")
    return "\n\n".join(parts)


def _system(ctx: TickerContext) -> str:
    return f"""You are {ctx.analyst_name}, the equity research analyst covering {ctx.symbol}: {ctx.description}
Your analyst focus: {ctx.analyst_focus}
Ticker focus: {ctx.focus_notes or 'n/a'}

This is a model-only run: rebuild your financial model on a quarterly basis. No memo. Call {MODEL_TOOL} once with the full quarterly model; if the engine reports errors, fix them and call it again. When it returns the computed model, reply with one line and stop.

Evidence rules: actual quarters tie to the quarterly XBRL table and filings. Carry over your existing assumptions (WACC, scenarios, comps view, private marks, capitalization) unless the new data contradicts them; keep key_assumptions and model_notes short and say in model_notes that this is the quarterly rebuild. Where the data does not cover something, say so in model_notes rather than inventing it.

{MODEL_GUIDE}

{QUARTERLY_MODEL_GUIDE}"""


def _user(ctx: TickerContext, data: dict, memory: dict) -> str:
    sections = [f"Today's date: {datetime.now(timezone.utc).date().isoformat()}. Quarterly model rebuild for {ctx.symbol}."]
    if "market" in data:
        sections.append(f"<market_data>\n{_dump(data['market'])}\n</market_data>")
    if "comps" in data:
        sections.append(f"<comps_by_bucket>\n{_dump(data['comps'])}\n</comps_by_bucket>")
    if "sec" in data:
        sections.append(f"<sec_filings>\n{_sec_block(data['sec'])}\n</sec_filings>")
    model = memory.get("model")
    if model and model.get("workbook"):
        sections.append(workbook_block(model, WORKBOOK_CHARS) + f"\nCall {mw.EDIT_TOOL} (if needed) in the same turn as {MODEL_TOOL}.")
    elif model:
        whose = "the PM's edited version: keep every PM assumption unless new data contradicts it" if model["source"] == "pm_upload" else "your last model"
        sections.append(f"<current_model date=\"{model['date']}\" description=\"{whose}; rebuild it by quarter\">\n{_dump(model['inputs'])}\n</current_model>")
    if memory.get("thesis"):
        t = memory["thesis"]
        sections.append(f"<your_current_trading_thesis>\n{_dump({k: t.get(k) for k in ('position', 'headline', 'narrative', 'market_missing')})}\n</your_current_trading_thesis>")
    if memory.get("previous"):
        sections.append(f"<your_latest_research_summary>\n{_dump(memory['previous'][0])}\n</your_latest_research_summary>")
    return "\n\n".join(sections)


def rebuild_one(symbol: str, max_usd: float = 1.0) -> dict:
    """Rebuild one ticker's model by quarter under a hard cost cap. Returns a summary (never raises)."""
    ctx = get_context(symbol)
    if ctx is None or not ctx.has_model or not ctx.quarterly_model:
        return {"ticker": symbol, "status": "skipped", "error": "not a public ticker of a quarterly-model analyst"}
    cost = 0.0
    try:
        with llm.separate_scope(ctx.symbol):
            state = {"symbol": ctx.symbol}
            data, errors = {}, []
            for node in (fetch_sec, fetch_market):
                out = node(state)
                data.update(out.get("data", {}))
                errors += out.get("errors", [])
            memory = load_memory(state)["memory"]
            base_model = memory.get("model")
            if base_model and base_model.get("workbook") and not is_quarterly(base_model["inputs"]):
                return {"ticker": ctx.symbol, "status": "skipped", "cost_usd": 0.0,
                        "error": "your uploaded workbook is annual and stays the model; add quarters to it and upload, or the next deep dive updates it as is"}
            wb_session = open_workbook_session(ctx, base_model)
            tools = [QUARTERLY_MODEL_TOOL_DEF] + ([mw.EDIT_TOOL_DEF] if wb_session is not None else [])
            comps = data.get("comps") or {}
            model_cap = max_usd - THESIS_RESERVE_USD
            messages = [{"role": "user", "content": _user(ctx, data, memory)}]
            system = _system(ctx)
            inputs = outputs = None
            for turn in range(MAX_TURNS):
                left = model_cap - cost
                rate_in = llm.PRICING.get(config.DEEP_MODEL, llm.DEFAULT_PRICE)[0]
                prompt_usd = (len(system) + sum(len(json.dumps(m["content"], default=str)) for m in messages)) / 3.5 / 1e6 * rate_in
                prompt_usd *= 1.25 if turn == 0 else 0.1  # first turn writes the cache, later turns read it
                if left - prompt_usd < 0.08:
                    raise llm.AnalystError(f"stopped at the ${max_usd:.2f} cap before turn {turn + 1} (spent ${cost:.2f})")
                with llm.get_client().beta.messages.stream(
                    model=config.DEEP_MODEL,
                    max_tokens=_max_tokens(left - prompt_usd),
                    betas=[llm.FALLBACK_BETA],
                    fallbacks="default",
                    output_config={"effort": EFFORT},
                    cache_control={"type": "ephemeral"},
                    system=system,
                    tools=tools,
                    messages=messages,
                ) as stream:
                    message = stream.get_final_message()
                cost += llm.record_cost(KIND, message, ctx.symbol)
                llm.check_stop(message)
                logger.info("%s quarterly rebuild turn %d: in=%s cache_read=%s out=%s ($%.3f so far)", ctx.symbol, turn + 1,
                            message.usage.input_tokens, message.usage.cache_read_input_tokens, message.usage.output_tokens, cost)
                messages.append({"role": "assistant", "content": message.content})
                tool_uses = [b for b in message.content if b.type == "tool_use"]
                if outputs is not None and not tool_uses:
                    break
                if not tool_uses:
                    messages.append({"role": "user", "content": f"Please call {MODEL_TOOL} now."})
                    continue
                results = []
                for block in sorted(tool_uses, key=lambda b: b.name == MODEL_TOOL):  # stage workbook edits first
                    try:
                        if block.name == mw.EDIT_TOOL and wb_session is not None:
                            results.append({"type": "tool_result", "tool_use_id": block.id,
                                            "content": wb_session.stage(block.input.get("edits") or [])})
                            continue
                        inputs = QuarterlyModelInputs.model_validate(block.input)
                        check_workbook_structure(wb_session, base_model, inputs.model_dump())
                        outputs = compute_quarterly_model(inputs, comps, symbol=ctx.symbol)
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": _dump(describe_for_claude(outputs)) + "\nSaved. Reply with one line; no further calls."})
                    except (ValidationError, ModelInputError, mw.WorkbookError) as exc:
                        if block.name == MODEL_TOOL:
                            outputs = None
                        results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                        "content": f"Input rejected, fix and call again: {exc}"})
                if outputs is not None:
                    break  # accepted: no need to pay for the closing line
                messages.append({"role": "user", "content": results})
            if outputs is None:
                raise llm.AnalystError(f"no valid quarterly model after {MAX_TURNS} turns")
            model_id = _save(ctx, base_model, wb_session, inputs, outputs, comps, data.get("sec") or {}, cost)
            if model_id is None:
                return {"ticker": ctx.symbol, "status": "skipped", "cost_usd": round(cost, 4),
                        "error": "you uploaded a newer version during the run; yours stays the latest"}
            thesis = None
            if cost + config.THESIS_UPDATE_ESTIMATE_USD <= max_usd:
                from agents.thesis import update_thesis
                summary = _valuation_line(outputs)
                spent = cost
                try:
                    t = update_thesis(ctx.symbol, "quarterly_model", f"Model rebuilt by quarter: {summary}")
                    thesis = {"signal": t["signal"], "headline": t["headline"]}
                except Exception as exc:
                    thesis = {"error": str(exc)[:200]}
                cost = spent + _thesis_cost(ctx.symbol)
                refresh_files(ctx.symbol, ["trading_thesis.md", "latest_events.md"])
        return {"ticker": ctx.symbol, "status": "done", "model_id": model_id, "cost_usd": round(cost, 4),
                "valuation": _valuation_line(outputs), "thesis": thesis, "data_errors": errors}
    except Exception as exc:
        logger.exception("Quarterly rebuild failed for %s", symbol)
        return {"ticker": ctx.symbol, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300], "cost_usd": round(cost, 4)}


def _thesis_cost(symbol: str) -> float:
    """Cost of the thesis update just recorded for this ticker (the latest separate_thesis_update row)."""
    with session_scope() as s:
        row = s.scalar(select(CostEntry).where(CostEntry.ticker == symbol, CostEntry.kind == llm.SEPARATE_PREFIX + "thesis_update")
                       .order_by(CostEntry.ts.desc()).limit(1))
        return float(row.usd) if row else 0.0


def _valuation_line(outputs: dict) -> str:
    ff = {m["method"]: m for m in outputs["football_field"]}
    dcf = (ff.get("DCF") or {}).get("implied_price")
    pw = outputs.get("probability_weighted_price")
    cur = next((r for r in outputs["rows"] if r["year"] == outputs["current_year"]), {})
    parts = [f"{outputs['current_year']} revenue ${cur.get('revenue', 0):,.0f}mm"]
    if dcf is not None:
        parts.append(f"DCF ${dcf:,.2f}")
    if pw is not None:
        parts.append(f"probability-weighted ${pw:,.2f}")
    return ", ".join(parts)


def _save(ctx: TickerContext, base_model: dict | None, wb_session, inputs: QuarterlyModelInputs, outputs: dict, comps: dict,
          sec: dict, cost: float) -> int | None:
    """Save as the next version (written into the PM's workbook when the latest model is theirs)."""
    from agents.model_versions import save_agent_version

    fin = sec.get("financials") or {}
    with session_scope() as s:
        research_id = s.scalar(select(ResearchOutput.id).where(ResearchOutput.ticker_id == ctx.ticker_id)
                               .order_by(ResearchOutput.date.desc()).limit(1))
        parent = s.scalar(select(FinancialModel.id).where(FinancialModel.ticker_id == ctx.ticker_id)
                          .order_by(FinancialModel.date.desc()).limit(1))
    model_id = save_agent_version(
        ctx, base_model, wb_session, inputs.model_dump(), outputs, comps, research_id,
        {"historical_annual": fin.get("annual", []), "historical_quarterly": fin.get("quarterly", [])},
        {"source": "agent", "note": "Quarterly rebuild (model-only run)", "parent_model_id": parent, "cost_usd": round(cost, 4)})
    if model_id is not None:
        refresh_files(ctx.symbol, ["financial_model.json"])
    return model_id


def separate_spent() -> float:
    with session_scope() as s:
        return float(s.scalar(select(func.coalesce(func.sum(CostEntry.usd), 0.0)).where(CostEntry.kind.like(f"{llm.SEPARATE_PREFIX}%"))))


def quarterly_tickers(analyst_key: str | None = None) -> list[str]:
    return [c.symbol for c in active_contexts() if c.has_model and c.quarterly_model and (analyst_key is None or c.analyst_key == analyst_key)]


def run(analyst_key: str | None = "internet", max_usd: float = 1.0, tickers: list[str] | None = None) -> list[dict]:
    symbols = [t.upper() for t in tickers] if tickers else quarterly_tickers(analyst_key)
    logger.info("Quarterly rebuild: %s (cap $%.2f each)", ", ".join(symbols), max_usd)
    results = []
    for sym in symbols:
        r = rebuild_one(sym, max_usd)
        results.append(r)
        logger.info("Quarterly rebuild %s: %s $%.2f %s", sym, r["status"], r.get("cost_usd", 0), r.get("valuation") or r.get("error", ""))
        print(f"  {sym:<6} {r['status']:<8} ${r.get('cost_usd', 0):.2f}  {r.get('valuation') or r.get('error', '')}", flush=True)
    total = sum(r.get("cost_usd", 0) for r in results)
    print(f"Done: {sum(r['status'] == 'done' for r in results)}/{len(results)} rebuilt, ${total:.2f} total", flush=True)
    return results


if __name__ == "__main__":
    from database.db import init_db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--analyst", default="internet")
    p.add_argument("--max-usd", type=float, default=1.0, help="Hard cap per ticker")
    p.add_argument("tickers", nargs="*", help="Only these tickers (default: every public ticker of the analyst)")
    a = p.parse_args()
    init_db()
    run(a.analyst, a.max_usd, a.tickers or None)
