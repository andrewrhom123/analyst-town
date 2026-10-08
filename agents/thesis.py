"""Trading thesis: the active position-management view for each ticker.

Created by a deep dive, then kept current by cheap light-model updates when the price moves >5%
since the last update, when price crosses the thesis' own entry/target/stop levels, and after meetings.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents import llm
from agents.philosophy import framework_block
from agents.registry import FOCUS_METRICS, TickerContext, get_context
from data_sources.cache import get_cached
from data_sources.news_fetcher import get_news
from data_sources.price_feed import latest_tick, price_history
from database.db import session_scope
from database.models import AgentLesson, CoverageFile, FinancialModel, ResearchOutput, Ticker, TradingThesis
from utils import config

logger = logging.getLogger(__name__)


class Catalyst(BaseModel):
    event: str
    timing: str = Field(description="Date or window, e.g. 'early Nov 2026 (Q3 print)'")
    why_it_matters: str


class Dependency(BaseModel):
    ticker: str = Field(description="Other ticker (covered or not)")
    relationship: str = Field(description="e.g. 'competitor', 'customer', 'read-through', 'macro driver'")
    impact: str = Field(description="How that name's developments move this thesis")


class TradeStructure(BaseModel):
    structure: Literal["outright_long", "outright_short", "pair", "hedged", "no_trade"] = Field(
        description="How to express the view; prefer a pair or hedge that strips out beta/sector risk")
    hedge_ticker: str | None = Field(description="The other leg (short for a long, long for a short), or null for outright/no trade")
    rationale: str = Field(description="Why this structure isolates the idea, e.g. 'long BLSH / short COIN strips out crypto beta'")


class TradingThesisOut(BaseModel):
    position: Literal["long", "short", "flat"]
    signal: Literal["buy", "add", "hold", "trim", "sell", "short", "cover", "watch"] = Field(
        description="Position-management action for the PM (not a sell-side rating)")
    headline: str = Field(description="One-line trade view, e.g. 'Market is missing the take-rate expansion; long vs. COIN', 'Edge gone, step aside'")
    conviction_level: int = Field(description="1-10, how much real edge we have")
    thesis: str = Field(description="1-2 paragraphs of narrative: what the business does and earns, the trade-offs, what is priced in")
    market_missing: str = Field(description="What the market is not seeing (the inefficiency), why it is mispriced now, and the data behind it")
    trade_structure: TradeStructure
    entry_zone_low: float | None = Field(description="Price where you would initiate/add (long) or short; null for flat with no level")
    entry_zone_high: float | None
    target_price: float | None
    stop_loss: float | None = Field(description="Price that proves the thesis wrong")
    time_horizon: str
    risk_level: Literal["low", "moderate", "elevated", "high"]
    risk_trend: Literal["decreasing", "stable", "increasing"]
    risk_factors: list[str]
    next_catalysts: list[Catalyst]
    cross_ticker_dependencies: list[Dependency]
    what_changed: str = Field(description="What changed since the previous thesis and why the call did or did not move")
    needs_deep_dive: bool = Field(description="True only if fundamentals may have changed (earnings, guidance, M&A, regulation) and a full SEC/model refresh is warranted")
    deep_dive_reason: str


# --- Reading -----------------------------------------------------------------------

def current_thesis(ticker_id: int) -> dict | None:
    with session_scope() as s:
        t = s.scalar(select(TradingThesis).where(TradingThesis.ticker_id == ticker_id).order_by(TradingThesis.created_at.desc()).limit(1))
        if t is None:
            return None
        return {"thesis": t.thesis, "created_at": t.created_at, "trigger": t.trigger, "trigger_detail": t.trigger_detail,
                "price_at_update": t.price_at_update, "model": t.model_used}


def thesis_history(ticker_id: int, limit: int = 8) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(TradingThesis).where(TradingThesis.ticker_id == ticker_id)
                         .order_by(TradingThesis.created_at.desc()).limit(limit)).all()
        return [{"date": r.created_at, "trigger": r.trigger, "signal": r.thesis.get("signal"),
                 "position": r.thesis.get("position"), "conviction": r.thesis.get("conviction_level"),
                 "price": r.price_at_update, "headline": r.thesis.get("headline")} for r in rows]


def latest_research(ticker_id: int) -> dict | None:
    with session_scope() as s:
        r = s.scalar(select(ResearchOutput).where(ResearchOutput.ticker_id == ticker_id).order_by(ResearchOutput.date.desc()).limit(1))
        return {"date": r.date.date().isoformat(), **r.research_memo} if r else None


def latest_model_outputs(ticker_id: int) -> dict | None:
    with session_scope() as s:
        m = s.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ticker_id).order_by(FinancialModel.date.desc()).limit(1))
        return (m.assumptions or {}).get("outputs") if m else None


def analyst_lessons(analyst_id: int, limit: int = 10) -> list[str]:
    with session_scope() as s:
        rows = s.scalars(select(AgentLesson).where(AgentLesson.analyst_id == analyst_id).order_by(AgentLesson.created_at.desc()).limit(limit)).all()
        return [r.lesson for r in rows]


def pod_snapshot(exclude_ticker_id: int | None = None) -> list[dict]:
    """One line per covered ticker: the pod's current calls, for cross-ticker reasoning."""
    with session_scope() as s:
        tickers = s.scalars(select(Ticker).where(Ticker.active.is_(True))).all()
        out = []
        for t in tickers:
            if t.id == exclude_ticker_id:
                continue
            th = s.scalar(select(TradingThesis).where(TradingThesis.ticker_id == t.id).order_by(TradingThesis.created_at.desc()).limit(1))
            out.append({"ticker": t.symbol, "company": t.name, "analyst": t.analyst.name,
                        "signal": th.thesis.get("signal") if th else None,
                        "position": th.thesis.get("position") if th else None,
                        "conviction": th.thesis.get("conviction_level") if th else None,
                        "headline": th.thesis.get("headline") if th else "no thesis yet"})
        return out


