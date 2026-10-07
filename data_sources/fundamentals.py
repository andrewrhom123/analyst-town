"""LTM fundamentals for comps: revenue growth, EBITDA growth and EBITDA margin over the last four quarters.

Built from SEC XBRL company facts for any US filer (covered names and peers alike), using the standard LTM
method so it works even where only year-to-date figures are filed (cash-flow items like D&A):

    LTM = latest fiscal year + current year-to-date - prior-year same year-to-date

Growth compares the latest LTM with the LTM ending a year earlier. EBITDA follows the sell-side adjusted
convention: operating income + D&A + stock-based compensation. Results are cached a week per symbol
("ltm_fundamentals"); refresh_ltm() fills the cache in the background so the relative value board stays a
cache-only read. Names without US XBRL (foreign filers, OTC ADRs) fall back to the market-data feed's TTM
figures, labelled as such.
"""

import logging
from datetime import date, timedelta

from data_sources.cache import get_cached, set_cached
from data_sources.sec_fetcher import COMPANY_FACTS_URL, REVENUE_CONCEPTS, SECError, _get, lookup_cik

logger = logging.getLogger(__name__)

LTM_CACHE_TTL = 7 * 24 * 3600
OPERATING_INCOME = ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"]
D_AND_A = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization",
           "Depreciation", "DepreciationAndAmortisationExpense",
           "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"]
SBC = ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"]
# Brokers / financials often have no operating-income line: fall back to pre-tax income (labelled).
PRETAX_INCOME = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                 "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                 "ProfitLossBeforeTax"]


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _points(facts: dict, concepts: list[str]) -> tuple[str | None, list[dict]]:
    """(unit, [{start, end, val}]) for the concept with the most recent data; restatements keep the latest filing."""
    best_unit, best, best_end = None, [], None
    for taxonomy in ("us-gaap", "ifrs-full"):
        for concept in concepts:
            units = facts.get(taxonomy, {}).get(concept, {}).get("units", {})
            if not units:
                continue
            unit = "USD" if "USD" in units else next(iter(units))
            by_period = {}
            for r in sorted(units[unit], key=lambda r: r.get("filed", "")):
                if r.get("start") and r.get("end") and r.get("val") is not None:
                    by_period[(r["start"], r["end"])] = r["val"]
            pts = [{"start": _d(s), "end": _d(e), "val": v} for (s, e), v in by_period.items()]
            if not pts:
                continue
            latest = max(p["end"] for p in pts)
            if best_end is None or latest > best_end:
                best_unit, best, best_end = unit, pts, latest
    return best_unit, best


def _days(p: dict) -> int:
    return (p["end"] - p["start"]).days


def _near(a: date, b: date, tol: int) -> bool:
    return abs((a - b).days) <= tol


def ltm(points: list[dict], end: date) -> float | None:
    """Last-twelve-months value of a flow ending at `end`."""
    at_end = [p for p in points if _near(p["end"], end, 4)]
    full_year = [p for p in at_end if 350 <= _days(p) <= 380]
    if full_year:
        return full_year[0]["val"]
    partial = [p for p in at_end if _days(p) < 350]
    if not partial:
        return None
    ytd = max(partial, key=_days)  # the longest year-to-date span ending here
    fy = [p for p in points if 350 <= _days(p) <= 380 and p["end"] < end and (end - p["end"]).days < 370]
    prior = [p for p in points if _near(p["end"], end - timedelta(days=365), 10) and abs(_days(p) - _days(ytd)) <= 15]
    if not fy or not prior:
        return None
    return max(fy, key=lambda p: p["end"])["val"] + ytd["val"] - prior[0]["val"]


def _end_a_year_before(points: list[dict], end: date) -> date | None:
    ends = sorted({p["end"] for p in points if _near(p["end"], end - timedelta(days=365), 10)})
    return ends[0] if ends else None


def _growth(now: float | None, before: float | None) -> float | None:
    """YoY growth; n/m (None) when either period is zero or negative, as on sell-side comp sheets."""
    if now is None or before is None or before <= 0 or now <= 0:
        return None
    return (now / before - 1) * 100


