"""Daily pod meeting: analysts challenge each other and revise their trading theses (light model).

Round 1 - Challenges: each analyst reads the pod's current theses and market tape, gives a short
          market read, challenges 1-3 colleagues' calls, and notes cross-ticker implications for its own names.
Round 2 - Responses: each analyst answers the challenges aimed at its tickers (accept / partially / reject),
          revises its theses where warranted, and records generalizable lessons (fed into future prompts).
Round 3 - Minutes: a chair summarizes debates, changes and the cross-ticker dependency map; each ticker's
          meeting_notes.md gets its section.

Analysts are called one at a time (never all at once). ~13 Sonnet calls for 6 analysts, about $1.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents import llm
from agents.coverage_files import add_meeting_notes, refresh_files
from agents.registry import FOCUS_METRICS, active_contexts, get_context
from agents.thesis import TradingThesisOut, analyst_lessons, compact_research, current_thesis, latest_research, save_thesis
from data_sources.price_feed import latest_tick
from database.db import session_scope
from database.models import AgentLesson, Meeting
from utils import config

logger = logging.getLogger(__name__)

# Round 2 for a wide coverage list (Fintech has 8 tickers) plus thinking can outgrow parse_call's 16k default.
MEETING_MAX_TOKENS = 32000


# --- Schemas ----------------------------------------------------------------------------------

class Challenge(BaseModel):
    ticker: str = Field(description="A colleague's ticker (not one of yours)")
    challenge: str = Field(description="The specific weakness or blind spot in their current call")
    evidence: str = Field(description="Data, price action or a cross-ticker read-through that supports the challenge")
    suggested_change: str


class CrossReference(BaseModel):
    my_ticker: str
    other_ticker: str
    implication: str = Field(description="e.g. 'Your Stripe thesis affects my PayPal outlook because ...'")


class Contribution(BaseModel):
    market_read: str = Field(description="2-3 sentences: how you read today's tape for your coverage")
    challenges: list[Challenge] = Field(description="1-3 challenges to colleagues' calls; quality over quantity")
    cross_references: list[CrossReference]


class ChallengeResponse(BaseModel):
    ticker: str
    challenger: str
    verdict: Literal["accepted", "partially_accepted", "rejected"]
    response: str


class Revision(BaseModel):
    ticker: str
    revised: bool = Field(description="False keeps the current thesis unchanged")
    change_summary: str = Field(description="What changed and why, or why the call stands")
    thesis: TradingThesisOut | None = Field(description="The full revised thesis; null when revised is false (keeps the output short)")


class Response(BaseModel):
    responses: list[ChallengeResponse]
    revisions: list[Revision] = Field(description="Exactly one per ticker you cover")
    lessons_learned: list[str] = Field(description="0-3 generalizable lessons worth remembering in future analysis")


class TickerNote(BaseModel):
    ticker: str
    notes_md: str = Field(description="Markdown: challenges raised, the response, what changed, open questions")


class Minutes(BaseModel):
    summary_md: str = Field(description="Markdown minutes: market tone, key debates and outcomes, thesis changes, "
                                        "cross-ticker dependency map, action items")
    ticker_notes: list[TickerNote] = Field(description="One per ticker discussed")


# --- Helpers ------------------------------------------------------------------------------

def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)


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


def _analyst_system(name: str, agent_type: str, symbols: list[str], lessons: list[str]) -> str:
    return f"""You are {name} ({agent_type} analyst) in the research pod's daily meeting. You cover: {", ".join(symbols)}.
The PM cares about {", ".join(FOCUS_METRICS)} and about actionable position management (entry/exit, risk).

