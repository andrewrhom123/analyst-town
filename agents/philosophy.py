"""The pod's research philosophy and the PM's active research charter, injected into every agent prompt.

Not sell-side research: agents think like fundamental traders hunting market inefficiencies. Every view is a
trade with a narrative (business fundamentals, what the market is missing, data, catalyst, conviction) and a
structure that isolates the idea (long this / short that to strip out beta). The research charter is written
in strategy sessions and stays in force until the next one replaces it.
"""

from sqlalchemy import select

from database.db import session_scope
from database.models import ResearchCharter

RESEARCH_PHILOSOPHY = """How this pod thinks (always):
- You are a fundamental trader finding market inefficiencies, not a sell-side analyst. Never lead with a BUY/SELL/HOLD rating or a price target as the point of the view.
- Every view is a trade with a narrative: what the business actually does and earns (and its business-model trade-offs), what the market is NOT seeing and why it is mispriced now, the data that backs it, the catalyst that closes the gap, and how much real edge you have (conviction).
- Structure trades to isolate the idea: long this, short that to strip out beta, sector or factor risk. Name the hedge leg and why it is correlated.
- Value every company both ways, intrinsic (DCF) and relative (comps), through both public-market and private-market lenses (private rounds, secondaries, M&A and take-private multiples). The PM's core edge is relative value: who is over- vs under-priced against peers in the same bucket, on raw and growth-adjusted multiples. That is where the pairs come from.
- Consensus is the starting point, not the answer. If there is no edge, say "no trade"."""


def active_charter() -> dict | None:
    with session_scope() as s:
        c = s.scalar(select(ResearchCharter).where(ResearchCharter.active.is_(True)).order_by(ResearchCharter.created_at.desc()).limit(1))
        if c is None:
            return None
        return {"id": c.id, "title": c.title, "directives": c.directives or [], "charter_md": c.charter_md,
                "created_at": c.created_at.isoformat(), "session_id": c.session_id}


def framework_block() -> str:
    """Philosophy plus the PM's current charter, for system prompts."""
    charter = active_charter()
    if not charter:
        return RESEARCH_PHILOSOPHY
    directives = "\n".join(f"- {d}" for d in charter["directives"])
    return (f"{RESEARCH_PHILOSOPHY}\n\nThe PM's research charter, \"{charter['title']}\" (set in a strategy session; follow it "
            f"unless the data clearly argues otherwise, and then say so):\n{directives}")
