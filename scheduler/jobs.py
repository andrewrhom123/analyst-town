"""Event-driven scheduler (US/Eastern):

  every 30 min, Mon-Fri 9:00-16:30  price_check     Finnhub quotes -> SMS alerts (+/-5% today) + thesis triggers  $0
  Mon-Fri 8:00 and 17:00            filing_check    new 10-Q/10-K/earnings 8-K -> deep dive             $0
  daily 7:15                        news_refresh    NewsAPI headlines -> latest_events.md                $0
  Mon-Fri 16:30                     daily meeting   analysts challenge and revise theses               ~$1
  every 1 min                       process_queue   build-out lane (parallel) + daily lane (one job at a time)
  daily 0:05                        daily_reset     budget-deferred jobs go back in the queue

Two lanes:
  * build-out: initial-coverage deep dives (first model/memo/thesis per ticker) are paid from the one-time
    BUILDOUT_BUDGET_USD pool and run in parallel, one worker per agent, so every name gets up to speed fast.
    When the pool is used up, leftover initial jobs fall back to the daily lane.
  * daily: everything else (thesis updates on price moves, deep dives on new filings / thesis flags) runs one
    job at a time under DAILY_BUDGET_USD, with deep dives spaced >= DEEP_DIVE_MIN_GAP_MINUTES apart.
Every job reserves its estimated cost against its pool before it starts, so concurrent jobs can't overshoot.

Run standalone as a worker:  python -m scheduler.jobs
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agents import llm
from alerts.sms import check_price_alerts
from agents.base_analyst import run_deep_dive
from agents.coverage_files import ensure_files, refresh_files
from agents.meeting import run_meeting
from agents.registry import active_contexts, get_context, sync_from_yaml
from agents.thesis import backfill_reference_price, current_thesis, price_triggers, update_thesis
from data_sources.news_fetcher import get_news
from data_sources.price_feed import poll_quotes
from data_sources.sec_fetcher import invalidate_sec_cache, latest_material_filing
from database.db import session_scope
from database.models import Job, ResearchOutput, ScheduledRunLock, Ticker
from utils import config

logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
_budget_lock = threading.Lock()
_reserved: dict[int, tuple[str, float]] = {}  # job id -> (pool, estimated cost) of jobs currently running
_busy_agents: set[int] = set()  # analyst ids with a build-out worker running
_daily_busy = threading.Event()  # set while the daily lane is running a job
_busy_lock = threading.Lock()
BUILDOUT, DAILY = "buildout", "daily"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:  # SQLite drops tzinfo; everything is stored in UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt


# --- Queue ---------------------------------------------------------------------------------------

def enqueue(kind: str, symbol: str, reason: str, trigger: str) -> int | None:
    """Queue a Claude job unless an equivalent one is already pending. Returns the job id (or None)."""
    ctx = get_context(symbol)
    if ctx is None:
        raise llm.AnalystError(f"Unknown ticker {symbol}")
    with session_scope() as s:
        pending = s.scalar(select(Job).where(Job.kind == kind, Job.ticker_id == ctx.ticker_id,
                                             Job.status.in_(["queued", "running", "deferred"])).limit(1))
        if pending:
            return None
        job = Job(kind=kind, ticker_id=ctx.ticker_id, reason=reason, trigger=trigger)
        s.add(job)
        s.flush()
        logger.info("Queued %s for %s (%s)", kind, ctx.symbol, reason)
        return job.id


def _last_deep_dive_start() -> datetime | None:
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == "deep_dive", Job.started_at.is_not(None)).order_by(Job.started_at.desc()).limit(1))
        return _aware(job.started_at) if job else None


def _queued(*conditions) -> Job | None:
    with session_scope() as s:
        return s.scalar(select(Job).join(Ticker, Job.ticker_id == Ticker.id)
                        .where(Job.status == "queued", *conditions).order_by(Job.created_at).limit(1))


def _next_buildout_job(analyst_id: int) -> Job | None:
    return _queued(Job.kind == "deep_dive", Job.trigger == "initial", Ticker.analyst_id == analyst_id)


def _next_daily_job(buildout_is_open: bool) -> Job | None:
    """Thesis updates first (cheap, time-sensitive); deep dives only after the spacing gap (manual ones skip it).
    Initial-coverage deep dives stay in the build-out lane while its pool is open."""
    job = _queued(Job.kind == "thesis_update")
    if job:
        return job
    job = _queued(Job.kind == "deep_dive", *([Job.trigger != "initial"] if buildout_is_open else []))
    if job is None:
        return None
    last = _last_deep_dive_start()
    if job.trigger != "manual" and last and datetime.now(timezone.utc) - last < timedelta(minutes=config.DEEP_DIVE_MIN_GAP_MINUTES):
        return None
    return job


def _agents_with_buildout_jobs() -> list[int]:
    with session_scope() as s:
        return list(s.scalars(select(Ticker.analyst_id).join(Job, Job.ticker_id == Ticker.id)
                              .where(Job.status == "queued", Job.kind == "deep_dive", Job.trigger == "initial").distinct()))


def _reserved_in(pool: str) -> float:
    return sum(est for p, est in _reserved.values() if p == pool)


def _reserve(job_id: int, pool: str, kind: str, estimate: float) -> tuple[bool, str]:
    """Atomically check the pool's budget, counting what already-running jobs are expected to spend."""
    with _budget_lock:
        if pool == BUILDOUT:
            spent = llm.buildout_spent()
            ok = spent + _reserved_in(BUILDOUT) + estimate <= config.BUILDOUT_BUDGET_USD
            why = "" if ok else f"build-out pool: spent ${spent:.2f} of ${config.BUILDOUT_BUDGET_USD:.2f}"
        else:
            ok, why = llm.can_spend(kind, estimate + _reserved_in(DAILY))
        if ok:
            _reserved[job_id] = (pool, estimate)
        return ok, why