Meeting norms: be direct and specific; challenge ideas, not people; use numbers and named linkages; concede good points; never invent data you were not given. If nothing in a colleague's call deserves a challenge, don't manufacture one.
Lessons you have taken from earlier meetings: {_dump(lessons) if lessons else "none yet"}"""


def _meeting_context(board: dict) -> str:
    return f"<pod_board as_of=\"{datetime.now(timezone.utc).isoformat(timespec='minutes')}\">\n{_dump(board)}\n</pod_board>"


# --- Rounds -------------------------------------------------------------------------------------

def run_meeting(trigger: str = "manual") -> dict:
    llm.require_budget("meeting", config.MEETING_ESTIMATE_USD)
    with session_scope() as s:
        meeting = Meeting(trigger=trigger)
        s.add(meeting)
        s.flush()
        meeting_id = meeting.id
    try:
        result = _run(meeting_id)
    except Exception as exc:
        with session_scope() as s:
            m = s.get(Meeting, meeting_id)
            m.status, m.error, m.finished_at = "failed", f"{type(exc).__name__}: {exc}", datetime.now(timezone.utc)
        raise
    return result


def _run(meeting_id: int) -> dict:
    board = _board()
    participants = {k: v for k, v in board.items() if any(t["thesis"] for t in v["tickers"].values())}
    if len(participants) < 2:
        raise llm.AnalystError("A meeting needs at least two analysts with a trading thesis")
    context = _meeting_context(board)
    transcript = {"contributions": {}, "responses": {}}

    # Round 1: challenges
    for key, a in participants.items():
        symbols = list(a["tickers"])
        contribution, _ = llm.parse_call(
            "meeting", Contribution, _analyst_system(a["analyst"], a["agent_type"], symbols, analyst_lessons(a["analyst_id"])),
            [{"role": "user", "content": context + "\n\nRound 1: give your market read, challenge 1-3 colleagues' calls, "
                                                   "and note cross-ticker implications for your own names."}],
            max_tokens=MEETING_MAX_TOKENS,
        )
        transcript["contributions"][key] = {"analyst": a["analyst"], **contribution.model_dump()}

    # Round 2: responses and revisions
    for key, a in participants.items():
        symbols = set(a["tickers"])
        incoming = [
            {"from": c["analyst"], **ch} for other, c in transcript["contributions"].items() if other != key
            for ch in c["challenges"] if ch["ticker"].upper() in symbols
        ]
        cross = [
            {"from": c["analyst"], **x} for other, c in transcript["contributions"].items() if other != key
            for x in c["cross_references"] if x["other_ticker"].upper() in symbols or x["my_ticker"].upper() in symbols
        ]
        reads = {c["analyst"]: c["market_read"] for other, c in transcript["contributions"].items() if other != key}
        prompt = (context + f"\n\n<colleagues_market_reads>\n{_dump(reads)}\n</colleagues_market_reads>"
                  f"\n<challenges_to_you>\n{_dump(incoming) or '[]'}\n</challenges_to_you>"
                  f"\n<cross_references_involving_you>\n{_dump(cross) or '[]'}\n</cross_references_involving_you>"
                  "\n\nRound 2: respond to each challenge, then give one revision per ticker you cover (revise only "
                  "where the discussion warrants it; set thesis to null for unchanged calls), and note any "
                  "generalizable lessons.")
        response, message = llm.parse_call(
            "meeting", Response, _analyst_system(a["analyst"], a["agent_type"], sorted(symbols), analyst_lessons(a["analyst_id"])),
            [{"role": "user", "content": prompt}], max_tokens=MEETING_MAX_TOKENS,
        )
        transcript["responses"][key] = {"analyst": a["analyst"], "incoming": incoming, **response.model_dump()}
        for rev in response.revisions:
            ctx = get_context(rev.ticker)
            if ctx and ctx.symbol in symbols and rev.revised and rev.thesis:
                save_thesis(ctx, rev.thesis, "meeting", f"meeting #{meeting_id}: {rev.change_summary[:200]}", message.model)
        with session_scope() as s:
            for lesson in response.lessons_learned:
                s.add(AgentLesson(analyst_id=a["analyst_id"], lesson=lesson, meeting_id=meeting_id))

    # Round 3: minutes
    minutes, _ = llm.parse_call(
        "meeting", Minutes,
        "You chair the research pod's daily meeting and write crisp minutes for the PM: what was debated, who "
        "changed their mind and why, which cross-ticker dependencies matter, and open action items. Markdown, no filler.",
        [{"role": "user", "content": f"<transcript>\n{_dump(transcript)}\n</transcript>\n\nWrite the minutes and one note per ticker discussed."}], max_tokens=MEETING_MAX_TOKENS,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for note in minutes.ticker_notes:
        ctx = get_context(note.ticker)
        if ctx:
            add_meeting_notes(ctx.symbol, f"## Meeting #{meeting_id}, {stamp}\n\n{note.notes_md}")
            refresh_files(ctx.symbol, ["trading_thesis.md"])
    with session_scope() as s:
        m = s.get(Meeting, meeting_id)
        m.status, m.finished_at = "done", datetime.now(timezone.utc)
        m.minutes_md = f"# Pod meeting #{meeting_id}, {stamp}\n\n{minutes.summary_md}"
        m.transcript = transcript
    revised = [r["ticker"] for resp in transcript["responses"].values() for r in resp["revisions"] if r["revised"]]
    return {"meeting_id": meeting_id, "participants": [a["analyst"] for a in participants.values()],
            "revised_theses": revised, "minutes_md": f"# Pod meeting #{meeting_id}, {stamp}\n\n{minutes.summary_md}"}


def latest_minutes() -> dict | None:
    with session_scope() as s:
        m = s.scalar(select(Meeting).order_by(Meeting.started_at.desc()).limit(1))
        if m is None:
            return None
        return {"meeting_id": m.id, "status": m.status, "trigger": m.trigger, "started_at": m.started_at.isoformat(),
                "finished_at": m.finished_at.isoformat() if m.finished_at else None, "minutes_md": m.minutes_md, "error": m.error}
