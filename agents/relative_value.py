"""Pod-wide relative value board: who is over- vs under-priced against peers in the same bucket.

Every comps bucket any covered ticker uses, with every name in it (covered names and their peers), scored on
LTM EV/revenue vs the bucket median and vs a growth-adjusted fair multiple (EV/revenue regressed on revenue
growth across the bucket), ranked rich to cheap, with the long-cheap / short-rich pair it implies. Built from
the market-data cache only (no API calls), so it is free to load; the per-ticker models add the forward,
model-based view and private-market marks.
"""

from agents.financial_model import score_bucket
from agents.registry import active_contexts
from agents.thesis import current_thesis
from data_sources.cache import get_cached
from data_sources.market_data import revenue_growth_pct


def _name(symbol: str) -> dict:
    o = get_cached("market_overview", symbol, None) or {}
    evr = o.get("ev_to_revenue")
    eve = o.get("ev_to_ebitda")
    return {"symbol": symbol, "name": o.get("name"), "ev_to_revenue": evr if evr and evr > 0 else None,
            "ev_to_ebitda": eve if eve and eve > 0 else None, "revenue_growth_pct": revenue_growth_pct(o),
            "as_of": o.get("as_of")}


def pod_relative_value() -> dict:
    contexts = active_contexts()
    covered = {c.symbol: c for c in contexts}
    members: dict[str, list[str]] = {}
    users: dict[str, set] = {}
    for c in contexts:
        for bucket, peers in (c.comps_buckets or {}).items():
            syms = members.setdefault(bucket, [])
            for sym in [c.symbol, *peers]:
                if sym not in syms:
                    syms.append(sym)
            users.setdefault(bucket, set()).add(c.symbol)
    buckets = []
    for bucket, syms in members.items():
        names = []
        for sym in syms:
            n = _name(sym)
            ctx = covered.get(sym)
            if ctx:
                th = current_thesis(ctx.ticker_id)
                t = th["thesis"] if th else {}
                n.update(covered=True, analyst=ctx.analyst_name, analyst_key=ctx.analyst_key, position=t.get("position"),
                         market_missing=t.get("market_missing"))
            else:
                n.update(covered=False)
            names.append(n)
        scored = score_bucket(names)
        ranked = [r for r in scored["rows"] if r["verdict"]]
        pair = None
        if len(ranked) >= 2:
            # prefer covered names on each leg, else the extremes
            rich = next((r for r in ranked if r["covered"] and r["verdict"] == "rich"), ranked[0])
            cheap = next((r for r in reversed(ranked) if r["covered"] and r["verdict"] == "cheap"), ranked[-1])
            if rich["symbol"] != cheap["symbol"] and rich["gap_pct"] - cheap["gap_pct"] > 15:
                pair = {"long": cheap["symbol"], "short": rich["symbol"], "spread_pct": rich["gap_pct"] - cheap["gap_pct"],
                        "basis": rich["basis"] if rich["basis"] == cheap["basis"] else "mixed"}
        buckets.append({"bucket": bucket, "used_by": sorted(users[bucket]), **scored, "pair_idea": pair,
                        "priced": len(ranked), "members": len(syms)})
    buckets.sort(key=lambda b: (-(b["pair_idea"]["spread_pct"] if b["pair_idea"] else 0), b["bucket"]))
    return {"buckets": buckets, "rich_cheap_band_pct": 15.0,
            "note": "LTM EV/revenue from the market-data cache; fair multiple = EV/revenue regressed on revenue growth across "
                    "the bucket (needs 3+ names with growth and an upward slope), else the bucket median."}


def desk_relative_value(symbols: list[str]) -> list[dict]:
    """Compact rich-to-cheap view of the buckets these tickers trade in (for agent prompts)."""
    mine = set(symbols)
    out = []
    for b in pod_relative_value()["buckets"]:
        if not mine & set(b["used_by"]):
            continue
        out.append({"bucket": b["bucket"], "pair_idea": b["pair_idea"],
                    "rich_to_cheap": [{"symbol": r["symbol"], "ev_to_revenue": round(r["ev_to_revenue"], 1) if r["ev_to_revenue"] else None,
                                       "growth_pct": round(r["revenue_growth_pct"], 1) if r["revenue_growth_pct"] is not None else None,
                                       "vs_fair_pct": round(r["gap_pct"], 0) if r["gap_pct"] is not None else None,
                                       "verdict": r["verdict"], "covered": r["covered"]} for r in b["rows"] if r["verdict"]]})
    return out