def _release(job_id: int) -> None:
    with _budget_lock:
        _reserved.pop(job_id, None)


def buildout_open() -> bool:
    """Whether the one-time pool can still fund another initial deep dive."""
    with _budget_lock:
        return llm.buildout_spent() + _reserved_in(BUILDOUT) + config.DEEP_DIVE_ESTIMATE_USD <= config.BUILDOUT_BUDGET_USD


def _claim(job_id: int) -> bool:
    with session_scope() as s:
        result = s.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                           .values(status="running", started_at=datetime.now(timezone.utc)))
        return result.rowcount == 1


def _finish(job_id: int, status: str, error: str | None = None, cost: float = 0.0) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        job.status, job.error, job.cost_usd = status, error, cost
        job.finished_at = datetime.now(timezone.utc)


def run_job(job_id: int, pool: str | None = None) -> dict:
    """Run a claimed job. With `pool`, its cost was already reserved by the caller; otherwise it is
    checked against the daily budget here and deferred if it doesn't fit."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        kind, reason, trigger = job.kind, job.reason, job.trigger
        symbol = s.get(Ticker, job.ticker_id).symbol
    estimate = config.DEEP_DIVE_ESTIMATE_USD if kind == "deep_dive" else config.THESIS_UPDATE_ESTIMATE_USD
    # Your own requests (manual) may use the whole budget; autonomous work keeps the meeting/ask reserves.
    if pool is None:
        ok, why = _reserve(job_id, DAILY, "ask" if trigger == "manual" else kind, estimate)
        if not ok:
            _finish(job_id, "deferred", why)
            logger.info("Deferred %s for %s: %s", kind, symbol, why)
            return {"job_id": job_id, "status": "deferred", "error": why}
    spend = llm.buildout_spent if pool == BUILDOUT else llm.spent_today
    spent_before = spend()  # pool-wide total; with parallel agents a job's cost can include its neighbours'
    try:
        if kind == "deep_dive" and pool == BUILDOUT:
            with llm.buildout_scope(symbol):
                result = run_deep_dive(symbol, trigger=f"{trigger}: {reason}")
        elif kind == "deep_dive":
            result = run_deep_dive(symbol, trigger=f"{trigger}: {reason}")
        else:
            thesis = update_thesis(symbol, trigger, reason)
            refresh_files(symbol, ["trading_thesis.md", "latest_events.md"])
            result = {"ticker": symbol, "signal": thesis["signal"], "headline": thesis["headline"]}
            if thesis.get("needs_deep_dive"):
                enqueue("deep_dive", symbol, thesis.get("deep_dive_reason") or "flagged by thesis update", "thesis_update")
        _finish(job_id, "done", cost=spend() - spent_before)
        return {"job_id": job_id, "status": "done", **result}
    except Exception as exc:
        logger.exception("Job %s (%s %s) failed", job_id, kind, symbol)
        _finish(job_id, "failed", f"{type(exc).__name__}: {exc}", cost=spend() - spent_before)
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
    finally:
        _release(job_id)


def _buildout_worker(analyst_id: int) -> None:
    """Work through one agent's initial deep dives on the build-out pool until done or the pool runs dry."""
    try:
        while (job := _next_buildout_job(analyst_id)) is not None:
            ok, why = _reserve(job.id, BUILDOUT, "deep_dive", config.DEEP_DIVE_ESTIMATE_USD)
            if not ok:
                logger.info("Build-out pool closed (%s); remaining initial jobs go to the daily lane", why)
                break  # job stays queued for the daily lane
            if not _claim(job.id):
                _release(job.id)
                continue  # another worker took it
            run_job(job.id, pool=BUILDOUT)
    except Exception:
        logger.exception("Build-out worker for analyst %s crashed", analyst_id)
    finally:
        with _busy_lock:
            _busy_agents.discard(analyst_id)