def compute_ltm(facts: dict) -> dict:
    unit, rev = _points(facts, REVENUE_CONCEPTS)
    if not rev:
        return {"available": False, "error": "no revenue in XBRL"}
    end = max(p["end"] for p in rev)
    prior_end = _end_a_year_before(rev, end)
    _, oi = _points(facts, OPERATING_INCOME)
    profit_line = "operating income"
    if not oi or ltm(oi, end) is None:
        _, oi = _points(facts, PRETAX_INCOME)
        profit_line = "pre-tax income"
    _, da = _points(facts, D_AND_A)
    _, sbc = _points(facts, SBC)

    def ebitda(at: date | None) -> float | None:
        if at is None:
            return None
        o, d = ltm(oi, at), ltm(da, at)
        if o is None or d is None:
            return None
        s = ltm(sbc, at)
        return o + d + (s or 0)

    rev_now = ltm(rev, end)
    rev_before = ltm(rev, prior_end) if prior_end else None
    e_now, e_before = ebitda(end), ebitda(prior_end)
    annual_only = not any(_days(p) < 350 and (end - p["end"]).days < 730 for p in rev)  # e.g. 20-F filers
    margin = e_now / rev_now * 100 if e_now is not None and rev_now else None
    note = None
    if margin is not None and margin > 95:  # tags don't line up (e.g. a broker's revenue net of interest expense)
        e_now = e_before = margin = None
        note = "EBITDA not comparable: XBRL revenue/profit tags inconsistent"
    return {
        "available": rev_now is not None,
        "period_end": end.isoformat(),
        "basis": "FY" if annual_only else "LTM",
        "currency": unit,
        "revenue_ltm": rev_now,
        "revenue_growth_pct": _growth(rev_now, rev_before),
        "ebitda_ltm": e_now,
        "ebitda_growth_pct": _growth(e_now, e_before),
        "ebitda_margin_pct": margin,
        "note": note,
        "ebitda_definition": f"{profit_line} + D&A" + (" + stock comp" if ltm(sbc, end) is not None else ""),
        "source": "SEC XBRL",
    }


def fetch_ltm(symbol: str) -> dict:
    try:
        cik, _ = lookup_cik(symbol)
        facts = _get(COMPANY_FACTS_URL.format(cik=cik)).json().get("facts", {})
    except (SECError, Exception) as exc:  # not an SEC filer, no XBRL yet, network
        return {"available": False, "error": str(exc)[:200]}
    try:
        return compute_ltm(facts)
    except Exception as exc:
        logger.warning("LTM computation failed for %s: %s", symbol, exc)
        return {"available": False, "error": f"computation: {exc}"[:200]}


def get_ltm(symbol: str) -> dict | None:
    """Cached LTM fundamentals (never fetches)."""
    return get_cached("ltm_fundamentals", symbol, None)


def refresh_overviews(symbols: list[str], max_age: int = LTM_CACHE_TTL) -> int:
    """Finnhub fundamentals (enterprise value, TTM ratios) for names missing EV or older than max_age."""
    from data_sources.market_data import _fetch_overview_finnhub

    fetched = 0
    for sym in dict.fromkeys(s.upper() for s in symbols):
        fresh = get_cached("market_overview", sym, max_age)
        if fresh and fresh.get("enterprise_value"):
            continue
        try:
            set_cached("market_overview", sym, _fetch_overview_finnhub(sym))
            fetched += 1
        except Exception as exc:  # not on Finnhub's free tier, or no key: keep whatever is cached
            logger.info("Finnhub fundamentals for %s unavailable: %s", sym, exc)
    return fetched


def refresh_ltm(symbols: list[str], max_age: int = LTM_CACHE_TTL) -> int:
    """Fetch LTM fundamentals for symbols whose cache is missing or older than max_age. Returns how many were fetched."""
    fetched = 0
    for sym in dict.fromkeys(s.upper() for s in symbols):
        if get_cached("ltm_fundamentals", sym, max_age) is not None:
            continue
        set_cached("ltm_fundamentals", sym, fetch_ltm(sym))
        fetched += 1
    if fetched:
        logger.info("LTM fundamentals refreshed for %d symbols", fetched)
    return fetched


