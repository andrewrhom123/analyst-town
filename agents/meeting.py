"""Daily Research Town Hall (4:30 PM ET, or on demand): peer research, not ratings (light model).

1. Rundowns  - in order Macro -> AI -> Internet Platforms -> Fintech, each analyst talks ~2 minutes about what
               it is seeing TODAY: observations, thesis updates, macro shifts hitting its coverage.
2. Pitches   - an analyst with a new trade idea pitches it with its rundown: narrative, business-model trade-offs,
               what the market is missing, supporting data, catalysts, news links, charts, conviction, and a
               structure that isolates the idea (pair / hedge leg to strip out beta).
3. Discussion - every analyst weighs in on colleagues' pitches and rundowns with its own data: agree, disagree
               or build. Then each pitcher answers the room and re-states its conviction.
4. Memo      - the chair writes the research memo (rundowns, pitches and how the debate came out, what the
               market is missing, action items); each discussed ticker's meeting_notes.md gets its section.

Every turn is saved the moment it is spoken (meetings.transcript["turns"]) so the boardroom can play it live;
rundowns, pitches and the memo also land in their own tables. 2N+1 to 3N+1 Sonnet calls, about $1.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents import llm
from agents.coverage_files import add_meeting_notes
from agents.philosophy import framework_block
from agents.registry import FOCUS_METRICS, active_contexts, get_context
from agents.relative_value import desk_relative_value
from agents.thesis import TradingThesisOut, analyst_lessons, compact_research, current_thesis, latest_research
from data_sources.cache import get_cached
from data_sources.price_feed import latest_tick
from database.db import session_scope
from database.models import Meeting, Memo, Pitch, Rundown
from utils import config

logger = logging.getLogger(__name__)

# A meeting runs in the web process; if the server restarts mid-meeting (e.g. a redeploy), its row would stay
# "running" forever and block new meetings. Anything still running after this long is marked failed.
STALE_AFTER = timedelta(minutes=20)
MEETING_MAX_TOKENS = 32000  # generous ceiling for rundown + pitch turns (calls stream, so this is safe)
SPEAKING_ORDER = ["macro", "ai", "internet", "fintech"]  # Macro -> AI -> Internet Platforms -> Fintech
MACRO_SERIES = ["fed_funds_rate", "treasury_2y", "treasury_10y", "cpi", "unemployment"]
SPOKEN = "Your words are read aloud to the room: speak naturally in first person, no markdown, no lists."


# --- Schemas ----------------------------------------------------------------------------------

class Revision(BaseModel):
    """A thesis re-statement (used by strategy sessions)."""
    ticker: str
    revised: bool = Field(description="False keeps the current thesis unchanged")
    change_summary: str = Field(description="What changed and why, or why the call stands")
    thesis: TradingThesisOut | None = Field(description="The full revised thesis; null when revised is false (keeps the output short)")


class NewsLink(BaseModel):
    title: str
    url: str = Field(description="Exactly as given in your headlines")


class CatalystItem(BaseModel):
    event: str
    timing: str


class PitchOut(BaseModel):
    title: str = Field(description="One line, e.g. 'Long DASH / short UBER: the market is missing ad take-rate'")
    long_ticker: str | None = Field(description="The long leg, or null for an outright short")
    short_ticker: str | None = Field(description="The short / hedge leg, or null for an outright long")
    structure: Literal["pair", "outright_long", "outright_short", "hedged"]
    spoken: str = Field(description="The pitch as you say it, 120-200 words: narrative, why now, the edge. " + SPOKEN)
    business_tradeoffs: str = Field(description="What the business model trades off, and why that matters here")
    market_missing: str = Field(description="What the market is not seeing, and why it is mispriced now")
    data_points: list[str] = Field(description="2-5 supporting data points with numbers and sources")
    catalysts: list[CatalystItem] = Field(description="Near-term events that close the gap")
    conviction: int = Field(description="1-10: how much real edge we have")
    news: list[NewsLink] = Field(description="0-3 supporting links chosen from your headlines")
    chart_tickers: list[str] = Field(description="1-2 tickers whose price chart supports the pitch")


class TickerUpdate(BaseModel):
    ticker: str
    update: str


class RundownOut(BaseModel):
    spoken: str = Field(description="Your rundown, about two minutes (200-300 words): what you are seeing TODAY across "
                                    "your coverage, thesis updates, macro shifts. " + SPOKEN)
    observations: list[str] = Field(description="2-4 key observations, with numbers")
    thesis_updates: list[TickerUpdate] = Field(description="Names whose view moved today and how (may be empty)")
    macro_shifts: list[str] = Field(description="Macro shifts affecting your coverage (may be empty)")
    pitch: PitchOut | None = Field(description="A new trade idea ONLY if you have real edge today; otherwise null")


class Comment(BaseModel):
    topic: str = Field(description="The topic id you are responding to, e.g. 'P1' or 'R-macro'")
    stance: Literal["agree", "disagree", "build"]
    spoken: str = Field(description="2-4 sentences bringing your own data. " + SPOKEN)
    evidence: list[str] = Field(description="The data you are bringing, with numbers")


class DiscussionOut(BaseModel):
    comments: list[Comment] = Field(description="1-3 comments on colleagues' topics, pitches first; quality over quantity")


class PitchResponseOut(BaseModel):
    spoken: str = Field(description="2-4 sentences answering the room: concede good points, defend with data. " + SPOKEN)
    conviction: int = Field(description="Your conviction after the debate, 1-10")
    adjustments: str = Field(description="How the trade changes (sizing, hedge, levels), or 'none'")


class TickerNote(BaseModel):
    ticker: str
    notes_md: str = Field(description="Markdown: what was said about this name, pitches, debate, open questions")


class TownHallMemo(BaseModel):
    title: str
    memo_md: str = Field(description="Markdown research memo for the PM: ## Market tone, ## Rundowns, ## Pitches and "
                                     "debate (trade, structure, edge, catalysts, where the room came out), ## What the "
                                     "market is missing, ## Action items")
    spoken_summary: str = Field(description="3-4 sentences the chair reads aloud to close. " + SPOKEN)
    ticker_notes: list[TickerNote] = Field(description="One per ticker discussed")


# --- Helpers ------------------------------------------------------------------------------

def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _board() -> dict:
    """The pod's current state: per analyst, per ticker: price, thesis, last deep-dive summary."""
    board = {}
    for ctx in active_contexts():
        th = current_thesis(ctx.ticker_id)
        tick = latest_tick(ctx.price_symbol) if ctx.price_symbol else None
        research = compact_research(latest_research(ctx.ticker_id))
        entry = board.setdefault(ctx.analyst_key, {"analyst": ctx.analyst_name, "agent_type": ctx.agent_type,
                                                   "analyst_id": ctx.analyst_id, "tickers": {}})
        entry["tickers"][ctx.symbol] = {
            "name": ctx.name,
            "price": tick,
            "thesis": th["thesis"] if th else None,
            "thesis_updated": th["created_at"].isoformat() if th else None,
            "deep_dive": {k: research[k] for k in ("date", "executive_summary", "financial_model")} if research else None,
        }
    return board