def _daily_worker(job_id: int) -> None:
    try:
        run_job(job_id)
    finally:
        _daily_busy.clear()


def process_queue() -> dict:
    """Non-blocking tick: start a build-out worker for every agent with initial deep dives (all agents in
    parallel while the pool lasts) and, if the daily lane is idle, start its next job."""
    started = []
    is_open = buildout_open()
    if is_open:
        with session_scope() as s:  # initial jobs deferred by the daily budget get another chance on the pool
            s.execute(update(Job).where(Job.status == "deferred", Job.kind == "deep_dive", Job.trigger == "initial")
                      .values(status="queued", error=None))
        for analyst_id in _agents_with_buildout_jobs():
            with _busy_lock:
                if analyst_id in _busy_agents:
                    continue
                _busy_agents.add(analyst_id)
            threading.Thread(target=_buildout_worker, args=(analyst_id,), name=f"buildout-{analyst_id}", daemon=True).start()
            started.append(analyst_id)
        if started:
            logger.info("Started build-out workers for analysts %s", started)

    daily_job = None
    with _busy_lock:
        if not _daily_busy.is_set():
            job = _next_daily_job(is_open)
            if job is not None and _claim(job.id):
                _daily_busy.set()
                daily_job = job.id
    if daily_job is not None:
        threading.Thread(target=_daily_worker, args=(daily_job,), name="daily-lane", daemon=True).start()
    return {"buildout_open": is_open, "buildout_workers_started": started, "daily_job": daily_job}


# --- Monitors (no Claude) ---------------------------------------------------------------------

