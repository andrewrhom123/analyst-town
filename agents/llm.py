"""Claude client, per-call cost ledger and the daily budget governor.

Models: deep dives use DEEP_MODEL (Claude Opus 5.5); thesis updates, meetings and /ask use LIGHT_MODEL
(Claude Sonnet 5.5). Every response is priced from its usage and written to cost_entries.

Budget rules (DAILY_BUDGET_USD, default $5, US/Eastern day):
  * autonomous work (deep dives, thesis updates) must leave MEETING_RESERVE_USD unspent until the daily
    meeting has run, plus ASK_RESERVE_USD for your questions; otherwise it is deferred to tomorrow
  * the meeting may use the budget minus ASK_RESERVE_USD
  * /ask may use everything up to the budget
"""

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy import func, select

from database.db import session_scope
from database.models import CostEntry, Meeting
from utils import config

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if a safety classifier declines, the API reruns the request on a
# recommended fallback model inside the same call instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EASTERN = ZoneInfo("America/New_York")

# $ per million tokens: (input, output, cache read). Cache writes (5-minute TTL) bill at 1.25x input.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
DEFAULT_PRICE = PRICING["claude-opus-5-5"]  # unknown model (e.g. a fallback): price conservatively


class AnalystError(Exception):
    pass


class BudgetExceeded(Exception):
    pass


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None, max_retries=3)
    return _client


def check_stop(message) -> None:
    if message.stop_reason == "refusal":
        details = message.stop_details
        raise AnalystError(f"Claude declined the request ({getattr(details, 'category', None)}): {getattr(details, 'explanation', '')}")
    if message.stop_reason == "max_tokens":
        raise AnalystError("Claude hit max_tokens before finishing")


# --- Cost ledger ---------------------------------------------------------------------

def price_usage(model: str, usage) -> float:
    rate_in, rate_out, rate_cache = PRICING.get(model, DEFAULT_PRICE)
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        (usage.input_tokens or 0) * rate_in
        + cache_write * rate_in * 1.25
        + cache_read * rate_cache
        + (usage.output_tokens or 0) * rate_out
    ) / 1_000_000


# --- One-time build-out pool ---------------------------------------------------------------
# Initial-coverage deep dives are paid from a separate one-time pool (BUILDOUT_BUDGET_USD) instead of
# the daily budget. Their cost rows are tagged "buildout_<kind>" and left out of spent_today().
BUILDOUT_PREFIX = "buildout_"
_buildout_tickers: set[str] = set()
_buildout_lock = threading.Lock()


@contextmanager
def buildout_scope(ticker: str):
    """Charge Claude calls for this ticker to the build-out pool while inside the block."""
    with _buildout_lock:
        _buildout_tickers.add(ticker)
    try:
        yield
    finally:
        with _buildout_lock:
            _buildout_tickers.discard(ticker)


def buildout_spent() -> float:
    """Total ever charged to the build-out pool (it is one-time, not daily)."""
    with session_scope() as s:
        return float(s.scalar(select(func.coalesce(func.sum(CostEntry.usd), 0.0)).where(CostEntry.kind.like(f"{BUILDOUT_PREFIX}%"))))


def record_cost(kind: str, message, ticker: str | None = None) -> float:
    usage = message.usage
    usd = price_usage(message.model, usage)
    with _buildout_lock:
        if ticker in _buildout_tickers:
            kind = BUILDOUT_PREFIX + kind
    with session_scope() as s:
        s.add(CostEntry(
            kind=kind, ticker=ticker, model=message.model,
            input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            usd=usd,
        ))
    return usd


def _day_start_utc() -> datetime:
    today = datetime.now(EASTERN).date()
    return datetime.combine(today, time(0, 0), tzinfo=EASTERN).astimezone(timezone.utc)


def spent_today() -> float:
    with session_scope() as s:
        return float(s.scalar(select(func.coalesce(func.sum(CostEntry.usd), 0.0))
                              .where(CostEntry.ts >= _day_start_utc(), CostEntry.kind.not_like(f"{BUILDOUT_PREFIX}%"))))


def spend_breakdown_today() -> dict:
    with session_scope() as s:
        rows = s.execute(
            select(CostEntry.kind, func.count(), func.sum(CostEntry.usd)).where(CostEntry.ts >= _day_start_utc()).group_by(CostEntry.kind)
        ).all()
    return {kind: {"calls": n, "usd": round(usd or 0, 4)} for kind, n, usd in rows}


def meeting_done_today() -> bool:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(Meeting).where(
            Meeting.started_at >= _day_start_utc(), Meeting.status == "done")) > 0


def budget_status() -> dict:
    spent = spent_today()
    return {
        "daily_budget_usd": config.DAILY_BUDGET_USD,
        "spent_today_usd": round(spent, 4),
        "remaining_usd": round(config.DAILY_BUDGET_USD - spent, 4),
        "meeting_done_today": meeting_done_today(),
        "breakdown": spend_breakdown_today(),
        "buildout": {"budget_usd": config.BUILDOUT_BUDGET_USD, "spent_usd": round(buildout_spent(), 4)},
    }


def can_spend(kind: str, estimate_usd: float) -> tuple[bool, str]:
    """Whether a call of this kind and estimated cost fits today's budget."""
    spent = spent_today()
    budget = config.DAILY_BUDGET_USD
    if kind == "ask":
        limit = budget
    elif kind == "meeting":
        limit = budget - config.ASK_RESERVE_USD
    else:
        reserve = config.ASK_RESERVE_USD + (0 if meeting_done_today() else config.MEETING_RESERVE_USD)
        limit = budget - reserve
    if spent + estimate_usd > limit:
        return False, f"daily budget: spent ${spent:.2f}, estimate ${estimate_usd:.2f}, limit for {kind} ${limit:.2f}"
    return True, ""


def require_budget(kind: str, estimate_usd: float) -> None:
    ok, why = can_spend(kind, estimate_usd)
    if not ok:
        raise BudgetExceeded(why)


# --- Convenience wrapper for single structured calls (light model) -----------------------

def parse_call(kind: str, output_format, system: str, messages: list, *, ticker: str | None = None,
               model: str | None = None, effort: str | None = None, max_tokens: int = 16000):
    """One structured-output call with refusal fallback. Returns (parsed, message)."""
    model = model or config.LIGHT_MODEL
    message = get_client().beta.messages.parse(
        model=model,
        max_tokens=max_tokens,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": effort or config.LIGHT_EFFORT},
        system=system,
        messages=messages,
        output_format=output_format,
    )
    record_cost(kind, message, ticker)
    check_stop(message)
    if message.parsed_output is None:
        raise AnalystError(f"{kind}: no parseable output")
    return message.parsed_output, message


def text_call(kind: str, system: str, messages: list, *, ticker: str | None = None, model: str | None = None,
              effort: str | None = None, max_tokens: int = 16000) -> str:
    model = model or config.LIGHT_MODEL
    message = get_client().beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": effort or config.LIGHT_EFFORT},
        system=system,
        messages=messages,
    )
    record_cost(kind, message, ticker)
    check_stop(message)
    return "".join(b.text for b in message.content if b.type == "text").strip()
