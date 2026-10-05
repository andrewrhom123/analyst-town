"""FastAPI entry point for the AI analyst pod backend.

Run locally:  uvicorn main:app --reload
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from agents import llm
from agents.base_analyst import build_track_record
from agents.briefing import HELP, ask, briefing, parse_command
from agents.dashboard import dashboard
from alerts.sms import SmsNotConfigured, recent_alerts, send_sms
from agents.coverage_files import FILENAMES, list_files, price_snapshot, read_file
from agents.meeting import latest_minutes, run_meeting
from agents.registry import CoverageError, add_analyst, analyst_info, get_context, list_coverage, reassign_ticker, update_ticker
from agents.thesis import current_thesis, thesis_history
from agents import voice
from data_sources.cache import get_cached
from data_sources.price_feed import chart_series, latest_tick
from database.db import get_db, init_db
from database.models import Analyst, CostEntry, FinancialModel, Interaction, Job, Meeting, PriceTick, ResearchOutput, Ticker
from exports.documents import memo_pdf, model_csv
from exports.excel_model import build_workbook
from scheduler.jobs import (
    add_coverage,
    bootstrap_coverage,
    remove_coverage,
    create_scheduler,
    enqueue,
    price_check,
    process_queue,
    reset_interrupted_jobs,
    run_job,
    scheduler_status,
    _claim,
)
from utils import config

config.setup_logging()
logger = logging.getLogger("api")
scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    init_db()
    reset_interrupted_jobs()
    queued = bootstrap_coverage()
    if queued:
        logger.info("Initial deep dives queued (run one at a time within the daily budget): %s", ", ".join(queued))
    if missing := config.missing_keys():
        logger.warning("Missing configuration: %s - affected features are skipped", ", ".join(missing))
    if not config.API_ACCESS_KEY:
        logger.warning("API_ACCESS_KEY is not set: endpoints that spend Claude credits are unprotected")
    if config.ENABLE_SCHEDULER:
        scheduler = create_scheduler()
        scheduler.start()
        logger.info("Scheduler started: %s", scheduler_status(scheduler)["jobs"])
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="AI Analyst Pod", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("%s %s -> unhandled error", request.method, request.url.path)
        raise
    logger.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, (time.perf_counter() - start) * 1000)
    return response


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if config.API_ACCESS_KEY and x_api_key != config.API_ACCESS_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    ticker: str | None = Field(None, description="Current ticker: plain text without a slash is asked to this agent")


class CoverageChange(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    agent: str | None = None
    to_agent: str | None = None


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite drops tzinfo; everything is stored in UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _ticker_or_404(symbol: str):
    ctx = get_context(symbol)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"{symbol.upper()} is not covered. GET /coverage for the roster.")
    return ctx


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except llm.BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"Over today's Claude budget: {exc}")
    except CoverageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except llm.AnalystError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# --- Status ----------------------------------------------------------------------------------

@app.get("/")
def root():
    return {"service": "AI Analyst Pod", "docs": "/docs",
            "commands": ["GET /briefing/{ticker}", "POST /ask/{ticker}", "POST /meeting", "POST /command"]}


@app.get("/status")
def status(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        db_ok = False
    jobs = {s: n for s, n in db.execute(select(Job.status, func.count()).group_by(Job.status)).all()}
    last_tick = db.scalar(select(func.max(PriceTick.ts)))
    last_job = db.scalar(select(func.max(Job.finished_at)))
    return {
        "last_price_check": _iso(last_tick),
        "last_analysis": _iso(last_job),
        "status": "ok" if db_ok else "degraded",
        "environment": config.ENVIRONMENT,
        "database": "sqlite" if config.DATABASE_URL.startswith("sqlite") else "postgresql",
        "missing_config": config.missing_keys(),
        "voice": config.voice_status(),
        "sms_alerts": {"enabled": config.SMS_ALERTS_ENABLED, "configured": config.sms_configured(), "threshold_pct": config.ALERT_MOVE_PCT},
        "models": {"deep_dive": config.DEEP_MODEL, "light": config.LIGHT_MODEL},
        "budget": {**(budget := llm.budget_status()), "buildout": {
            **budget["buildout"],
            "pending_jobs": db.scalar(select(func.count()).select_from(Job).where(
                Job.kind == "deep_dive", Job.trigger == "initial", Job.status.in_(["queued", "running", "deferred"]))),
            "running_jobs": db.scalar(select(func.count()).select_from(Job).where(
                Job.kind == "deep_dive", Job.trigger == "initial", Job.status == "running")),
        }},
        "jobs": jobs,
        "scheduler": scheduler_status(scheduler),
        "coverage": {"analysts": db.scalar(select(func.count()).select_from(Analyst).where(Analyst.active.is_(True))),
                     "tickers": db.scalar(select(func.count()).select_from(Ticker).where(Ticker.active.is_(True)))},
    }


def _ticker_card(symbol: str) -> dict:
    ctx = get_context(symbol)
    th = current_thesis(ctx.ticker_id)
    tick = latest_tick(ctx.price_symbol) if ctx.price_symbol else None
    t = th["thesis"] if th else {}
    return {
        "symbol": ctx.symbol, "name": ctx.name, "type": ctx.ticker_type, "aliases": ctx.aliases,
        "price": tick["price"] if tick else None,
        "change_pct": tick["change_pct"] if tick else None,
        "price_updated": tick["ts"] if tick else None,
        "signal": t.get("signal"), "position": t.get("position"), "headline": t.get("headline"),
        "conviction_level": t.get("conviction_level"), "risk_level": t.get("risk_level"),
        "thesis_updated": _iso(th["created_at"]) if th else None,
        "has_research": th is not None,
    }


def _agent_summary(a: dict, db: Session) -> dict:
    tickers = [_ticker_card(t["symbol"]) for t in a["tickers"]]
    ticker_ids = [get_context(t["symbol"]).ticker_id for t in a["tickers"]]
    running = db.scalar(select(func.count()).select_from(Job).where(Job.ticker_id.in_(ticker_ids), Job.status == "running")) if ticker_ids else 0
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.ticker_id.in_(ticker_ids), Job.status == "queued")) if ticker_ids else 0
    convictions = [t["conviction_level"] for t in tickers if t["conviction_level"] is not None]
    updates = [t["thesis_updated"] for t in tickers if t["thesis_updated"]]
    return {
        "id": a["key"], "key": a["key"], "name": a["name"], "agent_type": a["agent_type"], "color": a["color"],
        "focus_notes": a["focus_notes"],
        "status": "working" if running else ("queued" if queued else "idle"),
        "ticker_count": len(tickers),
        "covered_count": sum(t["has_research"] for t in tickers),
        "avg_conviction": round(sum(convictions) / len(convictions), 1) if convictions else None,
        "last_update": max(updates) if updates else None,
        "tickers": tickers,
    }


@app.get("/agents")
def agents(db: Session = Depends(get_db)):
    """All agents with status and their tickers' live price and current call (what the town renders)."""
    return [_agent_summary(a, db) for a in list_coverage()]


