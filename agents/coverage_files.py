"""Per-ticker coverage files.

    coverage/{SYMBOL}/
      company_memo.md       background, business model, key people + the latest long-form memo
      latest_events.md      price action, filings, news, recent thesis triggers (no Claude)
      financial_model.json  model, valuation and scenarios from the latest deep dive
      trading_thesis.md     position, signal, entry/exit, risk, catalysts, dependencies (+ live price)
      meeting_notes.md      what other analysts said and what changed (newest meeting first)

The database is the source of truth (Railway's disk is wiped on deploy); every write is mirrored to
COVERAGE_DIR on disk for browsing locally.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from agents.registry import TickerContext, get_context
from agents.thesis import current_thesis, latest_model_outputs, latest_research, render_thesis_md, thesis_history
from data_sources.cache import get_cached
from data_sources.price_feed import latest_tick, price_history
from database.db import session_scope
from database.models import CoverageFile, FinancialModel
from utils.config import COVERAGE_DIR

logger = logging.getLogger(__name__)

FILENAMES = ["company_memo.md", "latest_events.md", "financial_model.json", "trading_thesis.md", "current_price.json", "meeting_notes.md"]
MAX_MEETINGS_IN_NOTES = 10
MEETING_SEPARATOR = "\n\n<!-- meeting -->\n\n"


def write_file(ctx: TickerContext, filename: str, content: str) -> None:
    with session_scope() as s:
        f = s.scalar(select(CoverageFile).where(CoverageFile.ticker_id == ctx.ticker_id, CoverageFile.filename == filename))
        if f is None:
            s.add(CoverageFile(ticker_id=ctx.ticker_id, filename=filename, content=content))
        else:
            f.content = content
            f.updated_at = datetime.now(timezone.utc)
    try:
        folder = Path(COVERAGE_DIR) / ctx.symbol
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not mirror %s/%s to disk: %s", ctx.symbol, filename, exc)


def read_file(symbol: str, filename: str) -> dict | None:
    ctx = get_context(symbol)
    if ctx is None:
        return None
    with session_scope() as s:
        f = s.scalar(select(CoverageFile).where(CoverageFile.ticker_id == ctx.ticker_id, CoverageFile.filename == filename))
        return {"filename": filename, "content": f.content, "updated_at": f.updated_at.isoformat()} if f else None


def list_files(symbol: str) -> list[dict]:
    ctx = get_context(symbol)
    if ctx is None:
        return []
    with session_scope() as s:
        rows = s.scalars(select(CoverageFile).where(CoverageFile.ticker_id == ctx.ticker_id)).all()
        return [{"filename": f.filename, "updated_at": f.updated_at.isoformat(), "chars": len(f.content)} for f in rows]


# --- Renderers ----------------------------------------------------------------------------

def render_company_memo(ctx: TickerContext) -> str:
    research = latest_research(ctx.ticker_id)
    if not research:
        return f"# {ctx.name} ({ctx.symbol})\n\n_No deep dive yet. The initial deep dive is queued._\n"
    bg = (research.get("details") or {}).get("company_background") or {}
    parts = [f"# {ctx.name} ({ctx.symbol}): company memo", f"*Last deep dive {research['date']} · {ctx.analyst_name}*"]
    if bg:
        parts += ["## Background", bg.get("overview", ""), "## Business model", bg.get("business_model", "")]
        if bg.get("key_people"):
            parts += ["## Key people", "\n".join(f"- {p}" for p in bg["key_people"])]
        if bg.get("history_and_structure"):
            parts += ["## History and structure", bg["history_and_structure"]]
    parts += ["---", research["long_form_memo"]]
    return "\n\n".join(p for p in parts if p)


def render_latest_events(ctx: TickerContext) -> str:
    lines = [f"# {ctx.name} ({ctx.symbol}): latest events", f"*Refreshed {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC*", ""]
    if ctx.price_symbol:
        tick = latest_tick(ctx.price_symbol)
        lines.append("## Price action")
        if tick:
            chg = f"{tick['change_pct']:+.2f}%" if tick["change_pct"] is not None else "n/a"
            lines.append(f"Last ${tick['price']:,.2f} ({chg} today; range {tick['day_low']}-{tick['day_high']}; prev close {tick['prev_close']})")
        hist = price_history(ctx.price_symbol, 15)
        if hist:
            lines += ["", "| Date | Last | Day change |", "|---|---|---|"]
            lines += [f"| {h['date']} | ${h['price']:,.2f} | {h['change_pct']:+.2f}% |" if h["change_pct"] is not None
                      else f"| {h['date']} | ${h['price']:,.2f} | n/a |" for h in reversed(hist)]
        lines.append("")
    sec = get_cached("sec", (ctx.sec_ticker or "").upper(), None) if ctx.sec_ticker else None
    if sec and sec.get("recent_filings"):
        lines += ["## Recent SEC filings", "| Form | Filed | Items |", "|---|---|---|"]
        lines += [f"| {f['form']} | {f['filing_date']} | {f.get('items') or ''} |" for f in sec["recent_filings"][:10]]
        lines.append("")
    news = get_cached("news", ctx.symbol, None)
    if news and news.get("articles"):
        s = news.get("sentiment_summary", {})
        lines += ["## News (last 30 days)",
                  f"Sentiment (word-list scorer): avg {s.get('average_score')}, {s.get('positive', 0)} positive / "
                  f"{s.get('negative', 0)} negative / {s.get('neutral', 0)} neutral", ""]
        lines += [f"- {(a.get('published_at') or '')[:10]} **{a['title']}** ({a.get('source')}, {a.get('sentiment')}) {a.get('url') or ''}"
                  for a in news["articles"]]
        lines.append("")
    th = current_thesis(ctx.ticker_id)
    if th and th["thesis"].get("next_catalysts"):
        lines += ["## Upcoming catalysts", *[f"- {c['timing']}: {c['event']}" for c in th["thesis"]["next_catalysts"]], ""]
    hist = thesis_history(ctx.ticker_id, 5)
    if hist:
        lines += ["## Recent thesis triggers", *[f"- {h['date'].strftime('%Y-%m-%d %H:%M')} UTC, {h['trigger']}: "
                                                  f"{h['signal']} ({h['conviction']}/10) {h['headline']}" for h in hist]]
    return "\n".join(lines)


def render_financial_model(ctx: TickerContext) -> str:
    outputs = latest_model_outputs(ctx.ticker_id)
    research = latest_research(ctx.ticker_id)
    if not outputs:
        reason = "no company model for private/index tickers" if not ctx.has_model else "no deep dive yet"
        return json.dumps({"ticker": ctx.symbol, "note": reason,
                           "summary": (research or {}).get("financial_model")}, indent=2)
    with session_scope() as s:
        fm = s.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ctx.ticker_id).order_by(FinancialModel.date.desc()).limit(1))
        inputs = fm.assumptions.get("inputs") or {}
        as_of = fm.date.date().isoformat()

    def r(x, nd=2):
        return round(x, nd) if isinstance(x, float) else x

    doc = {
        "ticker": ctx.symbol,
        "as_of": as_of,
        "units": "USD millions unless noted; percentages are whole numbers",
        "summary": (research or {}).get("financial_model"),
        "annual": [{k: r(v) for k, v in row.items()} for row in outputs["rows"]],
        "revenue_segments": [{"name": s["name"], "revenue_mm": [r(v) for v in s["revenue_mm"]]} for s in outputs["segments"]],
        "capitalization": {k: r(v) for k, v in outputs["capitalization"].items()},
        "valuation": {
            "football_field": [{k: r(v) for k, v in m.items()} for m in outputs["football_field"]],
            "probability_weighted_price": r(outputs["probability_weighted_price"]),
            "valuation_range": {k: r(v) for k, v in (outputs["valuation_range"] or {}).items()},
        },
        "scenarios": [{k: (r(v) if not isinstance(v, list) else [r(x) for x in v]) for k, v in sc.items()} for sc in outputs["scenarios"]],
        "dcf_assumptions": inputs.get("dcf"),
        "next_print": inputs.get("next_print"),
        "key_assumptions": inputs.get("key_assumptions"),
        "comps_primary_bucket": outputs["comps"]["primary_bucket"],
        "relative_value": _relative_value(outputs, ctx.symbol),
        "private_market": outputs.get("private_market"),
        "excel": f"/coverage/{ctx.symbol}/model.xlsx",
    }
    return json.dumps(doc, indent=2, default=str)


def _relative_value(outputs: dict, symbol: str) -> list[dict]:
    """Rank vs bucket peers, recomputed with today's cached LTM fundamentals and the bucket's multiple convention."""
    from agents.financial_model import relative_value
    from agents.registry import comps_basis_overrides
    from data_sources.fundamentals import peer_metrics

    from agents.registry import get_context
    from data_sources.price_feed import latest_tick

    peers = {p["symbol"] for b in (outputs.get("comps") or {}).get("buckets", {}).values() for p in b.get("peers", [])}
    cap = dict(outputs.get("capitalization") or {})
    ctx = get_context(symbol)
    tick = latest_tick(ctx.price_symbol) if ctx and ctx.price_symbol else None
    if tick and cap.get("enterprise_value") and cap.get("share_price") and cap.get("diluted_shares_mm"):
        ev = cap["enterprise_value"] + (tick["price"] - cap["share_price"]) * cap["diluted_shares_mm"]  # $mm
        rows = {r.get("year"): r for r in outputs.get("rows", [])}
        cur = rows.get(cap.get("current_year"), {})
        if ev > 0:
            cap.update(enterprise_value=ev, priced_at=tick["ts"],
                       ev_to_revenue_current_year=ev / cur["revenue"] if cur.get("revenue") else None,
                       ev_to_ebitda_current_year=ev / cur["adj_ebitda"] if (cur.get("adj_ebitda") or 0) > 0 else None)
    return relative_value({**outputs, "capitalization": cap}, symbol, {sym: peer_metrics(sym) for sym in peers}, comps_basis_overrides())


def price_snapshot(ctx: TickerContext) -> dict:
    """Current price + change + 52-week range + distance to the thesis levels."""
    from data_sources.price_feed import get_52_week_range

    if not ctx.price_symbol:
        return {"ticker": ctx.symbol, "price": None, "note": "private company, no market price"}
    tick = latest_tick(ctx.price_symbol)
    rng = get_52_week_range(ctx.price_symbol)
    th = current_thesis(ctx.ticker_id)
    t = th["thesis"] if th else {}
    price = tick["price"] if tick else None

    def dist(level):
        return round((level / price - 1) * 100, 2) if level is not None and price else None

    return {
        "ticker": ctx.symbol,
        "price_symbol": ctx.price_symbol,
        "price": price,
        "change_pct": tick["change_pct"] if tick else None,
        "day_high": tick["day_high"] if tick else None,
        "day_low": tick["day_low"] if tick else None,
        "prev_close": tick["prev_close"] if tick else None,
        "updated_at": tick["ts"] if tick else None,
        **{k: rng.get(k) for k in ("week_52_high", "week_52_low")},
        "levels": {
            "entry_zone_low": t.get("entry_zone_low"), "entry_zone_high": t.get("entry_zone_high"),
            "target_price": t.get("target_price"), "stop_loss": t.get("stop_loss"),
            "to_target_pct": dist(t.get("target_price")), "to_stop_pct": dist(t.get("stop_loss")),
        } if t else None,
    }


RENDERERS = {
    "company_memo.md": render_company_memo,
    "latest_events.md": render_latest_events,
    "financial_model.json": render_financial_model,
    "trading_thesis.md": render_thesis_md,
    "current_price.json": lambda ctx: json.dumps(price_snapshot(ctx), indent=2, default=str),
}


def archive_files(symbol: str) -> None:
    """Move a removed ticker's disk mirror to coverage/_archive/ (DB rows are kept)."""
    src = Path(COVERAGE_DIR) / symbol.upper()
    if src.exists():
        dst = Path(COVERAGE_DIR) / "_archive" / f"{symbol.upper()}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def refresh_files(symbol: str, filenames: list[str] | None = None) -> None:
    ctx = get_context(symbol)
    if ctx is None:
        return
    for name in filenames or list(RENDERERS):
        try:
            write_file(ctx, name, RENDERERS[name](ctx))
        except Exception:
            logger.exception("Rendering %s/%s failed", symbol, name)


