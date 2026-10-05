"""One-time initial coverage: run every queued deep dive today under a raised budget.

    python -m scheduler.bootstrap --budget 100 --workers 3 --meeting

The normal $5/day governor still applies to everything else; this only raises the cap for this run
(today's spend counts toward it). Stops queueing new deep dives once the next one would cross --budget.
"""

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select

from agents import llm
from database.db import init_db, session_scope
from database.models import DataCache, Job, Ticker
from scheduler.jobs import _claim, bootstrap_coverage, reset_interrupted_jobs, run_job
from utils import config

logger = logging.getLogger("bootstrap")


def queued_deep_dives() -> list[tuple[int, str]]:
    with session_scope() as s:
        rows = s.execute(select(Job.id, Ticker.symbol).join(Ticker, Job.ticker_id == Ticker.id)
                         .where(Job.kind == "deep_dive", Job.status.in_(["queued", "deferred"]), Ticker.active.is_(True))
                         .order_by(Job.created_at)).all()
        for job_id, _ in rows:  # deferred -> queued so they can be claimed
            s.get(Job, job_id).status = "queued"
        return [(r[0], r[1]) for r in rows]


def run(budget: float, workers: int) -> list[dict]:
    config.DAILY_BUDGET_USD = budget  # raised cap for this run only (read live by the governor)
    jobs = queued_deep_dives()
    print(f"{len(jobs)} deep dives queued; spent today ${llm.spent_today():.2f}; cap ${budget:.0f}", flush=True)
    results = []

    def work(job_id: int, symbol: str) -> dict:
        if llm.spent_today() + config.DEEP_DIVE_ESTIMATE_USD > budget:
            return {"ticker": symbol, "status": "skipped", "error": "budget cap reached"}
        if not _claim(job_id):
            return {"ticker": symbol, "status": "skipped", "error": "already running"}
        t0 = time.time()
        r = run_job(job_id)
        r.setdefault("ticker", symbol)
        r["seconds"] = round(time.time() - t0)
        print(f"  {symbol:<6} {r['status']:<8} {r.get('signal', ''):<6} conviction {r.get('conviction', '-')}"
              f"  ${r.get('cost_usd', 0):.2f}  {r['seconds']}s  | today ${llm.spent_today():.2f}"
              + (f"  ERROR {r.get('error')}" if r["status"] != "done" else ""), flush=True)
        return r

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, job_id, sym) for job_id, sym in jobs]
        for f in as_completed(futures):
            results.append(f.result())
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=100.0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--meeting", action="store_true", help="run the pod meeting afterwards")
    args = parser.parse_args()

    config.setup_logging()
    logging.getLogger().setLevel(logging.WARNING)  # keep the console to the progress lines
    init_db()
    reset_interrupted_jobs()
    with session_scope() as s:  # drop market data cached from the old Alpha Vantage feed
        s.query(DataCache).filter(DataCache.data_type.in_(["market_overview", "market_quote"])).delete()
    bootstrap_coverage()

    results = run(args.budget, args.workers)
    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        print(f"Retrying {len(failed)} failed deep dives once…", flush=True)
        from scheduler.jobs import enqueue
        for r in failed:
            enqueue("deep_dive", r["ticker"], "initial coverage (retry)", "initial")
        results += run(args.budget, 1)

    done = [r for r in results if r["status"] == "done"]
    print(f"\nDeep dives done: {len(done)}; spent today ${llm.spent_today():.2f}", flush=True)
    if args.meeting:
        from agents.meeting import run_meeting
        config.DAILY_BUDGET_USD = max(args.budget, llm.spent_today() + config.MEETING_ESTIMATE_USD + 1)
        m = run_meeting("manual")
        print(f"Meeting #{m['meeting_id']}: revised {m['revised_theses']}; spent today ${llm.spent_today():.2f}", flush=True)


if __name__ == "__main__":
    main()