@app.get("/dashboard")
def get_dashboard():
    """News feed, cross-ticker themes and earnings dates for the town page (cached data only, no API calls)."""
    return dashboard()


def _agent_or_404(agent_id: str) -> dict:
    try:
        return analyst_info(agent_id)
    except CoverageError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/agents/{agent_id}/tickers")
def agent_tickers(agent_id: str):
    a = _agent_or_404(agent_id)
    return {"agent": a["name"], "id": a["key"], "color": a["color"], "tickers": [_ticker_card(t["symbol"]) for t in a["tickers"]]}


@app.get("/agents/{agent_id}/ticker/{symbol}/briefing")
def agent_briefing(agent_id: str, symbol: str):
    _agent_or_404(agent_id)
    _ticker_or_404(symbol)
    return _handle(briefing, symbol)


@app.get("/agents/{agent_id}/ticker/{symbol}/memo")
def agent_memo(agent_id: str, symbol: str, db: Session = Depends(get_db)):
    _agent_or_404(agent_id)
    return get_research(symbol, 10, db)


@app.get("/agents/{agent_id}/ticker/{symbol}/model")
def agent_model(agent_id: str, symbol: str):
    _agent_or_404(agent_id)
    _ticker_or_404(symbol)
    f = read_file(symbol, "financial_model.json")
    return json.loads(f["content"]) if f else {"note": "no model yet"}


