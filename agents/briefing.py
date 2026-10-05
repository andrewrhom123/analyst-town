"""On-demand interaction: /briefing (instant, no Claude), /ask (light model), command parsing."""

import json
import re

from sqlalchemy import select

from agents import llm
from agents.coverage_files import price_snapshot, read_file
from agents.registry import FOCUS_METRICS, get_context
from agents.thesis import analyst_lessons, compact_research, current_thesis, latest_research, pod_snapshot, render_thesis_md
from data_sources.price_feed import latest_tick, price_history
from database.db import session_scope
from database.models import Interaction
from utils import config


def briefing(symbol: str) -> dict:
    """Instant summary of the agent's current thinking, assembled from stored state ($0, no latency)."""
    ctx = get_context(symbol)
    if ctx is None:
        raise llm.AnalystError(f"Unknown ticker {symbol}")
    th = current_thesis(ctx.ticker_id)
    research = compact_research(latest_research(ctx.ticker_id))
    tick = latest_tick(ctx.price_symbol) if ctx.price_symbol else None
    notes = (read_file(ctx.symbol, "meeting_notes.md") or {}).get("content", "")
    latest_meeting = notes.split("<!-- meeting -->")[0].split("\n", 2)[-1].strip() if "## Meeting" in notes else None

    parts = [render_thesis_md(ctx)]
    if research:
        parts += ["", f"## Last deep dive ({research['date']})", research["executive_summary"] or ""]
        if research.get("financial_model"):
            parts.append("Model: " + ", ".join(f"{k} {v}" for k, v in research["financial_model"].items()))
    if latest_meeting:
        parts += ["", "## Latest meeting", latest_meeting]
    t = th["thesis"] if th else {}
    full = latest_research(ctx.ticker_id) or {}
    details = full.get("details") or {}
    return {
        "ticker": ctx.symbol,
        "name": ctx.name,
        "type": ctx.ticker_type,
        "analyst": ctx.analyst_name,
        "agent_id": ctx.analyst_key,
        "price": price_snapshot(ctx),
        "title": details.get("title"),
        "stance": details.get("stance"),
        "key_metric": details.get("key_metric"),
        "research_conviction": full.get("conviction_level"),
        "research_date": full.get("date"),
        "thesis": t.get("thesis"),
        "risk_factors": t.get("risk_factors"),
        "next_catalysts": t.get("next_catalysts"),
        "cross_ticker_dependencies": t.get("cross_ticker_dependencies"),
        "time_horizon": t.get("time_horizon"),
        "signal": t.get("signal"),
        "position": t.get("position"),
        "headline": t.get("headline"),
        "conviction_level": t.get("conviction_level"),
        "levels": {k: t.get(k) for k in ("entry_zone_low", "entry_zone_high", "target_price", "stop_loss")} if t else None,
        "risk": {"level": t.get("risk_level"), "trend": t.get("risk_trend")} if t else None,
        "thesis_updated": th["created_at"].isoformat() if th else None,
        "executive_summary": research["executive_summary"] if research else None,
        "financial_model": research["financial_model"] if research else None,
        "briefing_md": "\n".join(parts),
    }


