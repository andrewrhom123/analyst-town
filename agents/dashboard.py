"""Dashboard feed for the town page: news, cross-ticker themes and earnings dates.

Built only from data the pod already has (the news / SEC caches and current theses), so it costs no
API calls and no Claude spend. Themes are keyword clusters over headlines and theses, scored with the
headline sentiment the news fetcher already computes.
"""

import re
from datetime import date, datetime, timedelta

from agents.registry import get_context, list_coverage
from agents.thesis import current_thesis
from data_sources.cache import get_cached

NEWS_PER_TICKER = 8

# Category tags for news cards (first match wins).
CATEGORIES = [
    ("earnings", r"\b(earnings|quarter(ly)?|q[1-4]\b|results|revenue|eps|guidance|beats?|miss(es)?)\b"),
    ("regulatory", r"\b(sec|regulat\w*|lawsuit|probe|investigation|fine[ds]?|approval|approved|congress|bill|ban|compliance|antitrust)\b"),
    ("leadership", r"\b(ceo|cfo|founder|chief|executive|steps? down|appoint\w*|resign\w*|hires?)\b"),
    ("product", r"\b(launch\w*|unveil\w*|introduc\w*|rolls? out|release[sd]?|new product|partnership|partners)\b"),
]

# Cross-ticker themes: label + pattern matched against headlines and theses.
THEMES = [
    ("Tokenization push", r"\btokeni[sz]\w*|real[- ]world assets?|\brwa\b"),
    ("Stablecoin adoption", r"\bstablecoins?\b|\busdc\b|\busdt\b|genius act"),
    ("Crypto prices", r"\bbitcoin\b|\bbtc\b|\bether(eum)?\b|\bcrypto\b"),
    ("Fed & interest rates", r"\bfed\b|federal reserve|rate (cut|hike|pause)s?|interest rates?|\bfomc\b|powell"),
    ("Regulatory pressure", r"\bsec\b|regulat\w*|lawsuit|antitrust|probe|compliance"),
    ("Payments consolidation", r"\bacqui\w*|merger|\bm&a\b|takeover|buyout|consolidat\w*"),
    ("AI adoption", r"\bai\b|artificial intelligence|machine learning|\bllm\b|generative"),
    ("Consumer spending", r"consumer spending|retail sales|consumer demand|discretionary"),
    ("Ad market", r"\badvertis\w*|\bad spend|\bctv\b|programmatic"),
    ("IPO window", r"\bipo\b|listing|goes public|direct listing"),
]
_CATEGORY_RE = [(name, re.compile(p, re.I)) for name, p in CATEGORIES]
_THEME_RE = [(name, re.compile(p, re.I)) for name, p in THEMES]
_EARNINGS_CATALYST = re.compile(r"earnings|results|print|\bq[1-4]\b|quarter", re.I)
_PERIODIC_FORMS = ("10-Q", "10-K", "20-F", "40-F")


def _category(text: str) -> str:
    return next((name for name, rx in _CATEGORY_RE if rx.search(text)), "news")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _news(symbol: str) -> list[dict]:
    cached = get_cached("news", symbol, None) or {}
    out = []
    for a in cached.get("articles", [])[:NEWS_PER_TICKER]:
        text = f"{a.get('title') or ''}. {a.get('description') or ''}"
        out.append({
            "ticker": symbol, "title": a.get("title"), "description": a.get("description"), "source": a.get("source"),
            "url": a.get("url"), "published_at": a.get("published_at"), "sentiment": a.get("sentiment"),
            "sentiment_score": a.get("sentiment_score"), "category": _category(text),
        })
    return out


def _latest_quarter(sec: dict) -> dict | None:
    rows = ((sec.get("financials") or {}).get("quarterly")) or []
    if not rows:
        return None
    r = rows[-1]
    return {"period": r.get("period"), "revenue": r.get("revenue"), "revenue_growth_yoy_pct": r.get("revenue_growth_yoy_pct"),
            "operating_margin_pct": r.get("operating_margin_pct"), "net_income": r.get("net_income")}


def _earnings(ctx, thesis: dict) -> dict:
    sec = get_cached("sec", ctx.sec_ticker.upper(), None) if ctx.sec_ticker else None
    sec = sec if sec and sec.get("available") else {}
    last = None
    release = sec.get("earnings_release")
    if release:
        last = {"date": release.get("filing_date"), "form": release.get("form"), "url": release.get("url"), "kind": "earnings release"}
    else:
        for f in sec.get("recent_filings") or []:
            if (f["form"] == "8-K" and "2.02" in (f.get("items") or "")) or f["form"] in _PERIODIC_FORMS:
                last = {"date": f["filing_date"], "form": f["form"], "url": None, "kind": "filing"}
                break

    catalyst = next((c for c in thesis.get("next_catalysts") or []
                     if _EARNINGS_CATALYST.search(f"{c.get('event', '')} {c.get('timing', '')}")), None)
    estimate = None
    last_date = _parse_date(last["date"]) if last else None
    if last_date:
        est = last_date + timedelta(days=91)
        while est < date.today():
            est += timedelta(days=91)
        estimate = est.isoformat()
    return {
        "last": last,
        "result": _latest_quarter(sec),
        "next": {"timing": catalyst.get("timing"), "event": catalyst.get("event"), "why": catalyst.get("why_it_matters")} if catalyst else None,
        "next_estimate": estimate,  # ~a quarter after the last release; labeled as an estimate in the UI
    }


def _themes(news: list[dict], theses: dict[str, dict]) -> list[dict]:
    themes = []
    for name, rx in _THEME_RE:
        tickers, scores, headlines = set(), [], []
        for a in news:
            if rx.search(f"{a['title'] or ''} {a['description'] or ''}"):
                tickers.add(a["ticker"])
                if a.get("sentiment_score") is not None:
                    scores.append(a["sentiment_score"])
                if len(headlines) < 3:
                    headlines.append({"ticker": a["ticker"], "title": a["title"], "url": a["url"]})
        for symbol, t in theses.items():
            text = " ".join([t.get("thesis") or "", t.get("headline") or "", *(t.get("risk_factors") or [])])
            if rx.search(text):
                tickers.add(symbol)
        if len(tickers) < 1 or (not headlines and len(tickers) < 2):
            continue
        avg = sum(scores) / len(scores) if scores else 0.0
        tone = "bullish" if avg > 0.15 else "bearish" if avg < -0.15 else "mixed"
        themes.append({"name": name, "tone": tone, "score": round(avg, 2), "tickers": sorted(tickers),
                       "mentions": len(scores), "headlines": headlines})
    themes.sort(key=lambda t: (len(t["tickers"]), t["mentions"]), reverse=True)
    return themes[:8]


def dashboard() -> dict:
    news, earnings, theses = [], {}, {}
    for agent in list_coverage():
        for t in agent["tickers"]:
            ctx = get_context(t["symbol"])
            if ctx is None:
                continue
            th = current_thesis(ctx.ticker_id)
            thesis = th["thesis"] if th else {}
            theses[ctx.symbol] = thesis
            news += _news(ctx.symbol)
            earnings[ctx.symbol] = _earnings(ctx, thesis)
    news.sort(key=lambda a: a["published_at"] or "", reverse=True)
    return {"generated_at": datetime.utcnow().isoformat() + "Z", "news": news[:60], "themes": _themes(news, theses), "earnings": earnings}
