"""News from NewsAPI (/v2/everything), last 30 days, with lightweight sentiment scoring.

Free developer tier: 100 requests/day, articles up to one month old. Cached for NEWS_CACHE_TTL (1 day).
Sentiment is a finance-tuned word list - cheap and deterministic. Claude reads the headlines anyway,
so this is a quick signal for the dashboard rather than the analysis itself.
"""

import logging
import re
from datetime import date, timedelta

import requests

from data_sources.cache import cached_fetch
from utils.config import NEWS_CACHE_TTL, NEWSAPI_KEY

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"

POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies", "gain", "gains",
    "record", "growth", "grows", "upgrade", "upgraded", "outperform", "strong", "stronger", "raise", "raises",
    "raised", "profit", "profitable", "expands", "expansion", "wins", "win", "partnership", "launch", "launches",
    "bullish", "boost", "boosts", "accelerate", "accelerates", "approval", "approved", "rebound", "tops",
}
NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls", "slump", "slumps", "decline",
    "declines", "downgrade", "downgraded", "underperform", "weak", "weaker", "cut", "cuts", "loss", "losses",
    "lawsuit", "sued", "probe", "investigation", "layoffs", "warning", "warns", "risk", "risks", "fraud",
    "bearish", "selloff", "sell-off", "slowdown", "recession", "default", "fine", "fined", "hack", "breach",
    "lowers", "lowered", "tumble", "tumbles", "crash", "volatile",
}
_WORD = re.compile(r"[a-z][a-z\-]+")


def score_sentiment(text: str) -> tuple[float, str]:
    words = _WORD.findall(text.lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    if pos == neg:
        return 0.0, "neutral"
    score = round((pos - neg) / (pos + neg), 2)
    return score, "positive" if score > 0 else "negative"


def _relevance(article: dict, keywords: list[str]) -> int:
    title = (article.get("title") or "").lower()
    body = (article.get("description") or "").lower()
    return sum(3 * title.count(k) + body.count(k) for k in keywords)


def _fetch(query: str, keywords: list[str], limit: int) -> dict:
    if not NEWSAPI_KEY:
        raise RuntimeError("NEWSAPI_KEY is not set")
    resp = requests.get(
        NEWSAPI_URL,
        params={
            "q": query,
            "from": (date.today() - timedelta(days=30)).isoformat(),
            "language": "en",
            "sortBy": "relevancy",
            "searchIn": "title,description",
            "pageSize": 50,
        },
        headers={"X-Api-Key": NEWSAPI_KEY},
        timeout=30,
    )
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('code')} {data.get('message')}")

    seen, articles = set(), []
    for a in data.get("articles", []):
        title = (a.get("title") or "").strip()
        if not title or title == "[Removed]" or title.lower() in seen:
            continue
        seen.add(title.lower())
        score, label = score_sentiment(f"{title}. {a.get('description') or ''}")
        articles.append({
            "title": title,
            "description": a.get("description"),
            "source": (a.get("source") or {}).get("name"),
            "url": a.get("url"),
            "published_at": a.get("publishedAt"),
            "sentiment_score": score,
            "sentiment": label,
            "_relevance": _relevance(a, keywords),
        })

    articles.sort(key=lambda a: (a["_relevance"], a["published_at"] or ""), reverse=True)
    top = articles[:limit]
    for a in top:
        a.pop("_relevance")

    counts = {label: sum(a["sentiment"] == label for a in top) for label in ("positive", "negative", "neutral")}
    avg = round(sum(a["sentiment_score"] for a in top) / len(top), 2) if top else 0.0
    return {
        "available": True,
        "query": query,
        "total_results": data.get("totalResults", 0),
        "articles": top,
        "sentiment_summary": {"average_score": avg, **counts},
    }


def get_news(cache_key: str, query: str, keywords: list[str], limit: int = 10) -> dict:
    """Top relevant articles for an agent. Never raises."""
    try:
        return cached_fetch("news", cache_key, NEWS_CACHE_TTL, lambda: _fetch(query, [k.lower() for k in keywords], limit))
    except Exception as exc:
        logger.warning("News unavailable for %s: %s", cache_key, exc)
        return {"available": False, "query": query, "error": str(exc), "articles": []}
