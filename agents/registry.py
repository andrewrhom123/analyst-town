"""Coverage registry: analysts, the tickers they cover, and runtime coverage changes.

Source of truth is the database. coverage.yaml seeds it: on startup (or POST /coverage/reload) every
analyst/ticker in the file is created or updated; analysts dropped from the file are retired once they
have no active tickers. Day to day, coverage changes come from chat commands (/add, /remove, /reassign)
and live only in the database - no code changes or redeploys.
"""

import logging
from dataclasses import dataclass, field

import yaml
from sqlalchemy import select

from database.db import session_scope
from database.models import Analyst, Ticker
from utils.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

COVERAGE_FILE = PROJECT_ROOT / "coverage.yaml"
FOCUS_METRICS = ["revenue growth", "margins", "user adoption", "competitive threats"]
TICKER_TYPES = {"public", "foreign", "private", "index"}
AGENT_TYPES = {"company", "macro", "sector"}
DEFAULT_COLORS = ["#22d3ee", "#e879f9", "#4ade80", "#fbbf24", "#60a5fa", "#f87171"]


class CoverageError(ValueError):
    pass


@dataclass(frozen=True)
class TickerContext:
    """Everything an agent needs to know about one covered ticker (detached from the DB session)."""

    ticker_id: int
    symbol: str
    name: str
    ticker_type: str
    description: str
    sec_ticker: str | None
    price_symbol: str | None
    news_query: str
    news_keywords: list[str]
    competitors: list[str]
    comps_buckets: dict[str, list[str]]
    focus_notes: str
    analyst_id: int
    analyst_key: str
    analyst_name: str
    agent_type: str
    analyst_focus: str
    active: bool = True
    aliases: list[str] = field(default_factory=list)
    sibling_symbols: list[str] = field(default_factory=list)  # other tickers the same analyst covers

    @property
    def has_model(self) -> bool:
        return self.ticker_type == "public"

    @property
    def uses_macro_indicators(self) -> bool:
        return self.agent_type == "macro"


def _context(t: Ticker) -> TickerContext:
    a = t.analyst
    return TickerContext(
        ticker_id=t.id, symbol=t.symbol, name=t.name, ticker_type=t.ticker_type, description=t.description or t.name,
        sec_ticker=t.sec_ticker, price_symbol=t.price_symbol, news_query=t.news_query or f'"{t.name}"',
        news_keywords=t.news_keywords or [t.name.lower()], competitors=t.competitors or [],
        comps_buckets=t.comps_buckets or {}, focus_notes=t.focus_notes or "",
        analyst_id=a.id, analyst_key=a.key, analyst_name=a.name, agent_type=a.agent_type, analyst_focus=a.focus_notes or "",
        active=t.active, aliases=t.aliases or [],
        sibling_symbols=[x.symbol for x in a.tickers if x.active and x.id != t.id],
    )


def _find_ticker(s, symbol: str) -> Ticker | None:
    symbol = symbol.strip().upper()
    t = s.scalar(select(Ticker).where(Ticker.symbol == symbol))
    if t is not None:
        return t
    for t in s.scalars(select(Ticker)):  # aliases (e.g. SQ -> XYZ); coverage is small
        if symbol in [a.upper() for a in (t.aliases or [])]:
            return t
    return None


def get_context(symbol: str) -> TickerContext | None:
    with session_scope() as s:
        t = _find_ticker(s, symbol)
        return _context(t) if t else None


def active_contexts() -> list[TickerContext]:
    with session_scope() as s:
        return [_context(t) for t in s.scalars(select(Ticker).where(Ticker.active.is_(True)).order_by(Ticker.id))]