def speaking_order(board: dict) -> list[str]:
    """Macro -> AI -> Internet Platforms -> Fintech, then any other desks."""
    def rank(key: str):
        if key in SPEAKING_ORDER:
            return (SPEAKING_ORDER.index(key), key)
        return (0 if board[key]["agent_type"] == "macro" else len(SPEAKING_ORDER), key)
    return sorted(board, key=rank)


def headlines(symbols: list[str], per_ticker: int = 4) -> dict:
    out = {}
    for sym in symbols:
        news = get_cached("news", sym, None) or {}
        out[sym] = [{k: a.get(k) for k in ("published_at", "title", "url", "source", "sentiment")}
                    for a in (news.get("articles") or [])[:per_ticker]]
    return out


def macro_indicators() -> dict:
    out = {}
    for name in MACRO_SERIES:
        cached = get_cached("macro", name, None)
        if cached:
            out[name] = {"name": cached.get("name"), "unit": cached.get("unit"), "recent": (cached.get("series") or [])[:4]}
    return out


def _desk_view(a: dict) -> dict:
    """What an analyst brings to the room about its own names (compact)."""
    view = {}
    for sym, t in a["tickers"].items():
        th = t["thesis"] or {}
        view[sym] = {"name": t["name"], "price": t["price"],
                     "thesis": {k: th.get(k) for k in ("position", "headline", "conviction_level", "market_missing",
                                                       "trade_structure", "entry_zone_low", "entry_zone_high",
                                                       "target_price", "stop_loss", "next_catalysts")} if th else None,
                     "deep_dive_summary": (t["deep_dive"] or {}).get("executive_summary")}
    return view