def compact_research(research: dict | None) -> dict | None:
    if not research:
        return None
    d = research.get("details") or {}
    return {
        "date": research.get("date"),
        "executive_summary": research.get("executive_summary"),
        "financial_model": research.get("financial_model"),
        "conviction_level": research.get("conviction_level"),
        "reasoning": research.get("reasoning"),
        "key_risks": research.get("key_risks"),
        "catalysts": d.get("catalysts"),
        "valuation": d.get("valuation"),
    }


# --- Rendering -------------------------------------------------------------------------

def _money(x):
    return "n/a" if x is None else f"${x:,.2f}"


def _vs(level, price):
    if level is None or not price:
        return ""
    return f" ({(level / price - 1) * 100:+.0f}% from here)"


def render_thesis_md(ctx: TickerContext) -> str:
    th = current_thesis(ctx.ticker_id)
    tick = latest_tick(ctx.price_symbol) if ctx.price_symbol else None
    price = tick["price"] if tick else (th["price_at_update"] if th else None)
    lines = [f"# {ctx.name} ({ctx.symbol}): trading thesis", f"*Covered by {ctx.analyst_name}*", ""]
    if tick:
        chg = f"{tick['change_pct']:+.2f}% today" if tick["change_pct"] is not None else ""
        lines.append(f"**Price:** {_money(tick['price'])} {chg} · as of {tick['ts'][:16].replace('T', ' ')} UTC")
    elif ctx.price_symbol is None:
        lines.append("**Price:** private company, no market price")
    if th is None:
        lines += ["", "_No thesis yet: the initial deep dive is queued._"]
        return "\n".join(lines)
    t = th["thesis"]
    ts = t.get("trade_structure") or {}
    lines += [
        f"**View:** {t['headline']} · action: {t['signal']}",
        f"**Position:** {t['position']} · **Conviction:** {t['conviction_level']}/10 · **Horizon:** {t['time_horizon']}",
        f"**Entry zone:** {_money(t['entry_zone_low'])} - {_money(t['entry_zone_high'])} · "
        f"**Target:** {_money(t['target_price'])}{_vs(t['target_price'], price)} · "
        f"**Stop:** {_money(t['stop_loss'])}{_vs(t['stop_loss'], price)}",
        f"**Risk:** {t['risk_level']}, {t['risk_trend']}",
        f"*Updated {th['created_at'].strftime('%Y-%m-%d %H:%M')} UTC by {th['trigger'].replace('_', ' ')}"
        + (f" ({th['trigger_detail']})" if th["trigger_detail"] else "") + f" at {_money(th['price_at_update'])}*",
        "", "## Thesis", t["thesis"],
        *(["", "## What the market is missing", t["market_missing"]] if t.get("market_missing") else []),
        *(["", "## Trade structure", f"**{ts['structure'].replace('_', ' ')}**"
           + (f" · hedge leg: {ts['hedge_ticker']}" if ts.get("hedge_ticker") else "") + f": {ts['rationale']}"] if ts else []),
        "", "## What changed", t["what_changed"],
        "", "## Risk factors", *[f"- {r}" for r in t["risk_factors"]],
        "", "## Next catalysts", "| Event | Timing | Why it matters |", "|---|---|---|",
        *[f"| {c['event']} | {c['timing']} | {c['why_it_matters']} |" for c in t["next_catalysts"]],
        "", "## Cross-ticker dependencies", "| Ticker | Relationship | Impact |", "|---|---|---|",
        *[f"| {d['ticker']} | {d['relationship']} | {d['impact']} |" for d in t["cross_ticker_dependencies"]],
        "", "## Recent calls", "| When (UTC) | Trigger | Signal | Position | Conviction | Price | Headline |", "|---|---|---|---|---|---|---|",
        *[f"| {h['date'].strftime('%Y-%m-%d %H:%M')} | {h['trigger']} | {h['signal']} | {h['position']} | {h['conviction']} | "
          f"{_money(h['price'])} | {h['headline']} |" for h in thesis_history(ctx.ticker_id)],
    ]
    return "\n".join(lines)


