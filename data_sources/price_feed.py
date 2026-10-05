"""Real-time quotes from Finnhub (free tier: 60 calls/minute, US equities and ETFs).

Used by the price monitor every PRICE_CHECK_MINUTES during market hours. No Claude involved.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import delete, select

from database.db import session_scope
from database.models import PriceTick
from utils.config import FINNHUB_API_KEY

logger = logging.getLogger(__name__)

QUOTE_URL = "https://finnhub.io/api/v1/quote"
TICK_RETENTION_DAYS = 90


class PriceFeedError(Exception):
    pass


_throttle = threading.Lock()
_last_call = 0.0
MIN_INTERVAL = 1.05  # stay under Finnhub's free 60 calls/minute across all threads


def _get(url: str, params: dict) -> requests.Response:
    global _last_call
    if not FINNHUB_API_KEY:
        raise PriceFeedError("FINNHUB_API_KEY is not set")
    for attempt in range(2):
        with _throttle:
            wait = MIN_INTERVAL - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        resp = requests.get(url, params={**params, "token": FINNHUB_API_KEY}, timeout=15)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        time.sleep(5)
    raise PriceFeedError("Finnhub rate limit hit")


def fetch_quote(symbol: str) -> dict:
    q = _get(QUOTE_URL, {"symbol": symbol}).json()
    if not q or not q.get("c"):  # Finnhub returns zeros for unknown symbols
        raise PriceFeedError(f"No quote for {symbol}")
    return {
        "symbol": symbol,
        "price": float(q["c"]),
        "change_pct": float(q["dp"]) if q.get("dp") is not None else None,
        "day_high": q.get("h"),
        "day_low": q.get("l"),
        "prev_close": q.get("pc"),
        "quote_time": datetime.fromtimestamp(q["t"], tz=timezone.utc).isoformat() if q.get("t") else None,
    }


def poll_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch and store a tick for each symbol. Failures are logged and skipped."""
    out = {}
    for sym in symbols:
        try:
            q = fetch_quote(sym)
        except Exception as exc:
            logger.warning("Quote for %s failed: %s", sym, exc)
            continue
        out[sym] = q
    if out:
        with session_scope() as s:
            for q in out.values():
                s.add(PriceTick(symbol=q["symbol"], price=q["price"], change_pct=q["change_pct"],
                                day_high=q["day_high"], day_low=q["day_low"], prev_close=q["prev_close"]))
            s.execute(delete(PriceTick).where(PriceTick.ts < datetime.now(timezone.utc) - timedelta(days=TICK_RETENTION_DAYS)))
    return out


def latest_tick(symbol: str) -> dict | None:
    with session_scope() as s:
        t = s.scalar(select(PriceTick).where(PriceTick.symbol == symbol).order_by(PriceTick.ts.desc()).limit(1))
        if t is None:
            return None
        return {"symbol": symbol, "price": t.price, "change_pct": t.change_pct, "day_high": t.day_high,
                "day_low": t.day_low, "prev_close": t.prev_close, "ts": t.ts.isoformat()}


METRIC_URL = "https://finnhub.io/api/v1/stock/metric"
PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"


def _finnhub(url: str, params: dict) -> dict:
    return _get(url, params).json() or {}


def get_52_week_range(symbol: str) -> dict:
    """52-week high/low from Finnhub basic financials (free), cached a day."""
    from data_sources.cache import cached_fetch

    def fetch():
        m = _finnhub(METRIC_URL, {"symbol": symbol, "metric": "all"}).get("metric") or {}
        return {"week_52_high": m.get("52WeekHigh"), "week_52_low": m.get("52WeekLow"),
                "week_52_high_date": m.get("52WeekHighDate"), "week_52_low_date": m.get("52WeekLowDate")}

    try:
        return cached_fetch("finnhub_52w", symbol, 24 * 3600, fetch)
    except Exception as exc:
        logger.info("52-week range for %s unavailable: %s", symbol, exc)
        return {"week_52_high": None, "week_52_low": None}


def company_profile(symbol: str) -> dict | None:
    """Name / exchange / industry for a symbol (used when /add gets only a ticker). None if unknown."""
    try:
        p = _finnhub(PROFILE_URL, {"symbol": symbol})
    except Exception:
        return None
    return p if p.get("name") else None


def chart_series(symbol: str, days: int = 30) -> list[dict]:
    """Daily closes for the chart: Alpha Vantage daily history (cached a day, fetched on demand within the
    free-tier budget) merged with the closes the price monitor records itself (which take over over time)."""
    from data_sources.market_data import get_daily_history

    series = {row["date"]: row["close"] for row in get_daily_history(symbol)}
    for row in price_history(symbol, days + 5):
        series[row["date"]] = row["price"]
    dates = sorted(series)[-days:]
    return [{"date": d, "close": round(series[d], 4)} for d in dates]


def price_history(symbol: str, days: int = 10) -> list[dict]:
    """Closing-ish snapshot per day (last tick of each day) plus today's ticks, oldest first."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as s:
        ticks = s.scalars(select(PriceTick).where(PriceTick.symbol == symbol, PriceTick.ts >= since).order_by(PriceTick.ts)).all()
        by_day = {}
        for t in ticks:
            ts = t.ts if t.ts.tzinfo else t.ts.replace(tzinfo=timezone.utc)
            by_day[ts.date().isoformat()] = {"date": ts.date().isoformat(), "price": t.price, "change_pct": t.change_pct}
        return list(by_day.values())
