"""The pod's research philosophy and the PM's active research charter, injected into every agent prompt.

Not sell-side research: agents think like fundamental traders hunting market inefficiencies. Every view is a
trade with a narrative (business fundamentals, what the market is missing, data, catalyst, conviction) and a
structure that isolates the idea (long this / short that to strip out beta). The research charter is written
in strategy sessions and stays in force until the next one replaces it.
"""

import re
import time

from sqlalchemy import select

from database.db import session_scope
from database.models import ResearchCharter

RESEARCH_PHILOSOPHY = """How this pod thinks (always):
- You are a fundamental trader finding market inefficiencies, not a sell-side analyst. Never lead with a BUY/SELL/HOLD rating or a price target as the point of the view.
- Every view is a trade with a narrative: what the business actually does and earns (and its business-model trade-offs), what the market is NOT seeing and why it is mispriced now, the data that backs it, the catalyst that closes the gap, and how much real edge you have (conviction).
- Structure trades to isolate the idea: long this, short that to strip out beta, sector or factor risk. Name the hedge leg and why it is correlated.
- Value every company both ways, intrinsic (DCF) and relative (comps), through both public-market and private-market lenses (private rounds, secondaries, M&A and take-private multiples). The PM's core edge is relative value: who is over- vs under-priced against peers in the same bucket, on raw and growth-adjusted multiples. That is where the pairs come from.
- Consensus is the starting point, not the answer. If there is no edge, say "no trade"."""


NAMING_RULE = """How to refer to companies (always, in speech and in writing):
- Use the company's name, not its ticker: "PubMatic", "DraftKings", "The Trade Desk", "Robinhood", never "PUBM", "DKNG", "TTD", "HOOD" in sentences.
- In written materials (memos, notes, theses) you may add the ticker in parentheses once, at the first mention: "DraftKings (DKNG)". Never in anything spoken.
- Tickers stay only where a field explicitly asks for a ticker symbol, and in tables. Use the directory below to translate the tickers in your data."""

_SUFFIX = re.compile(r",?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Ltd\.?|Limited|plc|PLC|N\.?V\.?|S\.?A\.?|SE|AG|Holdings? Inc\.?|Class [A-C])$")
_directory: dict = {"at": 0.0, "text": ""}


def short_name(name: str | None) -> str | None:
    """'Robinhood Markets Inc' -> 'Robinhood Markets'; 'PubMatic, Inc.' -> 'PubMatic'."""
    if not name:
        return None
    name = name.strip()
    for _ in range(2):
        name = _SUFFIX.sub("", name).strip().rstrip(",")
    return name


def name_directory() -> str:
    """'TICKER = Company' for covered names and every comps peer (cached 10 minutes)."""
    if time.time() - _directory["at"] < 600 and _directory["text"]:
        return _directory["text"]
    from agents.registry import active_contexts
    from data_sources.cache import get_cached

    names: dict[str, str] = {}
    for c in active_contexts():
        names[c.symbol] = c.name
        for peers in (c.comps_buckets or {}).values():
            for sym in peers:
                if sym not in names:
                    n = short_name((get_cached("market_overview", sym, None) or {}).get("name"))
                    if n:
                        names[sym] = n
    _directory.update(at=time.time(), text="; ".join(f"{k} = {v}" for k, v in sorted(names.items())))
    return _directory["text"]


def active_charter() -> dict | None:
    with session_scope() as s:
        c = s.scalar(select(ResearchCharter).where(ResearchCharter.active.is_(True)).order_by(ResearchCharter.created_at.desc()).limit(1))
        if c is None:
            return None
        return {"id": c.id, "title": c.title, "directives": c.directives or [], "charter_md": c.charter_md,
                "created_at": c.created_at.isoformat(), "session_id": c.session_id}


def framework_block() -> str:
    """Philosophy, naming rule (with the ticker -> name directory) and the PM's current charter, for system prompts."""
    try:
        directory = name_directory()
    except Exception:  # never let a lookup problem break a prompt
        directory = ""
    base = f"{RESEARCH_PHILOSOPHY}\n\n{NAMING_RULE}" + (f"\nTicker directory: {directory}" if directory else "")
    charter = active_charter()
    if not charter:
        return base
    directives = "\n".join(f"- {d}" for d in charter["directives"])
    return (f"{base}\n\nThe PM's research charter, \"{charter['title']}\" (set in a strategy session; follow it "
            f"unless the data clearly argues otherwise, and then say so):\n{directives}")