def price_check() -> dict:
    contexts = [c for c in active_contexts() if c.price_symbol]
    quotes = poll_quotes(sorted({c.price_symbol for c in contexts}))
    triggered, alerts = [], []
    for ctx in contexts:
        q = quotes.get(ctx.price_symbol)
        if not q:
            continue
        backfill_reference_price(ctx.ticker_id, q["price"])
        th = current_thesis(ctx.ticker_id)
        try:
            alert = check_price_alerts(ctx, q, th["thesis"] if th else None)
            if alert:
                alerts.append(alert)
        except Exception:  # an alert problem must never stop the price monitor
            logger.exception("Price alert check for %s failed", ctx.symbol)
        reason = price_triggers(ctx, q["price"])
        if reason:
            with session_scope() as s:
                last = _aware(s.get(Ticker, ctx.ticker_id).last_thesis_at)
            if last and datetime.now(timezone.utc) - last < timedelta(hours=config.THESIS_COOLDOWN_HOURS):
                logger.info("%s trigger in cooldown: %s", ctx.symbol, reason)
            elif enqueue("thesis_update", ctx.symbol, reason, reason.split(":")[0]):
                triggered.append({"ticker": ctx.symbol, "reason": reason})
        refresh_files(ctx.symbol, ["trading_thesis.md", "latest_events.md", "current_price.json"])
    logger.info("Price check: %d quotes, %d triggers, %d SMS alerts", len(quotes), len(triggered), len(alerts))
    return {"quotes": len(quotes), "triggered": triggered, "alerts": alerts}


def filing_check() -> list[dict]:
    found = []
    for ctx in active_contexts():
        if ctx.ticker_type != "public" or not ctx.sec_ticker:
            continue
        try:
            filing = latest_material_filing(ctx.sec_ticker)
        except Exception as exc:
            logger.warning("Filing check for %s failed: %s", ctx.symbol, exc)
            continue
        if not filing:
            continue
        with session_scope() as s:
            t = s.get(Ticker, ctx.ticker_id)
            previous, t.last_filing_accession = t.last_filing_accession, filing["accession"]
        if previous and previous != filing["accession"]:
            invalidate_sec_cache(ctx.sec_ticker)
            reason = f"new filing {filing['form']} {filing['filing_date']}" + (f" (items {filing['items']})" if filing.get("items") else "")
            enqueue("deep_dive", ctx.symbol, reason, "new_filing")
            found.append({"ticker": ctx.symbol, **filing})
    return found


def news_refresh() -> None:
    for ctx in active_contexts():
        get_news(ctx.symbol, ctx.news_query, ctx.news_keywords)
        refresh_files(ctx.symbol, ["latest_events.md"])


def bootstrap_coverage() -> list[str]:
    """Sync coverage.yaml, create files for every ticker, and queue an initial deep dive where none exists."""
    sync_from_yaml()
    queued = []
    for ctx in active_contexts():
        ensure_files(ctx.symbol)
        with session_scope() as s:
            has_research = s.scalar(select(ResearchOutput.id).where(ResearchOutput.ticker_id == ctx.ticker_id).limit(1))
        if not has_research and current_thesis(ctx.ticker_id) is None:
            if enqueue("deep_dive", ctx.symbol, "initial coverage", "initial"):
                queued.append(ctx.symbol)
    return queued


def add_coverage(symbol: str, agent: str, spec: dict | None = None) -> dict:
    """Chat /add: identify the company, create its files, start tracking now, queue the initial deep dive."""
    from agents.registry import add_ticker
    from data_sources.price_feed import company_profile
    from data_sources.sec_fetcher import lookup_cik

    symbol = symbol.upper()
    spec = dict(spec or {})
    if "type" not in spec:
        profile = company_profile(symbol)
        try:
            _, sec_name = lookup_cik(symbol)
            spec.setdefault("type", "public")
            spec.setdefault("name", (profile or {}).get("name") or sec_name.title())
        except Exception:
            spec["type"] = "foreign" if profile else "private"
            spec.setdefault("name", (profile or {}).get("name") or symbol.title())
        if profile:
            spec.setdefault("description", f"{profile['name']} ({profile.get('exchange', '')}; {profile.get('finnhubIndustry', '')})")
    spec.setdefault("name", symbol)
    spec.setdefault("news_query", f'"{spec["name"]}"' + (f" OR {symbol}" if len(symbol) > 3 else ""))
    symbol = add_ticker({**spec, "symbol": symbol, "analyst": agent})
    ctx = get_context(symbol)
    if ctx.price_symbol:
        poll_quotes([ctx.price_symbol])
    ensure_files(symbol)
    job_id = enqueue("deep_dive", symbol, "initial coverage", "initial")
    return {"symbol": symbol, "name": ctx.name, "type": ctx.ticker_type, "agent": ctx.analyst_name,
            "initial_deep_dive_job": job_id}