@app.get("/agents/{agent_id}/ticker/{symbol}/price")
def agent_price(agent_id: str, symbol: str):
    _agent_or_404(agent_id)
    return price_snapshot(_ticker_or_404(symbol))


@app.get("/agents/{agent_id}/ticker/{symbol}/chart")
def agent_chart(agent_id: str, symbol: str, days: int = Query(30, ge=5, le=100)):
    _agent_or_404(agent_id)
    ctx = _ticker_or_404(symbol)
    if not ctx.price_symbol:
        return {"ticker": ctx.symbol, "series": [], "levels": None, "note": "private company, no market price"}
    snap = price_snapshot(ctx)
    return {"ticker": ctx.symbol, "price_symbol": ctx.price_symbol, "series": chart_series(ctx.price_symbol, days),
            "levels": snap["levels"], "price": snap["price"], "change_pct": snap["change_pct"]}


@app.post("/agents/{agent_id}/ticker/{symbol}/ask", dependencies=[Depends(require_key)])
def agent_ask(agent_id: str, symbol: str, body: AskIn):
    _agent_or_404(agent_id)
    _ticker_or_404(symbol)
    return _handle(ask, symbol, body.question)


@app.get("/agents/{agent_id}/ticker/{symbol}/chat")
def agent_chat_history(agent_id: str, symbol: str, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    _agent_or_404(agent_id)
    ctx = _ticker_or_404(symbol)
    rows = db.scalars(select(Interaction).where(Interaction.ticker_id == ctx.ticker_id)
                      .order_by(Interaction.date.desc()).limit(limit)).all()[::-1]
    return [{"id": r.id, "date": _iso(r.date), "question": r.user_question, "answer": r.agent_response} for r in rows]


@app.get("/agents/{agent_id}/ticker/{symbol}/download/{kind}")
def agent_download(agent_id: str, symbol: str, kind: str, db: Session = Depends(get_db)):
    """kind: memo.pdf | memo.md | model.csv | model.xlsx | research.json | model.json"""
    _agent_or_404(agent_id)
    ctx = _ticker_or_404(symbol)
    stamp = date.today().isoformat()
    if kind in ("memo.pdf", "memo.md"):
        memo = (read_file(ctx.symbol, "company_memo.md") or {}).get("content")
        if not memo or "_No deep dive yet" in memo:
            raise HTTPException(status_code=404, detail=f"No memo yet for {ctx.symbol}")
        if kind == "memo.md":
            return Response(memo, media_type="text/markdown",
                            headers={"Content-Disposition": f'attachment; filename="{ctx.symbol}_memo_{stamp}.md"'})
        return Response(memo_pdf(memo, f"{ctx.symbol} research memo"), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{ctx.symbol}_memo_{stamp}.pdf"'})
    if kind == "model.xlsx":
        return get_model_xlsx(ctx.symbol, db)
    if kind == "model.csv":
        fm = db.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ctx.ticker_id).order_by(FinancialModel.date.desc()).limit(1))
        if fm is None:
            raise HTTPException(status_code=404, detail=f"No financial model for {ctx.symbol}")
        return Response(model_csv(ctx.symbol, fm.assumptions["outputs"]), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{ctx.symbol}_model_{stamp}.csv"'})
    if kind == "research.json":
        body = json.dumps(get_research(ctx.symbol, 10, db), indent=2, default=str)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{ctx.symbol}_research_{stamp}.json"'})
    if kind == "model.json":
        f = read_file(ctx.symbol, "financial_model.json")
        return Response(f["content"] if f else "{}", media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{ctx.symbol}_model_{stamp}.json"'})
    raise HTTPException(status_code=404, detail="kind must be memo.pdf, memo.md, model.csv, model.xlsx, research.json or model.json")


@app.get("/costs")
def costs(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    day = func.date(CostEntry.ts)
    rows = db.execute(select(day, CostEntry.kind, func.count(), func.sum(CostEntry.usd))
                      .group_by(day, CostEntry.kind).order_by(day.desc()).limit(days * 5)).all()
    return {"today": llm.budget_status(),
            "history": [{"date": str(d), "kind": k, "calls": n, "usd": round(u or 0, 4)} for d, k, n, u in rows]}


# --- Commands ---------------------------------------------------------------------------------

@app.get("/briefing/{symbol}")
def get_briefing(symbol: str, format: str = Query("json", pattern="^(json|md)$")):
    _ticker_or_404(symbol)
    b = _handle(briefing, symbol)
    return PlainTextResponse(b["briefing_md"], media_type="text/markdown") if format == "md" else b


@app.post("/ask/{symbol}", dependencies=[Depends(require_key)])
def post_ask(symbol: str, body: AskIn):
    _ticker_or_404(symbol)
    return _handle(ask, symbol, body.question)


def _run_now(job_id: int | None) -> None:
    if job_id and _claim(job_id):
        run_job(job_id)


@app.post("/deepdive/{symbol}", status_code=202, dependencies=[Depends(require_key)])
def post_deepdive(symbol: str, background: BackgroundTasks):
    ctx = _ticker_or_404(symbol)
    job_id = enqueue("deep_dive", ctx.symbol, "requested by PM", "manual")
    if job_id is None:
        return {"ticker": ctx.symbol, "status": "already queued or running"}
    background.add_task(_run_now, job_id)
    return {"ticker": ctx.symbol, "job_id": job_id, "status": "started", "estimate_usd": config.DEEP_DIVE_ESTIMATE_USD}


@app.post("/update/{symbol}", status_code=202, dependencies=[Depends(require_key)])
def post_update(symbol: str, background: BackgroundTasks):
    ctx = _ticker_or_404(symbol)
    job_id = enqueue("thesis_update", ctx.symbol, "requested by PM", "manual")
    if job_id is None:
        return {"ticker": ctx.symbol, "status": "already queued or running"}
    background.add_task(_run_now, job_id)
    return {"ticker": ctx.symbol, "job_id": job_id, "status": "started"}


def _meeting_task() -> None:
    try:
        run_meeting("manual")
    except Exception:
        logger.exception("Manual meeting failed")


@app.post("/meeting", status_code=202, dependencies=[Depends(require_key)])
def post_meeting(background: BackgroundTasks):
    ok, why = llm.can_spend("meeting", config.MEETING_ESTIMATE_USD)
    if not ok:
        raise HTTPException(status_code=429, detail=why)
    background.add_task(_meeting_task)
    return {"status": "started", "estimate_usd": config.MEETING_ESTIMATE_USD, "poll": "/meetings/latest"}


@app.get("/meetings/latest")
def get_latest_meeting():
    m = latest_minutes()
    if m is None:
        raise HTTPException(status_code=404, detail="No meetings yet")
    return m


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    agent: str | None = Field(None, description="Analyst key whose voice to use, or 'chair'")


def _voice_call(fn, *args):
    try:
        return fn(*args)
    except voice.VoiceNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except voice.VoiceLimitReached as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except voice.VoiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/voice/status")
def voice_status():
    """Which voice features are configured, the voice profile per analyst, and today's TTS usage."""
    return {**config.voice_status(), **voice.usage_today(),
            "profiles": {a["key"]: voice.profile_for(a["key"]).public() for a in list_coverage()},
            "chair": voice.CHAIR.public()}


@app.post("/voice/tts", dependencies=[Depends(require_key)])
def voice_tts(body: SpeakIn):
    """Speak `text` in the analyst's voice (ElevenLabs). Returns audio/mpeg; replays come from the disk cache."""
    audio, info = _voice_call(voice.synthesize, body.text, body.agent)
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"X-Voice": info["voice"], "X-Voice-Cached": str(info["cached"]).lower(), "Cache-Control": "private, max-age=86400"})


@app.post("/voice/stt", dependencies=[Depends(require_key)])
async def voice_stt(request: Request):
    """Transcribe a recording (raw request body, e.g. audio/webm from MediaRecorder) with ElevenLabs Scribe (tickers sent as keyterms)."""
    audio = await request.body()
    vocabulary = [x for a in list_coverage() for t in a["tickers"] for x in (t["symbol"], t["name"])]
    text_out = _voice_call(voice.transcribe, audio, request.headers.get("content-type", "audio/webm"), vocabulary)
    return {"text": text_out}


@app.get("/meetings/latest/script")
def get_latest_meeting_script(db: Session = Depends(get_db)):
    """The latest finished meeting as spoken turns for boardroom playback (speaker, voice, round, text)."""
    m = db.scalar(select(Meeting).where(Meeting.status == "done").order_by(Meeting.started_at.desc()).limit(1))
    if m is None:
        raise HTTPException(status_code=404, detail="No finished meetings yet")
    return {"meeting_id": m.id, "finished_at": _iso(m.finished_at),
            "lines": voice.meeting_script(m.id, m.transcript or {}, m.minutes_md)}


@app.get("/meetings")
def get_meetings(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    rows = db.scalars(select(Meeting).order_by(Meeting.started_at.desc()).limit(limit)).all()
    return [{"meeting_id": m.id, "trigger": m.trigger, "status": m.status, "started_at": _iso(m.started_at),
             "finished_at": _iso(m.finished_at), "error": m.error} for m in rows]


@app.post("/command")
def post_command(body: CommandIn, background: BackgroundTasks, x_api_key: str | None = Header(default=None)):
    """Chat entry point. Slash commands: /briefing, /ask, /meeting, /add, /remove, /reassign, /coverage,
    /deepdive, /update. Plain text (no slash) is asked to the agent covering `ticker`."""
    text = body.text.strip()
    if text.lower() in ("/help", "help", "/?"):
        return {"command": "help", "message": HELP}
    if not text.startswith("/"):
        if not body.ticker:
            raise HTTPException(status_code=400, detail="Open a ticker first, or use a command. " + HELP)
        text = f"/ask {body.ticker}: {text}"
    try:
        cmd = parse_command(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    name = cmd["command"]
    if name not in ("briefing", "coverage"):
        require_key(x_api_key)
    if name == "briefing":
        return {"command": name, **get_briefing(cmd["ticker"])}
    if name == "ask":
        return {"command": name, **post_ask(cmd["ticker"], AskIn(question=cmd["question"]))}
    if name == "meeting":
        return {"command": name, **post_meeting(background)}
    if name == "deepdive":
        return {"command": name, **post_deepdive(cmd["ticker"], background)}
    if name == "update":
        return {"command": name, **post_update(cmd["ticker"], background)}
    if name == "coverage":
        return {"command": name, "agents": [get_agent_coverage(cmd["agent"])] if cmd["agent"] else list_coverage()}
    if name == "add":
        return {"command": name, **coverage_add(CoverageChange(ticker=cmd["ticker"], agent=cmd["agent"]))}
    if name == "remove":
        return {"command": name, **coverage_remove(CoverageChange(ticker=cmd["ticker"], agent=cmd["agent"]))}
    return {"command": name, **coverage_reassign(CoverageChange(ticker=cmd["ticker"], agent=cmd["from_agent"], to_agent=cmd["to_agent"]))}


# --- Coverage ------------------------------------------------------------------------------------

class TickerIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str
    analyst: str = Field(description="Analyst key, e.g. 'fintech'")
    type: str = Field("public", pattern="^(public|foreign|private|index)$")
    description: str | None = None
    sec_ticker: str | None = None
    price_symbol: str | None = None
    news_query: str | None = None
    news_keywords: list[str] | None = None
    competitors: list[str] | None = None
    comps_buckets: dict[str, list[str]] | None = None
    focus_notes: str | None = None
    start_research: bool = Field(True, description="Queue the initial deep dive (~$1, runs within the daily budget)")


class TickerPatch(BaseModel):
    analyst: str | None = None
    active: bool | None = None
    fields: dict | None = None


class AnalystIn(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    name: str
    type: str = Field("company", pattern="^(company|macro|sector)$")
    focus_notes: str = ""


@app.get("/coverage")
def get_coverage():
    return list_coverage()


@app.get("/coverage/{agent_name}")
def get_agent_coverage(agent_name: str):
    """All tickers an agent covers, with current status ('/coverage Fintech')."""
    a = _agent_or_404(agent_name)
    return {**{k: a[k] for k in ("key", "name", "agent_type", "color", "focus_notes")},
            "tickers": [_ticker_card(t["symbol"]) for t in a["tickers"]]}


@app.post("/coverage/add", status_code=201, dependencies=[Depends(require_key)])
def coverage_add(body: CoverageChange):
    """Add a ticker to an agent: identifies the company, creates its files, starts tracking, queues the first deep dive."""
    if not body.agent:
        raise HTTPException(status_code=400, detail="agent is required")
    return _handle(add_coverage, body.ticker, body.agent)


@app.post("/coverage/remove", dependencies=[Depends(require_key)])
def coverage_remove(body: CoverageChange):
    """Stop tracking a ticker; its files are archived and history kept."""
    return _handle(remove_coverage, body.ticker, body.agent)


@app.post("/coverage/reassign", dependencies=[Depends(require_key)])
def coverage_reassign(body: CoverageChange):
    if not body.to_agent:
        raise HTTPException(status_code=400, detail="to_agent is required")
    return _handle(reassign_ticker, body.ticker, body.agent, body.to_agent)


@app.post("/coverage/tickers", status_code=201, dependencies=[Depends(require_key)])
def post_ticker(body: TickerIn):
    """Add a ticker with full metadata (news query, comps buckets, ...)."""
    spec = {k: v for k, v in body.model_dump().items() if v is not None and k not in ("start_research", "symbol", "analyst")}
    return _handle(add_coverage, body.symbol, body.analyst, spec)


@app.patch("/coverage/tickers/{symbol}", dependencies=[Depends(require_key)])
def patch_ticker(symbol: str, body: TickerPatch):
    _handle(update_ticker, symbol, body.analyst, body.active, body.fields)
    return get_context(symbol).__dict__


@app.post("/coverage/analysts", status_code=201, dependencies=[Depends(require_key)])
def post_analyst(body: AnalystIn):
    return _handle(add_analyst, body.model_dump())


@app.post("/coverage/reload", dependencies=[Depends(require_key)])
def reload_coverage():
    return {"initial_deep_dives_queued": bootstrap_coverage(), "coverage": list_coverage()}


@app.get("/coverage/{symbol}/files")
def get_files(symbol: str):
    _ticker_or_404(symbol)
    return list_files(symbol)


@app.get("/coverage/{symbol}/files/{filename}")
def get_file(symbol: str, filename: str):
    _ticker_or_404(symbol)
    f = read_file(symbol, filename)
    if f is None:
        raise HTTPException(status_code=404, detail=f"No {filename}. Files: {FILENAMES}")
    media = "application/json" if filename.endswith(".json") else "text/markdown"
    return Response(content=f["content"], media_type=media)


@app.get("/coverage/{symbol}/research")
def get_research(symbol: str, history: int = Query(10, ge=0, le=100), db: Session = Depends(get_db)):
    """Latest deep dive in the research format (long_form_memo, executive_summary, financial_model, ...)."""
    ctx = _ticker_or_404(symbol)
    rows = db.scalars(select(ResearchOutput).where(ResearchOutput.ticker_id == ctx.ticker_id)
                      .order_by(ResearchOutput.date.desc()).limit(history + 1)).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No deep dive yet for {ctx.symbol}")
    latest = rows[0]
    memo = latest.research_memo
    keys = ["long_form_memo", "executive_summary", "financial_model", "key_risks", "conviction_level", "reasoning"]
    return {
        **{k: memo.get(k) for k in keys},
        "meta": {"ticker": ctx.symbol, "analyst": ctx.analyst_name, "research_id": latest.id, "date": _iso(latest.date),
                 "model_used": latest.model_used, "stance": (memo.get("details") or {}).get("stance"),
                 "history": [{"research_id": r.id, "date": _iso(r.date), "conviction_level": r.conviction_level,
                              "stance": r.rating, "financial_model": r.research_memo.get("financial_model")} for r in rows[1:]]},
        "details": memo.get("details"),
    }


@app.get("/coverage/{symbol}/model.xlsx")
def get_model_xlsx(symbol: str, db: Session = Depends(get_db)):
    ctx = _ticker_or_404(symbol)
    fm = db.scalar(select(FinancialModel).where(FinancialModel.ticker_id == ctx.ticker_id).order_by(FinancialModel.date.desc()).limit(1))
    if fm is None or not (fm.assumptions or {}).get("inputs"):
        raise HTTPException(status_code=404, detail=f"No financial model yet for {ctx.symbol}")
    research = db.get(ResearchOutput, fm.research_id) if fm.research_id else None
    record = {**(research.research_memo if research else {}), "date": fm.date.date().isoformat()}
    sec = get_cached("sec", (ctx.sec_ticker or ctx.symbol).upper(), None)
    content = build_workbook(ctx.analyst_name, ctx.symbol, record, fm.assumptions["inputs"], fm.assumptions["outputs"],
                             build_track_record(ctx.ticker_id, sec))
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{ctx.symbol}_model_{fm.date.date().isoformat()}.xlsx"'})


@app.get("/coverage/{symbol}/thesis/history")
def get_thesis_history(symbol: str, limit: int = Query(20, ge=1, le=200)):
    ctx = _ticker_or_404(symbol)
    return [{**h, "date": _iso(h["date"])} for h in thesis_history(ctx.ticker_id, limit)]


# --- Operations ------------------------------------------------------------------------------------

@app.get("/jobs")
def get_jobs(limit: int = Query(50, ge=1, le=500), status: str | None = None, db: Session = Depends(get_db)):
    q = select(Job, Ticker.symbol).join(Ticker, Job.ticker_id == Ticker.id, isouter=True).order_by(Job.created_at.desc()).limit(limit)
    if status:
        q = q.where(Job.status == status)
    return [{"id": j.id, "kind": j.kind, "ticker": sym, "trigger": j.trigger, "reason": j.reason, "status": j.status,
             "created_at": _iso(j.created_at), "started_at": _iso(j.started_at), "finished_at": _iso(j.finished_at),
             "cost_usd": round(j.cost_usd or 0, 4), "error": j.error} for j, sym in db.execute(q).all()]


class BootstrapIn(BaseModel):
    budget_usd: float = Field(60.0, ge=1, le=150, description="Cap for today's total Claude spend during this run")
    workers: int = Field(3, ge=1, le=4)
    meeting: bool = True


def _bootstrap_task(body: BootstrapIn) -> None:
    from scheduler.bootstrap import run as run_bootstrap

    try:
        bootstrap_coverage()  # re-queue any ticker still missing its initial deep dive
        run_bootstrap(body.budget_usd, body.workers)
        if body.meeting:
            config.DAILY_BUDGET_USD = max(body.budget_usd, llm.spent_today() + config.MEETING_ESTIMATE_USD + 1)
            run_meeting("manual")
    except Exception:
        logger.exception("Bootstrap failed")
    finally:
        config.DAILY_BUDGET_USD = config._float("DAILY_BUDGET_USD", 5.0)  # back to the normal daily cap


@app.post("/admin/bootstrap", status_code=202, dependencies=[Depends(require_key)])
def post_bootstrap(body: BootstrapIn, background: BackgroundTasks):
    """One-time initial coverage: run every queued deep dive now under a raised cap (progress: GET /jobs)."""
    background.add_task(_bootstrap_task, body)
    return {"status": "started", "budget_usd": body.budget_usd, "poll": "/jobs?status=running", "costs": "/costs"}


class TestSmsIn(BaseModel):
    message: str | None = Field(default=None, max_length=480)


@app.post("/admin/test-sms", dependencies=[Depends(require_key)])
def post_test_sms(body: TestSmsIn | None = None):
    """Send a test text to every ALERT_PHONE_NUMBERS entry to confirm Twilio is wired up."""
    text = (body.message if body and body.message else None) or (
        f"Analyst Town test alert: SMS price alerts are live. You'll get a text when a covered ticker moves "
        f"{config.ALERT_MOVE_PCT:g}% or more in a day.")
    try:
        results = send_sms(text)
    except SmsNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not any(r["ok"] for r in results):
        raise HTTPException(status_code=502, detail={"message": "Twilio rejected every send", "results": results})
    return {"sent": sum(r["ok"] for r in results), "results": results}


@app.get("/alerts")
def get_alerts(limit: int = Query(50, ge=1, le=500)):
    """Recent SMS price alerts (sent, failed or skipped), newest first."""
    return {"enabled": config.SMS_ALERTS_ENABLED, "configured": config.sms_configured(),
            "threshold_pct": config.ALERT_MOVE_PCT, "recipients": len(config.ALERT_PHONE_NUMBERS),
            "alerts": recent_alerts(limit)}


@app.post("/price-check", dependencies=[Depends(require_key)])
def post_price_check():
    """Run the price monitor now (normally every 30 min in market hours)."""
    return price_check()


@app.post("/process-queue", dependencies=[Depends(require_key)])
def post_process_queue(background: BackgroundTasks):
    background.add_task(process_queue)
    return {"status": "started"}