# --- Saving --------------------------------------------------------------------------

def save_thesis(ctx: TickerContext, thesis: TradingThesisOut | dict, trigger: str, detail: str, model: str) -> dict:
    data = thesis.model_dump() if isinstance(thesis, BaseModel) else dict(thesis)
    data["conviction_level"] = max(1, min(10, int(data["conviction_level"])))
    tick = latest_tick(ctx.price_symbol) if ctx.price_symbol else None
    with session_scope() as s:
        s.add(TradingThesis(ticker_id=ctx.ticker_id, trigger=trigger, trigger_detail=detail,
                            price_at_update=tick["price"] if tick else None, thesis=data, model_used=model))
        t = s.get(Ticker, ctx.ticker_id)
        t.last_thesis_at = datetime.now(timezone.utc)
    return data


# --- Light-model update -------------------------------------------------------------------

def _system(ctx: TickerContext) -> str:
    return f"""You are {ctx.analyst_name}, an analyst in a small research pod, managing the trading view on {ctx.symbol} ({ctx.description}).
Your analyst focus: {ctx.analyst_focus}
Ticker focus: {ctx.focus_notes or 'n/a'}
The PM cares about: {", ".join(FOCUS_METRICS)}.

You are updating the TRADING THESIS between full research deep dives: the narrative, what the market is missing, the trade structure (pair/hedge), position and action, entry/exit levels, risk, catalysts, and cross-ticker dependencies. Think like a PM managing a position, not a report writer.

{framework_block()}

Rules:
- Anchor levels to the computed valuation from the last deep dive (DCF, comps, scenario prices) and to recent price action. Levels must be coherent with the position: long -> stop below price, target above; short -> stop above, target below.
- Explain moves with evidence (news, peers, macro). If you can't find a reason in the data, say the move is unexplained rather than inventing one.
- Do not invent fundamentals. If the move or news suggests fundamentals changed (earnings, guidance, M&A, regulation), set needs_deep_dive with the reason; keep the trading call conservative until then.
- Changing the signal requires a reason; holding steady through noise is a valid call. Say what is priced in.
- Use the pod snapshot and your colleagues' views for cross-ticker dependencies; name the specific linkage.
- First person, plain sentences, numbers over adjectives."""


def _model_view(ticker_id: int) -> dict | None:
    """Latest model valuation (the PM's edited version when there is one): anchor the levels on it."""
    with session_scope() as s:
        fm = s.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ticker_id).order_by(FinancialModel.date.desc()).limit(1))
        if fm is None:
            return None
        a = fm.assumptions or {}
        o = a.get("outputs") or {}
        return {"source": "PM's edited model" if a.get("source") == "pm_upload" else "your model",
                "pm_note": a.get("note"), "pm_changes": [{k: c[k] for k in ("label", "old", "new")} for c in a.get("changes") or []],
                "football_field": [{k: m.get(k) for k in ("method", "implied_price", "upside_pct")} for m in o.get("football_field", [])],
                "probability_weighted_price": o.get("probability_weighted_price")}