def comps_universe() -> list[str]:
    """Every covered ticker and every comps peer."""
    from agents.registry import active_contexts

    syms = []
    for c in active_contexts():
        if c.ticker_type == "public":
            syms.append(c.symbol)
        for peers in (c.comps_buckets or {}).values():
            syms += peers
    return list(dict.fromkeys(syms))


LIVE_EV_MAX_MOVE = 0.6  # a repriced EV this far from the cached one means mismatched inputs: keep the cached EV


def mark_ev_to_market(symbol: str, ev: float | None, overview: dict) -> tuple[float | None, float | None, str | None, bool]:
    """(ev, price, priced_at, live). Moves the cached EV with the share price since it was cached:
    EV now = cached EV - cached market cap + latest price x shares (debt and cash stay as last reported)."""
    from data_sources.price_feed import latest_tick

    tick = latest_tick(symbol)
    shares, cap = overview.get("shares_outstanding"), overview.get("market_cap")
    if not (ev and tick and tick.get("price") and shares and cap):
        return ev, tick.get("price") if tick else None, overview.get("as_of"), False
    live = ev - cap + tick["price"] * shares
    if live <= 0 or abs(live / ev - 1) > LIVE_EV_MAX_MOVE:
        return ev, tick["price"], overview.get("as_of"), False
    ts = tick["ts"] if ("+" in tick["ts"][10:] or tick["ts"].endswith("Z")) else tick["ts"] + "+00:00"  # SQLite drops tz; ticks are UTC
    return live, tick["price"], ts, True


def peer_metrics(symbol: str) -> dict:
    """Comps row for one name from the caches: LTM multiples (current EV / LTM revenue or EBITDA), LTM revenue and
    EBITDA growth, LTM EBITDA margin. Falls back to the market feed's TTM ratios where there is no US XBRL."""
    o = get_cached("market_overview", symbol, None) or {}
    l = get_ltm(symbol) or {}
    ev = o.get("enterprise_value")
    if not ev and (o.get("ev_to_revenue") or 0) > 0 and o.get("revenue_ttm"):
        ev = o["ev_to_revenue"] * o["revenue_ttm"]  # Alpha Vantage gives the ratio, not EV itself
    ev, price, priced_at, live = mark_ev_to_market(symbol, ev, o)
    usd = bool(l.get("available")) and l.get("currency") == "USD"
    rev = l.get("revenue_ltm") if usd else None
    ebitda = l.get("ebitda_ltm") if usd else None
    feed_rev = o.get("ev_to_revenue") if (o.get("ev_to_revenue") or 0) > 0 else None
    feed_ebitda = o.get("ev_to_ebitda") if (o.get("ev_to_ebitda") or 0) > 0 else None
    if l.get("available"):
        period = f"{l['basis']} to {l['period_end']}"
        growth = l.get("revenue_growth_pct")
    else:
        period = "TTM (market feed)" if o else None
        from data_sources.market_data import revenue_growth_pct
        growth = revenue_growth_pct(o)
    ev_rev = ev / rev if ev and rev and rev > 0 else None
    ev_ebitda = (ev / ebitda if ebitda > 0 else None) if ev and ebitda is not None else None
    feed = []
    if ev_rev is None and feed_rev:
        ev_rev = feed_rev
        feed.append("ev_to_revenue")
    if ev_ebitda is None and ebitda is None and l.get("ebitda_margin_pct") is None and feed_ebitda:
        ev_ebitda = feed_ebitda
        feed.append("ev_to_ebitda")
    return {
        "symbol": symbol, "name": o.get("name"), "as_of": o.get("as_of"), "period": period,
        "ev_to_revenue": ev_rev,
        "ev_to_ebitda": ev_ebitda,
        "feed_multiples": feed,  # multiples taken from the market feed's TTM ratios, not LTM filings
        "price": price, "priced_at": priced_at, "ev_live": live,
        "revenue_growth_pct": growth,
        "ebitda_growth_pct": l.get("ebitda_growth_pct"),
        "ebitda_margin_pct": l.get("ebitda_margin_pct"),
        "ebitda_definition": l.get("ebitda_definition"),
    }
