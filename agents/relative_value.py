"""Pod-wide relative value board: who is over- vs under-priced against peers in the same bucket.

Every comps bucket any covered ticker uses, with every name in it (covered names and their peers), scored on the
bucket's multiple (EV/Revenue or EV/EBITDA: the industry's sell-side convention from coverage.yaml, else chosen
by how profitable the bucket is) vs the bucket median and vs a growth-adjusted fair multiple, ranked rich to
cheap, with the long-cheap / short-rich pair it implies. Each row also carries LTM revenue growth, EBITDA growth
and EBITDA margin (last four quarters, SEC XBRL). Built from caches only, so it is free to load; the per-ticker
models add the forward, model-based view and private-market marks.
"""

from agents.financial_model import choose_basis, score_bucket
from agents.registry import active_contexts, comps_basis_overrides
from agents.thesis import current_thesis
from data_sources.fundamentals import peer_metrics


def pod_relative_value() -> dict:
    contexts = active_contexts()
    covered = {c.symbol: c for c in contexts}
    overrides = comps_basis_overrides()
    members: dict[str, list[str]] = {}
    users: dict[str, set] = {}
    for c in contexts:
        for bucket, peers in (c.comps_buckets or {}).items():
            syms = members.setdefault(bucket, [])
            for sym in [c.symbol, *peers]:
                if sym not in syms:
                    syms.append(sym)
            users.setdefault(bucket, set()).add(c.symbol)
    metrics: dict[str, dict] = {}
    buckets = []
    for bucket, syms in members.items():
        names = []
        for sym in syms:
            n = dict(metrics.setdefault(sym, peer_metrics(sym)))
            ctx = covered.get(sym)
            if ctx:
                th = current_thesis(ctx.ticker_id)
                t = th["thesis"] if th else {}
                n.update(covered=True, analyst=ctx.analyst_name, analyst_key=ctx.analyst_key, position=t.get("position"))
            else:
                n.update(covered=False)
            names.append(n)
        basis, why = choose_basis(names, overrides.get(bucket))
        scored = score_bucket(names, basis)
        ranked = [r for r in scored["rows"] if r["verdict"]]
        pair = None
        if len(ranked) >= 2:
            # prefer covered names on each leg, else the extremes
            rich = next((r for r in ranked if r["covered"] and r["verdict"] == "rich"), ranked[0])
            cheap = next((r for r in reversed(ranked) if r["covered"] and r["verdict"] == "cheap"), ranked[-1])
            if rich["symbol"] != cheap["symbol"] and rich["gap_pct"] - cheap["gap_pct"] > 15:
                pair = {"long": cheap["symbol"], "short": rich["symbol"], "spread_pct": rich["gap_pct"] - cheap["gap_pct"],
                        "basis": rich["basis"] if rich["basis"] == cheap["basis"] else "mixed"}
        buckets.append({"bucket": bucket, "used_by": sorted(users[bucket]), **scored, "multiple_reason": why,
                        "pair_idea": pair, "priced": len(ranked), "members": len(syms)})
    buckets.sort(key=lambda b: (-(b["pair_idea"]["spread_pct"] if b["pair_idea"] else 0), b["bucket"]))
    live = [m for m in metrics.values() if m.get("ev_live")]
    return {"buckets": buckets, "rich_cheap_band_pct": 15.0,
            "priced_as_of": max((m["priced_at"] for m in live), default=None),
            "live_names": len(live), "total_names": len(metrics),
            "note": "EV marked to the latest price (cached EV - cached market cap + price x shares), over LTM (last four quarters) revenue or EBITDA from SEC filings, else the market feed's TTM. "
                    "EBITDA = operating income + D&A + stock comp. Fair multiple = the bucket's multiple regressed on LTM "
                    "revenue growth (needs 3+ names and an upward slope), else the bucket median."}


def desk_relative_value(symbols: list[str]) -> list[dict]:
    """Compact rich-to-cheap view of the buckets these tickers trade in (for agent prompts)."""
    mine = set(symbols)
    r1 = lambda v: round(v, 1) if v is not None else None  # noqa: E731
    out = []
    for b in pod_relative_value()["buckets"]:
        if not mine & set(b["used_by"]):
            continue
        out.append({"bucket": b["bucket"], "multiple": b["multiple_name"], "why": b["multiple_reason"], "pair_idea": b["pair_idea"],
                    "rich_to_cheap": [{"symbol": r["symbol"], "multiple": r1(r["multiple"]), "ltm_revenue_growth_pct": r1(r["revenue_growth_pct"]),
                                       "ltm_ebitda_growth_pct": r1(r["ebitda_growth_pct"]), "ltm_ebitda_margin_pct": r1(r["ebitda_margin_pct"]),
                                       "vs_fair_pct": round(r["gap_pct"]) if r["gap_pct"] is not None else None,
                                       "verdict": r["verdict"], "covered": r["covered"]} for r in b["rows"] if r["verdict"]]})
    return out