def build_update_prompt(ctx: TickerContext, trigger: str, detail: str) -> str:
    th = current_thesis(ctx.ticker_id)
    news = get_cached("news", ctx.symbol, None) or {}
    meeting_notes = ""
    with session_scope() as s:
        f = s.scalar(select(CoverageFile).where(CoverageFile.ticker_id == ctx.ticker_id, CoverageFile.filename == "meeting_notes.md"))
        if f:
            meeting_notes = f.content[:4000]
    payload = {
        "trigger": {"type": trigger, "detail": detail, "time_utc": datetime.now(timezone.utc).isoformat(timespec="minutes")},
        "price_now": latest_tick(ctx.price_symbol) if ctx.price_symbol else None,
        "recent_daily_prices": price_history(ctx.price_symbol, 15) if ctx.price_symbol else [],
        "current_thesis": th["thesis"] if th else None,
        "current_thesis_set_at": {"time": th["created_at"].isoformat(), "price": th["price_at_update"], "trigger": th["trigger"]} if th else None,
        "last_deep_dive": compact_research(latest_research(ctx.ticker_id)),
        "current_model_valuation": _model_view(ctx.ticker_id),
        "news_headlines": [{k: a.get(k) for k in ("published_at", "title", "source", "sentiment")} for a in news.get("articles", [])],
        "pod_snapshot": pod_snapshot(exclude_ticker_id=ctx.ticker_id),
        "your_lessons_from_meetings": analyst_lessons(ctx.analyst_id),
    }
    return (f"<context>\n{json.dumps(payload, default=str, indent=1)}\n</context>\n\n"
            + (f"<latest_meeting_notes>\n{meeting_notes}\n</latest_meeting_notes>\n\n" if meeting_notes else "")
            + "Update the trading thesis for this trigger.")


def update_thesis(symbol: str, trigger: str, detail: str) -> dict:
    """Light-model thesis update. Returns the saved thesis (includes needs_deep_dive)."""
    ctx = get_context(symbol)
    if ctx is None:
        raise llm.AnalystError(f"Unknown ticker {symbol}")
    llm.require_budget("thesis_update", config.THESIS_UPDATE_ESTIMATE_USD)
    try:  # refresh headlines if stale (NewsAPI, cached a day)
        get_news(ctx.symbol, ctx.news_query, ctx.news_keywords)
    except Exception:
        pass
    parsed, message = llm.parse_call(
        "thesis_update", TradingThesisOut, _system(ctx),
        [{"role": "user", "content": build_update_prompt(ctx, trigger, detail)}],
        ticker=ctx.symbol,
    )
    return save_thesis(ctx, parsed, trigger, detail, message.model)


# --- Level checks (no Claude) ----------------------------------------------------------------

def backfill_reference_price(ticker_id: int, price: float) -> bool:
    """A thesis saved before any quote existed has no reference price; anchor it to the first quote seen."""
    with session_scope() as s:
        t = s.scalar(select(TradingThesis).where(TradingThesis.ticker_id == ticker_id).order_by(TradingThesis.created_at.desc()).limit(1))
        if t is None or t.price_at_update is not None:
            return False
        t.price_at_update = price
        return True



def price_triggers(ctx: TickerContext, price: float) -> str | None:
    """Return a trigger description if price moved enough / crossed a level since the last thesis."""
    th = current_thesis(ctx.ticker_id)
    if th is None or price is None:
        return None
    t, ref = th["thesis"], th["price_at_update"]
    lo, hi, stop, target = t.get("entry_zone_low"), t.get("entry_zone_high"), t.get("stop_loss"), t.get("target_price")
    long_side = t.get("position") != "short"
    if stop is not None and ((long_side and price <= stop) or (not long_side and price >= stop)):
        return f"level_cross: stop {stop:,.2f} hit at ${price:,.2f}"
    if target is not None and ((long_side and price >= target) or (not long_side and price <= target)):
        return f"level_cross: target {target:,.2f} reached at ${price:,.2f}"
    if ref:
        move = (price / ref - 1) * 100
        if abs(move) >= config.PRICE_MOVE_TRIGGER_PCT:
            return f"price_move: {move:+.1f}% since last thesis (${ref:,.2f} -> ${price:,.2f})"
    if lo is not None and hi is not None and lo <= price <= hi and ref is not None and not (lo <= ref <= hi) \
            and t.get("signal") in ("watch", "hold", "buy", "short"):
        return f"level_cross: price ${price:,.2f} entered entry zone {lo:,.2f}-{hi:,.2f}"
    return None