def ensure_files(symbol: str) -> None:
    """Create all five files for a (new) ticker."""
    ctx = get_context(symbol)
    if ctx is None:
        return
    refresh_files(symbol)
    if read_file(symbol, "meeting_notes.md") is None:
        write_file(ctx, "meeting_notes.md", f"# {ctx.name} ({ctx.symbol}): meeting notes\n\n_No meetings yet._\n")


def add_meeting_notes(symbol: str, section_md: str) -> None:
    """Prepend this meeting's notes; keep the most recent MAX_MEETINGS_IN_NOTES."""
    ctx = get_context(symbol)
    if ctx is None:
        return
    existing = (read_file(symbol, "meeting_notes.md") or {}).get("content", "")
    header = f"# {ctx.name} ({ctx.symbol}): meeting notes"
    body = existing.split("\n", 1)[1] if existing.startswith("# ") and "\n" in existing else ""  # any title, incl. older ticker-only ones
    previous = [b.strip() for b in body.split(MEETING_SEPARATOR) if b.strip() and "_No meetings yet._" not in b]
    sections = [section_md.strip()] + previous[: MAX_MEETINGS_IN_NOTES - 1]
    write_file(ctx, "meeting_notes.md", header + "\n\n" + MEETING_SEPARATOR.join(sections) + "\n")