def _system(a: dict, lessons: list[str]) -> str:
    return f"""You are {a['analyst']} ({a['agent_type']} analyst) at the research pod's daily town hall in the boardroom. You cover: {", ".join(a['tickers'])}.
The PM (Andrew) may be listening; he cares about {", ".join(FOCUS_METRICS)}.

{framework_block()}

Town hall norms: this is peer research, not a hierarchy. Bring data, name linkages, agree or disagree on the merits, concede good points, and hunt for what the market is missing. Debate trades, not ratings. Never invent data you were not given.
Lessons you have taken from earlier meetings: {_dump(lessons) if lessons else "none yet"}"""


def _turn(meeting_id: int, rnd: str, agent_key: str, speaker: str, text: str, meta: dict | None = None) -> dict:
    """Append one spoken turn to the live transcript (the boardroom polls and plays these as they land)."""
    with session_scope() as s:
        m = s.get(Meeting, meeting_id)
        transcript = dict(m.transcript or {})
        turns = list(transcript.get("turns") or [])
        turn = {"id": len(turns) + 1, "round": rnd, "agent_key": agent_key, "speaker": speaker, "text": text,
                "meta": meta or {}, "ts": _now().isoformat()}
        turns.append(turn)
        transcript["turns"] = turns
        m.transcript = transcript  # reassign so the JSON change is persisted
        return turn


def _progress(meeting_id: int, text: str | None) -> None:
    with session_scope() as s:
        m = s.get(Meeting, meeting_id)
        m.transcript = {**(m.transcript or {}), "progress": text}


# --- Run ------------------------------------------------------------------------------------

def run_meeting(trigger: str = "manual") -> dict:
    llm.require_budget("meeting", config.MEETING_ESTIMATE_USD)
    with session_scope() as s:
        meeting = Meeting(trigger=trigger, mode="daily", transcript={"turns": [], "progress": "Macro is up first"})
        s.add(meeting)
        s.flush()
        meeting_id = meeting.id
    try:
        result = _run(meeting_id)
    except Exception as exc:
        with session_scope() as s:
            m = s.get(Meeting, meeting_id)
            m.status, m.error, m.finished_at = "failed", f"{type(exc).__name__}: {exc}", _now()
            m.transcript = {**(m.transcript or {}), "progress": None}
        raise
    return result