def ask(symbol: str, question: str) -> dict:
    """The covering analyst answers immediately (light model, low effort), grounded in its stored work."""
    ctx = get_context(symbol)
    if ctx is None:
        raise llm.AnalystError(f"Unknown ticker {symbol}")
    llm.require_budget("ask", 0.05)
    th = current_thesis(ctx.ticker_id)
    with session_scope() as s:
        history = s.scalars(select(Interaction).where(Interaction.ticker_id == ctx.ticker_id)
                            .order_by(Interaction.date.desc()).limit(6)).all()[::-1]
        turns = [(h.user_question, h.agent_response) for h in history]
    model_json = (read_file(ctx.symbol, "financial_model.json") or {}).get("content", "")
    context = {
        "price_now": latest_tick(ctx.price_symbol) if ctx.price_symbol else None,
        "recent_daily_prices": price_history(ctx.price_symbol, 10) if ctx.price_symbol else [],
        "trading_thesis": th["thesis"] if th else None,
        "last_deep_dive": compact_research(latest_research(ctx.ticker_id)),
        "pod_snapshot": pod_snapshot(exclude_ticker_id=ctx.ticker_id),
        "lessons": analyst_lessons(ctx.analyst_id),
    }
    system = (
        f"You are {ctx.analyst_name}, covering {ctx.symbol} ({ctx.description}) in a research pod. The PM is asking you "
        f"a question directly. They care about {', '.join(FOCUS_METRICS)} and position management.\n"
        "Answer immediately and concisely in the first person, like a sharp analyst at the PM's desk: lead with the answer, "
        "then the reasoning. For a what-if (e.g. 'earnings miss 20%'), show the math off your model: which inputs move and "
        "what happens to revenue, EBITDA, FCF, the implied value and your entry/exit levels. If you lack the data, say what "
        "you'd need. Never invent numbers that aren't in your work below.\n\n"
        f"<your_current_work>\n{json.dumps(context, default=str, indent=1)}\n</your_current_work>\n\n"
        f"<financial_model_json>\n{model_json[:20000]}\n</financial_model_json>"
    )
    messages = []
    for q, a in turns:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    messages.append({"role": "user", "content": question})
    answer = llm.text_call("ask", system, messages, ticker=ctx.symbol, effort=config.ASK_EFFORT)
    with session_scope() as s:
        row = Interaction(ticker_id=ctx.ticker_id, user_question=question, agent_response=answer)
        s.add(row)
        s.flush()
        return {"id": row.id, "ticker": ctx.symbol, "analyst": ctx.analyst_name, "question": question,
                "answer": answer, "date": row.date.isoformat()}


COMMAND = re.compile(r"^/(?P<cmd>briefing|ask|meeting|deepdive|update|add|remove|reassign|coverage)\b\s*(?P<rest>.*)$",
                     re.IGNORECASE | re.DOTALL)
HELP = ("Commands: /briefing TICKER · /ask TICKER: question · /meeting · /add TICKER to AGENT · "
        "/remove TICKER from AGENT · /reassign TICKER from AGENT to AGENT · /coverage AGENT · /deepdive TICKER · /update TICKER")


def parse_command(text: str) -> dict:
    """'/briefing TTD', '/ask TTD: what if earnings miss 20%?', '/meeting', '/add MSTR to Fintech',
    '/remove MARA from Fintech', '/reassign AMZN from AI to Internet Platforms', '/coverage Fintech'."""
    m = COMMAND.match(text.strip())
    if not m:
        raise ValueError(HELP)
    cmd, rest = m.group("cmd").lower(), m.group("rest").strip()
    if cmd == "meeting":
        return {"command": "meeting"}
    if cmd == "coverage":
        return {"command": "coverage", "agent": rest or None}
    if cmd == "add":
        mm = re.match(r"^(\S+)\s+to\s+(.+)$", rest, re.IGNORECASE)
        if not mm:
            raise ValueError("Usage: /add TICKER to AGENT  (e.g. /add MSTR to Fintech)")
        return {"command": "add", "ticker": mm.group(1).upper(), "agent": mm.group(2).strip()}
    if cmd == "remove":
        mm = re.match(r"^(\S+)(?:\s+from\s+(.+))?$", rest, re.IGNORECASE)
        if not mm:
            raise ValueError("Usage: /remove TICKER from AGENT")
        return {"command": "remove", "ticker": mm.group(1).upper(), "agent": (mm.group(2) or "").strip() or None}
    if cmd == "reassign":
        mm = re.match(r"^(\S+)\s+(?:from\s+(.+?)\s+)?to\s+(.+)$", rest, re.IGNORECASE)
        if not mm:
            raise ValueError("Usage: /reassign TICKER from AGENT to AGENT")
        return {"command": "reassign", "ticker": mm.group(1).upper(), "from_agent": (mm.group(2) or "").strip() or None,
                "to_agent": mm.group(3).strip()}
    if ":" in rest:
        ticker, question = rest.split(":", 1)
    else:
        ticker, _, question = rest.partition(" ")
    ticker, question = ticker.strip().upper(), question.strip()
    if not ticker:
        raise ValueError(f"/{cmd} needs a ticker")
    if cmd == "ask":
        if not question:
            raise ValueError("Usage: /ask TICKER: your question")
        return {"command": "ask", "ticker": ticker, "question": question}
    return {"command": cmd, "ticker": ticker}