def resolve_analyst(s, name_or_key: str) -> Analyst:
    """Match 'fintech', 'Fintech', 'Fintech Analyst', 'Internet Platforms', 'AI'..."""
    wanted = name_or_key.strip().lower()
    analysts = s.scalars(select(Analyst)).all()
    for a in analysts:
        names = {a.key.lower(), a.name.lower(), a.name.lower().replace(" analyst", "").replace(" strategist", "")}
        if wanted in names:
            return a
    matches = [a for a in analysts if a.active and (a.name.lower().startswith(wanted) or a.key.startswith(wanted))]
    if len(matches) == 1:
        return matches[0]
    active = [a.name for a in analysts if a.active]
    raise CoverageError(f"Unknown agent {name_or_key!r}. Agents: {', '.join(active)}")


def _apply_ticker_fields(t: Ticker, spec: dict) -> None:
    ticker_type = spec.get("type", t.ticker_type or "public")
    if ticker_type not in TICKER_TYPES:
        raise CoverageError(f"{spec.get('symbol')}: type must be one of {sorted(TICKER_TYPES)}")
    symbol = spec["symbol"].upper()
    t.name = spec.get("name", t.name or symbol)
    t.ticker_type = ticker_type
    t.aliases = [a.upper() for a in spec.get("aliases", t.aliases or [])]
    t.description = spec.get("description", t.description or "")
    t.sec_ticker = spec.get("sec_ticker", symbol if ticker_type == "public" else None)
    t.price_symbol = spec.get("price_symbol", None if ticker_type == "private" else symbol)
    t.news_query = spec.get("news_query", t.news_query or f'"{t.name}"')
    t.news_keywords = spec.get("news_keywords", t.news_keywords or [t.name.lower()])
    t.competitors = spec.get("competitors", t.competitors or [])
    t.comps_buckets = spec.get("comps_buckets", t.comps_buckets or {})
    t.focus_notes = spec.get("focus_notes", t.focus_notes or "")


def _upsert_analyst(s, spec: dict, index: int = 0) -> Analyst:
    if spec.get("type", "company") not in AGENT_TYPES:
        raise CoverageError(f"analyst {spec.get('key')}: type must be one of {sorted(AGENT_TYPES)}")
    a = s.scalar(select(Analyst).where(Analyst.key == spec["key"]))
    if a is None:
        a = Analyst(key=spec["key"], name=spec["name"], agent_type=spec.get("type", "company"))
        s.add(a)
    a.name = spec.get("name", a.name)
    a.agent_type = spec.get("type", a.agent_type)
    a.focus_notes = spec.get("focus_notes", a.focus_notes or "")
    a.color = spec.get("color", a.color or DEFAULT_COLORS[index % len(DEFAULT_COLORS)])
    a.active = True
    s.flush()
    return a


def sync_from_yaml() -> list[str]:
    """Create/update analysts and tickers from coverage.yaml. Returns symbols that were newly added."""
    config = yaml.safe_load(COVERAGE_FILE.read_text(encoding="utf-8")) or {}
    added, keys = [], set()
    with session_scope() as s:
        for i, a_spec in enumerate(config.get("analysts", [])):
            analyst = _upsert_analyst(s, a_spec, i)
            keys.add(analyst.key)
            for t_spec in a_spec.get("tickers", []):
                symbol = t_spec["symbol"].upper()
                t = s.scalar(select(Ticker).where(Ticker.symbol == symbol))
                if t is None:
                    t = Ticker(symbol=symbol, analyst_id=analyst.id, ticker_type=t_spec.get("type", "public"), name=t_spec.get("name", symbol))
                    s.add(t)
                    added.append(symbol)
                t.analyst_id = analyst.id
                t.active = True
                _apply_ticker_fields(t, t_spec)
        s.flush()
        # Retire analysts that left the roster once nothing active is assigned to them.
        for a in s.scalars(select(Analyst).where(Analyst.key.not_in(keys))):
            if not any(t.active for t in a.tickers):
                a.active = False
    if added:
        logger.info("Coverage added from coverage.yaml: %s", ", ".join(added))
    return added


# --- Runtime coverage management (chat + API) ------------------------------------------------

def add_analyst(spec: dict) -> dict:
    with session_scope() as s:
        a = _upsert_analyst(s, spec, len(s.scalars(select(Analyst)).all()))
        return {"key": a.key, "name": a.name, "agent_type": a.agent_type, "color": a.color}