def _run(meeting_id: int) -> dict:
    board = _board()
    order = [k for k in speaking_order(board) if board[k]["tickers"]]
    if len(order) < 2:
        raise llm.AnalystError("A town hall needs at least two analysts with coverage")
    rundowns, pitches = [], []  # pitches: {"id": "P1", "db_id", "agent_key", "analyst", "pitch": PitchOut, "comments": []}

    # 1-2. Rundowns, with any pitches
    for key in order:
        a = board[key]
        _progress(meeting_id, f"{a['analyst']} is giving the rundown")
        payload = {"your_names": _desk_view(a), "headlines": headlines(list(a["tickers"])),
                   "relative_value": desk_relative_value(list(a["tickers"])),
                   "colleagues_so_far": [{"analyst": r["analyst"], "rundown": r["spoken"], "pitch": r.get("pitch_title")} for r in rundowns]}
        if a["agent_type"] == "macro":
            payload["macro_indicators"] = macro_indicators()
        out, _ = llm.parse_call(
            "meeting", RundownOut, _system(a, analyst_lessons(a["analyst_id"])),
            [{"role": "user", "content": f"<town_hall as_of=\"{_now().isoformat(timespec='minutes')}\">\n{_dump(payload)}\n</town_hall>\n\n"
              "Give your rundown. Pitch a new trade only if you have real edge today."}],
            max_tokens=MEETING_MAX_TOKENS,
        )
        data = {"observations": out.observations, "thesis_updates": [u.model_dump() for u in out.thesis_updates], "macro_shifts": out.macro_shifts}
        with session_scope() as s:
            s.add(Rundown(meeting_id=meeting_id, analyst_key=key, analyst_name=a["analyst"], spoken=out.spoken, data=data))
        _turn(meeting_id, "rundown", key, a["analyst"], out.spoken, data)
        entry = {"analyst": a["analyst"], "spoken": out.spoken}
        if out.pitch:
            p = out.pitch
            allowed = {h["url"] for hs in payload["headlines"].values() for h in hs if h.get("url")}
            p.news = [n for n in p.news if n.url in allowed]  # links must come from real headlines
            pid = f"P{len(pitches) + 1}"
            with session_scope() as s:
                row = Pitch(meeting_id=meeting_id, analyst_key=key, analyst_name=a["analyst"], title=p.title,
                            long_ticker=p.long_ticker, short_ticker=p.short_ticker, structure=p.structure,
                            conviction=max(1, min(10, p.conviction)), data=p.model_dump(), discussion=[])
                s.add(row)
                s.flush()
                db_id = row.id
            pitches.append({"id": pid, "db_id": db_id, "agent_key": key, "analyst": a["analyst"], "pitch": p, "comments": []})
            entry["pitch_title"] = p.title
            _turn(meeting_id, "pitch", key, a["analyst"], p.spoken, {"pitch_id": pid, "db_id": db_id, **p.model_dump()})
        rundowns.append({"key": key, **entry})

    # 3. Discussion: everyone weighs in on colleagues' pitches and rundowns
    topics = [{"id": p["id"], "type": "pitch", "by": p["analyst"], "title": p["pitch"].title, "pitch": p["pitch"].model_dump(exclude={"spoken"})}
              for p in pitches] + [{"id": f"R-{r['key']}", "type": "rundown", "by": r["analyst"], "rundown": r["spoken"]} for r in rundowns]
    by_id = {p["id"]: p for p in pitches}
    for key in order:
        a = board[key]
        others = [t for t in topics if t["by"] != a["analyst"]]
        if not others:
            continue
        _progress(meeting_id, f"{a['analyst']} is weighing in")
        out, _ = llm.parse_call(
            "meeting", DiscussionOut, _system(a, analyst_lessons(a["analyst_id"])),
            [{"role": "user", "content": f"<your_names>\n{_dump(_desk_view(a))}\n</your_names>\n<topics>\n{_dump(others)}\n</topics>\n\n"
              "Weigh in on 1-3 of these with your own data, pitches first. Agree, disagree or build; what is the market missing?"}],
        )
        valid = {t["id"]: t for t in others}
        for c in out.comments:
            topic = valid.get(c.topic.strip())
            if topic is None:
                continue
            meta = {"topic": topic["id"], "topic_title": topic.get("title") or f"{topic['by']}'s rundown",
                    "stance": c.stance, "evidence": c.evidence}
            _turn(meeting_id, "discussion", key, a["analyst"], c.spoken, meta)
            if topic["id"] in by_id:
                by_id[topic["id"]]["comments"].append({"agent_key": key, "analyst": a["analyst"], "stance": c.stance,
                                                       "spoken": c.spoken, "evidence": c.evidence})

    # 3b. Pitchers answer the room
    for p in pitches:
        if not p["comments"]:
            continue
        a = board[p["agent_key"]]
        _progress(meeting_id, f"{a['analyst']} is answering the room")
        out, _ = llm.parse_call(
            "meeting", PitchResponseOut, _system(a, analyst_lessons(a["analyst_id"])),
            [{"role": "user", "content": f"<your_pitch>\n{_dump(p['pitch'].model_dump())}\n</your_pitch>\n"
              f"<the_room>\n{_dump(p['comments'])}\n</the_room>\n\nAnswer the room on your pitch."}],
        )
        response = {"agent_key": p["agent_key"], "analyst": a["analyst"], "role": "pitcher", "spoken": out.spoken,
                    "conviction": max(1, min(10, out.conviction)), "adjustments": out.adjustments}
        with session_scope() as s:
            row = s.get(Pitch, p["db_id"])
            row.discussion = [*p["comments"], response]
            row.conviction = response["conviction"]
        p["response"] = response
        _turn(meeting_id, "response", p["agent_key"], a["analyst"], out.spoken,
              {"pitch_id": p["id"], "conviction": response["conviction"], "adjustments": out.adjustments})
    for p in pitches:
        if p["comments"] and "response" not in p:  # no answer recorded; still keep the room's comments
            with session_scope() as s:
                s.get(Pitch, p["db_id"]).discussion = p["comments"]

    # 4. Research memo
    _progress(meeting_id, "The chair is writing the research memo")
    with session_scope() as s:
        turns = (s.get(Meeting, meeting_id).transcript or {}).get("turns", [])
    memo, _ = llm.parse_call(
        "meeting", TownHallMemo,
        "You chair the research pod's daily town hall and write the research memo the PM reviews right after. Capture "
        "each rundown's signal, every pitch (trade, structure, edge, catalysts, conviction before and after) and where the "
        "debate came out, what the market is missing, and action items. Trades and narrative, never ratings. No filler.\n\n"
        + framework_block(),
        [{"role": "user", "content": f"<town_hall_turns>\n{_dump([{k: t[k] for k in ('round', 'speaker', 'text', 'meta')} for t in turns])}\n"
          "</town_hall_turns>\n\nWrite the research memo and one note per ticker discussed."}],
        max_tokens=MEETING_MAX_TOKENS,
    )
    stamp = _now().strftime("%Y-%m-%d %H:%M UTC")
    minutes_md = f"# {memo.title}\n\n*Daily research town hall #{meeting_id}, {stamp}*\n\n{memo.memo_md}"
    for note in memo.ticker_notes:
        ctx = get_context(note.ticker)
        if ctx:
            add_meeting_notes(ctx.symbol, f"## Town hall #{meeting_id}, {stamp}\n\n{note.notes_md}")
    with session_scope() as s:
        s.add(Memo(kind="town_hall", meeting_id=meeting_id, title=memo.title, memo_md=minutes_md))
    _turn(meeting_id, "memo", "chair", "Chair", memo.spoken_summary, {"title": memo.title})
    with session_scope() as s:
        m = s.get(Meeting, meeting_id)
        m.status, m.finished_at, m.minutes_md = "done", _now(), minutes_md
        m.transcript = {**(m.transcript or {}), "progress": None}
    return {"meeting_id": meeting_id, "participants": [board[k]["analyst"] for k in order],
            "pitches": [p["pitch"].title for p in pitches], "revised_theses": [], "minutes_md": minutes_md}