def remove_coverage(symbol: str, agent: str | None = None) -> dict:
    """Chat /remove: stop tracking, archive files, cancel pending jobs (history stays in the DB)."""
    from agents.coverage_files import archive_files
    from agents.registry import remove_ticker

    symbol = remove_ticker(symbol, agent)
    ctx = get_context(symbol)
    with session_scope() as s:
        s.execute(update(Job).where(Job.ticker_id == ctx.ticker_id, Job.status.in_(["queued", "deferred"]))
                  .values(status="cancelled", error="ticker removed from coverage"))
    archive_files(symbol)
    return {"symbol": symbol, "status": "removed", "agent": ctx.analyst_name}


def daily_reset() -> None:
    with session_scope() as s:
        n = s.execute(update(Job).where(Job.status == "deferred").values(status="queued", error=None)).rowcount
    if n:
        logger.info("Re-queued %d budget-deferred jobs", n)


def reset_interrupted_jobs() -> None:
    with session_scope() as s:
        s.execute(update(Job).where(Job.status == "running").values(status="queued", started_at=None))


def _acquire_slot(run_key: str) -> bool:
    try:
        with session_scope() as s:
            s.add(ScheduledRunLock(run_key=run_key))
        return True
    except IntegrityError:
        return False


def scheduled_meeting() -> None:
    run_key = f"meeting-{datetime.now(EASTERN).date().isoformat()}"
    if not _acquire_slot(run_key):
        return
    try:
        result = run_meeting("scheduled")
        logger.info("Meeting %s done; revised: %s", result["meeting_id"], result["revised_theses"])
    except Exception:
        logger.exception("Scheduled meeting failed")


def ltm_refresh() -> None:
    """Weekly-fresh LTM fundamentals (SEC XBRL) for every covered name and comps peer: feeds the relative value board."""
    from data_sources.fundamentals import comps_universe, refresh_ltm, refresh_overviews

    try:
        universe = comps_universe()
        refresh_overviews(universe)
        refresh_ltm(universe)
    except Exception:
        logger.exception("LTM fundamentals refresh failed")


def create_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=EASTERN)
    weekdays = "mon-fri"
    jobs = [
        ("price_check", price_check, CronTrigger(day_of_week=weekdays, hour="9-16", minute=f"*/{config.PRICE_CHECK_MINUTES}", timezone=EASTERN)),
        ("filing_check", filing_check, CronTrigger(day_of_week=weekdays, hour="8,17", minute=0, timezone=EASTERN)),
        ("news_refresh", news_refresh, CronTrigger(hour=7, minute=15, timezone=EASTERN)),
        ("ltm_refresh", ltm_refresh, CronTrigger(hour=6, minute=30, timezone=EASTERN)),
        ("daily_meeting", scheduled_meeting, CronTrigger(day_of_week=weekdays, hour=config.MEETING_HOUR, minute=config.MEETING_MINUTE, timezone=EASTERN)),
        ("process_queue", process_queue, IntervalTrigger(minutes=1)),
        ("daily_reset", daily_reset, CronTrigger(hour=0, minute=5, timezone=EASTERN)),
    ]
    for job_id, fn, trigger in jobs:
        sched.add_job(fn, trigger, id=job_id, replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=600)
    return sched


def scheduler_status(sched: BackgroundScheduler | None) -> dict:
    if not sched:
        return {"running": False}
    return {"running": sched.running,
            "jobs": {j.id: j.next_run_time.isoformat() if j.next_run_time else None for j in sched.get_jobs()}}


if __name__ == "__main__":
    from database.db import init_db

    config.setup_logging()
    init_db()
    reset_interrupted_jobs()
    bootstrap_coverage()
    sched = create_scheduler()
    sched.start()
    logger.info("Worker scheduler started: %s", scheduler_status(sched))
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
