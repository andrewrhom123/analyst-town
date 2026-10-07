"""Market data from Alpha Vantage.

Free tier is ~25 requests/day, so calls are cached aggressively:
  quotes (price, change %, volume)          -> MARKET_CACHE_TTL (1 hour)
  company overview (market cap, P/E, 52w)   -> 24 hours (fundamentals rarely change intraday)
  economic indicators (rates, CPI, jobs)    -> MACRO_CACHE_TTL (24 hours)
"""

import logging
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from data_sources.cache import cached_fetch, get_cached, set_cached
from utils.config import (
    ALPHAVANTAGE_API_KEY,
    ALPHAVANTAGE_PEER_BUDGET,
    MACRO_CACHE_TTL,
    MARKET_CACHE_TTL,
    PEER_CACHE_TTL,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
OVERVIEW_CACHE_TTL = 24 * 3600
MIN_INTERVAL_SECONDS = 1.5

_lock = threading.Lock()
_last_request = 0.0


class AlphaVantageError(Exception):
    pass


def _usage_key() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def calls_today() -> int:
    usage = get_cached("av_usage", _usage_key(), None)
    return usage["count"] if usage else 0


def _record_call() -> None:
    set_cached("av_usage", _usage_key(), {"count": calls_today() + 1})


def _call(params: dict) -> dict:
    global _last_request
    if not ALPHAVANTAGE_API_KEY:
        raise AlphaVantageError("ALPHAVANTAGE_API_KEY is not set")
    for attempt in range(2):
        with _lock:  # serialize calls across agent threads to respect the burst limit
            wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            resp = requests.get(BASE_URL, params={**params, "apikey": ALPHAVANTAGE_API_KEY}, timeout=30)
            _last_request = time.monotonic()
            _record_call()
        resp.raise_for_status()
        data = resp.json()
        # Alpha Vantage signals errors/limits with HTTP 200 and a message key.
        message = data.get("Note") or data.get("Information") or data.get("Error Message")
        if not message:
            return data
        if "Error Message" in data or "day" in message.lower() or attempt == 1:
            raise AlphaVantageError(message)
        logger.info("Alpha Vantage burst limit hit; retrying in 20s")
        time.sleep(20)
    raise AlphaVantageError("unreachable")


def _num(value) -> float | None:
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def get_quote(symbol: str) -> dict:
    """Real-time quote from Finnhub (free, 60/min)."""
    from data_sources.price_feed import fetch_quote

    def fetch():
        q = fetch_quote(symbol)
        return {
            "symbol": symbol,
            "price": q["price"],
            "change_percent": q["change_pct"],
            "day_high": q["day_high"],
            "day_low": q["day_low"],
            "previous_close": q["prev_close"],
            "quote_time": q["quote_time"],
        }

    return cached_fetch("market_quote", symbol, MARKET_CACHE_TTL, fetch)


def _fetch_overview_finnhub(symbol: str) -> dict:
    """Fundamentals from Finnhub basic financials + profile (free tier). Same keys as the Alpha Vantage version."""
    from data_sources.price_feed import METRIC_URL, PROFILE_URL, _finnhub

    m = _finnhub(METRIC_URL, {"symbol": symbol, "metric": "all"}).get("metric") or {}
    if not m:
        raise AlphaVantageError(f"No Finnhub fundamentals for {symbol}")
    p = _finnhub(PROFILE_URL, {"symbol": symbol})
    mm = 1e6  # Finnhub reports market cap / EV in millions
    shares = p.get("shareOutstanding")
    rev_ps = m.get("revenuePerShareTTM")
    return {
        "as_of": date.today().isoformat(),
        "source": "finnhub",
        "name": p.get("name"),
        "sector": p.get("finnhubIndustry"),
        "industry": p.get("finnhubIndustry"),
        "market_cap": m["marketCapitalization"] * mm if m.get("marketCapitalization") else None,
        "enterprise_value": m["enterpriseValue"] * mm if m.get("enterpriseValue") else None,
        "pe_ratio": m.get("peTTM"),
        "price_to_sales_ttm": m.get("psTTM"),
        "ev_to_revenue": m.get("evRevenueTTM"),
        "ev_to_ebitda": m.get("evEbitdaTTM"),
        "ev_to_fcf": m.get("currentEv/freeCashFlowTTM"),
        "week_52_high": m.get("52WeekHigh"),
        "week_52_low": m.get("52WeekLow"),
        "revenue_ttm": rev_ps * shares * mm if rev_ps and shares else None,
        "revenue_growth_ttm_yoy_pct": m.get("revenueGrowthTTMYoy"),
        "quarterly_revenue_growth_yoy_pct": m.get("revenueGrowthQuarterlyYoy"),
        "gross_margin_ttm_pct": m.get("grossMarginTTM"),
        "operating_margin_ttm_pct": m.get("operatingMarginTTM"),
        "net_margin_ttm_pct": m.get("netProfitMarginTTM"),
        "eps_ttm": m.get("epsTTM"),
        "beta": m.get("beta"),
        "shares_outstanding": shares * mm if shares else None,
    }


def _fetch_overview(symbol: str) -> dict:
    """Finnhub first (no meaningful daily cap); Alpha Vantage OVERVIEW as a fallback."""
    try:
        return _fetch_overview_finnhub(symbol)
    except Exception as exc:
        logger.info("Finnhub fundamentals for %s unavailable (%s); trying Alpha Vantage", symbol, exc)
    return _fetch_overview_alphavantage(symbol)


def _fetch_overview_alphavantage(symbol: str) -> dict:
    o = _call({"function": "OVERVIEW", "symbol": symbol})
    if not o.get("Symbol"):
        raise AlphaVantageError(f"No overview returned for {symbol}")
    return {
        "as_of": date.today().isoformat(),
        "name": o.get("Name"),
        "sector": o.get("Sector"),
        "industry": o.get("Industry"),
        "market_cap": _num(o.get("MarketCapitalization")),
        "pe_ratio": _num(o.get("PERatio")),
        "forward_pe": _num(o.get("ForwardPE")),
        "price_to_sales_ttm": _num(o.get("PriceToSalesRatioTTM")),
        "ev_to_revenue": _num(o.get("EVToRevenue")),
        "ev_to_ebitda": _num(o.get("EVToEBITDA")),
        "week_52_high": _num(o.get("52WeekHigh")),
        "week_52_low": _num(o.get("52WeekLow")),
        "revenue_ttm": _num(o.get("RevenueTTM")),
        "quarterly_revenue_growth_yoy": _num(o.get("QuarterlyRevenueGrowthYOY")),
        "profit_margin": _num(o.get("ProfitMargin")),
        "operating_margin_ttm": _num(o.get("OperatingMarginTTM")),
        "eps": _num(o.get("EPS")),
        "beta": _num(o.get("Beta")),
        "shares_outstanding": _num(o.get("SharesOutstanding")),
        "analyst_target_price": _num(o.get("AnalystTargetPrice")),
    }


def get_overview(symbol: str) -> dict:
    return cached_fetch("market_overview", symbol, OVERVIEW_CACHE_TTL, lambda: _fetch_overview(symbol))


# --- Daily history for charts ------------------------------------------------------

CHART_BUDGET_CEILING = 22  # leave the last few free-tier calls of the day for deep dives


def get_daily_history(symbol: str) -> list[dict]:
    """~100 daily closes (TIME_SERIES_DAILY compact), cached a day. Fetched only when a chart is opened and
    only while today's Alpha Vantage usage leaves room; otherwise serves whatever is cached (or nothing)."""
    cached = get_cached("daily_history", symbol, OVERVIEW_CACHE_TTL)
    if cached is not None:
        return cached["rows"]
    if calls_today() < CHART_BUDGET_CEILING:
        try:
            data = _call({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact"})
            series = data.get("Time Series (Daily)") or {}
            rows = sorted(({"date": d, "close": _num(v.get("4. close"))} for d, v in series.items()), key=lambda r: r["date"])
            rows = [r for r in rows if r["close"] is not None]
            if rows:
                set_cached("daily_history", symbol, {"rows": rows})
                return rows
        except Exception as exc:
            logger.info("Daily history for %s unavailable: %s", symbol, exc)
    stale = get_cached("daily_history", symbol, None)
    return stale["rows"] if stale else []


# --- Peer multiples for comps -------------------------------------------------

def revenue_growth_pct(o: dict | None) -> float | None:
    """Revenue growth in percent: Finnhub TTM (already %), else Alpha Vantage quarterly YoY (a fraction)."""
    if not o:
        return None
    if o.get("revenue_growth_ttm_yoy_pct") is not None:
        return o["revenue_growth_ttm_yoy_pct"]
    q = o.get("quarterly_revenue_growth_yoy")
    return q * 100 if q is not None else None


def _peer_row(symbol: str, o: dict | None) -> dict:
    if not o:
        return {"symbol": symbol, "name": None, "market_cap_mm": None, "enterprise_value_mm": None,
                "ev_to_revenue": None, "ev_to_ebitda": None, "revenue_growth_pct": None, "gross_margin_pct": None, "as_of": None}
    ev_rev = o.get("ev_to_revenue")
    if o.get("enterprise_value"):
        ev = o["enterprise_value"] / 1e6
    else:
        ev = ev_rev * o["revenue_ttm"] / 1e6 if ev_rev and o.get("revenue_ttm") else None
    ev_ebitda = o.get("ev_to_ebitda")
    return {
        "symbol": symbol,
        "name": o.get("name"),
        "market_cap_mm": o["market_cap"] / 1e6 if o.get("market_cap") else None,
        "enterprise_value_mm": ev,
        "ev_to_revenue": ev_rev if ev_rev and ev_rev > 0 else None,
        "ev_to_ebitda": ev_ebitda if ev_ebitda and ev_ebitda > 0 else None,  # negative EBITDA -> not meaningful
        "revenue_growth_pct": revenue_growth_pct(o),  # for growth-adjusted relative value
        "gross_margin_pct": o.get("gross_margin_ttm_pct"),
        "as_of": o.get("as_of"),
    }


def get_peer_multiples(buckets: dict[str, list[str]]) -> dict[str, list[dict]]:
    """LTM EV/Revenue and EV/EBITDA per peer, grouped by valuation-logic bucket.

    Peer fundamentals come from Finnhub (free, 60 calls/min) and are cached for PEER_CACHE_TTL (7 days).
    If Finnhub has nothing for a symbol, the Alpha Vantage fallback only runs while today's usage is
    below ALPHAVANTAGE_PEER_BUDGET (free tier is 25 calls/day).
    """
    result = {}
    for bucket, symbols in buckets.items():
        rows = []
        for sym in symbols:
            overview = get_cached("market_overview", sym, PEER_CACHE_TTL)
            if overview is None:
                try:
                    overview = _fetch_overview_finnhub(sym)
                    set_cached("market_overview", sym, overview)
                except Exception as exc:
                    logger.info("Peer fundamentals for %s not on Finnhub: %s", sym, exc)
                    if calls_today() < ALPHAVANTAGE_PEER_BUDGET:
                        try:
                            overview = get_overview(sym)
                        except Exception as exc2:
                            logger.info("Peer overview for %s unavailable: %s", sym, exc2)
            if overview is None:
                overview = get_cached("market_overview", sym, None)  # stale beats nothing
            rows.append(_peer_row(sym, overview))
        result[bucket] = rows
    # LTM revenue / EBITDA growth and EBITDA margin from SEC XBRL (cached a week), plus LTM-based multiples
    from data_sources.fundamentals import peer_metrics, refresh_ltm
    try:
        refresh_ltm([r["symbol"] for rows in result.values() for r in rows])
    except Exception as exc:
        logger.warning("LTM refresh for comps failed: %s", exc)
    for rows in result.values():
        for r in rows:
            m = peer_metrics(r["symbol"])
            r.update({k: m[k] for k in ("ev_to_revenue", "ev_to_ebitda", "revenue_growth_pct", "ebitda_growth_pct",
                                        "ebitda_margin_pct", "period") if m.get(k) is not None})
    return result


def get_market_data(symbol: str, include_overview: bool = True) -> dict:
    """Quote + fundamentals for one symbol. Never raises; partial data is returned with 'errors'."""
    result: dict = {"symbol": symbol, "available": False, "errors": []}
    try:
        result["quote"] = get_quote(symbol)
        result["available"] = True
    except Exception as exc:
        result["errors"].append(f"quote: {exc}")
    if include_overview:
        try:
            result["overview"] = get_overview(symbol)
            result["available"] = True
        except Exception as exc:
            result["errors"].append(f"overview: {exc}")
    return result


# --- Macro indicators -------------------------------------------------------

MACRO_SERIES = {
    "fed_funds_rate": {"function": "FEDERAL_FUNDS_RATE", "interval": "monthly"},
    "treasury_10y": {"function": "TREASURY_YIELD", "interval": "monthly", "maturity": "10year"},
    "treasury_2y": {"function": "TREASURY_YIELD", "interval": "monthly", "maturity": "2year"},
    "cpi": {"function": "CPI", "interval": "monthly"},
    "unemployment": {"function": "UNEMPLOYMENT"},
}


def get_macro_indicator(name: str, points: int = 13) -> dict:
    params = MACRO_SERIES[name]

    def fetch():
        data = _call(params)
        rows = data.get("data") or []
        return {
            "name": data.get("name", name),
            "unit": data.get("unit"),
            "series": [{"date": r["date"], "value": _num(r["value"])} for r in rows[:points]],
        }

    return cached_fetch("macro", name, MACRO_CACHE_TTL, fetch)


def get_macro_data() -> dict:
    result: dict = {"indicators": {}, "errors": []}
    for name in MACRO_SERIES:
        try:
            result["indicators"][name] = get_macro_indicator(name)
        except Exception as exc:
            result["errors"].append(f"{name}: {exc}")
    return result