# --- Reading --------------------------------------------------------------------------------

def expire_stale_meetings(s) -> None:
    """Mark meetings that have been 'running' longer than STALE_AFTER as failed (their process died)."""
    now = _now()
    for m in s.scalars(select(Meeting).where(Meeting.status == "running")):
        started = m.started_at if m.started_at.tzinfo else m.started_at.replace(tzinfo=timezone.utc)  # SQLite drops tz
        if now - started > STALE_AFTER:
            m.status, m.finished_at = "failed", now
            m.error = ("Interrupted: no result after 20 minutes, so the server probably restarted mid-meeting "
                       "(e.g. a redeploy). Start a new one.")


def meeting_in_progress() -> bool:
    with session_scope() as s:
        expire_stale_meetings(s)
        s.flush()
        return s.scalar(select(Meeting.id).where(Meeting.status == "running").limit(1)) is not None


def _iso(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _detail(s, m: Meeting) -> dict:
    pitches = s.scalars(select(Pitch).where(Pitch.meeting_id == m.id).order_by(Pitch.id)).all()
    transcript = m.transcript or {}
    return {"meeting_id": m.id, "mode": m.mode or "daily", "status": m.status, "trigger": m.trigger,
            "started_at": _iso(m.started_at), "finished_at": _iso(m.finished_at), "minutes_md": m.minutes_md, "error": m.error,
            "progress": transcript.get("progress"), "turns": transcript.get("turns", []),
            "pitches": [{"id": p.id, "analyst_key": p.analyst_key, "analyst": p.analyst_name, "title": p.title,
                         "long_ticker": p.long_ticker, "short_ticker": p.short_ticker, "structure": p.structure,
                         "conviction": p.conviction, "data": p.data, "discussion": p.discussion} for p in pitches]}


def latest_minutes() -> dict | None:
    with session_scope() as s:
        expire_stale_meetings(s)
        s.flush()
        m = s.scalar(select(Meeting).order_by(Meeting.started_at.desc()).limit(1))
        return _detail(s, m) if m else None


def meeting_detail(meeting_id: int) -> dict | None:
    with session_scope() as s:
        m = s.get(Meeting, meeting_id)
        return _detail(s, m) if m else None