def add_ticker(spec: dict) -> str:
    """Add (or re-activate) a ticker in an analyst's coverage. Returns the symbol."""
    symbol = spec["symbol"].upper()
    with session_scope() as s:
        analyst = resolve_analyst(s, spec["analyst"])
        t = _find_ticker(s, symbol)
        if t is not None and t.active:
            raise CoverageError(f"{t.symbol} is already covered by {t.analyst.name}")
        if t is None:
            t = Ticker(symbol=symbol, analyst_id=analyst.id, ticker_type=spec.get("type", "public"), name=spec.get("name", symbol))
            s.add(t)
        t.analyst_id = analyst.id
        t.active = True
        _apply_ticker_fields(t, {**spec, "symbol": t.symbol})
        return t.symbol


def remove_ticker(symbol: str, analyst_name: str | None = None) -> str:
    """Stop tracking a ticker (files and history are archived, not deleted)."""
    with session_scope() as s:
        t = _find_ticker(s, symbol)
        if t is None or not t.active:
            raise CoverageError(f"{symbol.upper()} is not in active coverage")
        if analyst_name:
            a = resolve_analyst(s, analyst_name)
            if a.id != t.analyst_id:
                raise CoverageError(f"{t.symbol} is covered by {t.analyst.name}, not {a.name}")
        t.active = False
        return t.symbol


def reassign_ticker(symbol: str, from_name: str | None, to_name: str) -> dict:
    with session_scope() as s:
        t = _find_ticker(s, symbol)
        if t is None:
            raise CoverageError(f"Unknown ticker {symbol.upper()}")
        if from_name:
            src = resolve_analyst(s, from_name)
            if src.id != t.analyst_id:
                raise CoverageError(f"{t.symbol} is covered by {t.analyst.name}, not {src.name}")
        dst = resolve_analyst(s, to_name)
        previous = t.analyst.name
        t.analyst_id = dst.id
        t.active = True
        return {"symbol": t.symbol, "from": previous, "to": dst.name}


def update_ticker(symbol: str, analyst_key: str | None = None, active: bool | None = None, fields: dict | None = None) -> None:
    with session_scope() as s:
        t = _find_ticker(s, symbol)
        if t is None:
            raise CoverageError(f"Unknown ticker {symbol}")
        if analyst_key:
            t.analyst_id = resolve_analyst(s, analyst_key).id
        if active is not None:
            t.active = active
        if fields:
            _apply_ticker_fields(t, {**fields, "symbol": t.symbol})


def analyst_info(name_or_key: str) -> dict:
    with session_scope() as s:
        a = resolve_analyst(s, name_or_key)
        return {"key": a.key, "name": a.name, "agent_type": a.agent_type, "color": a.color, "focus_notes": a.focus_notes,
                "active": a.active,
                "tickers": [{"symbol": t.symbol, "name": t.name, "type": t.ticker_type, "aliases": t.aliases or []}
                            for t in a.tickers if t.active]}


def list_coverage(include_inactive: bool = False) -> list[dict]:
    with session_scope() as s:
        out = []
        for a in s.scalars(select(Analyst).order_by(Analyst.id)):
            if not a.active and not include_inactive:
                continue
            out.append({
                "key": a.key, "name": a.name, "agent_type": a.agent_type, "color": a.color, "focus_notes": a.focus_notes,
                "active": a.active,
                "tickers": [{"symbol": t.symbol, "name": t.name, "type": t.ticker_type, "active": t.active, "aliases": t.aliases or []}
                            for t in a.tickers if t.active or include_inactive],
            })
        return out


def comps_basis_overrides() -> dict[str, str]:
    """Bucket -> multiple ('ev_to_revenue' | 'ev_to_ebitda') from coverage.yaml's comps_basis (industry convention)."""
    try:
        return (yaml.safe_load(COVERAGE_FILE.read_text(encoding="utf-8")) or {}).get("comps_basis") or {}
    except Exception:
        return {}
