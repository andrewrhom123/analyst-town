"""One-on-one office conversations: talk to an analyst about anything, not just one ticker (light model).

"What's the most interesting stock you're covering right now?", "Where do macro headwinds hit your coverage?",
"What trade would you do with unlimited capital?", "Walk me through your thesis on DASH". The analyst answers
from its whole book (theses, what the market is missing, trade structures, latest deep dives, headlines), the
latest town hall memo and the PM's research charter. Every conversation is stored with its transcript, the key
insights it produced and any trade ideas. A new conversation starts after IDLE_GAP of silence.
"""

import json
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents import llm
from agents.meeting import _board, _desk_view, headlines, macro_indicators
from agents.philosophy import framework_block
from agents.registry import FOCUS_METRICS, analyst_info
from agents.thesis import analyst_lessons
from database.db import session_scope
from database.models import Memo, OfficeConversation
from utils import config

IDLE_GAP = timedelta(hours=2)
HISTORY_TURNS = 12  # prior exchanges sent back to the model


class TradeIdea(BaseModel):
    long: str | None = Field(description="Long leg ticker, or null")
    short: str | None = Field(description="Short / hedge leg ticker, or null")
    rationale: str


class OfficeReply(BaseModel):
    reply: str = Field(description="Your answer, conversational and specific, 1-4 short paragraphs. It is shown and also read "
                                   "aloud, so keep formatting light (no tables).")
    insights: list[str] = Field(description="0-2 genuinely new key insights from this exchange worth keeping (not repeats of earlier ones)")
    trade_ideas: list[TradeIdea] = Field(description="Trade ideas you proposed in this reply (may be empty)")
    title: str = Field(description="5-8 word title for the conversation so far")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tz


def serialize(c: OfficeConversation) -> dict:
    return {"conversation_id": c.id, "analyst_key": c.analyst_key, "title": c.title, "started_at": _aware(c.started_at).isoformat(),
            "updated_at": _aware(c.updated_at).isoformat(), "messages": c.messages or [], "insights": c.insights or [],
            "trade_ideas": c.trade_ideas or []}


def _system(info: dict, focus: str | None) -> str:
    board = _board().get(info["key"]) or {"analyst": info["name"], "agent_type": info["agent_type"], "tickers": {}}
    book = {"your_names": _desk_view(board), "headlines": headlines(list(board["tickers"]), per_ticker=2)}
    if info["agent_type"] == "macro":
        book["macro_indicators"] = macro_indicators()
    with session_scope() as s:
        memo = s.scalar(select(Memo).where(Memo.kind == "town_hall").order_by(Memo.created_at.desc()).limit(1))
        memo_md = memo.memo_md[:3500] if memo else None
    focus_line = f"The PM currently has {focus} up on your screens; it may or may not be what they ask about.\n" if focus else ""
    return f"""You are {info['name']} ({info['agent_type']} analyst) in your office. The PM (Andrew) has walked in for a one-on-one. You cover: {", ".join(board['tickers']) or "nothing yet"}.
They care about {", ".join(FOCUS_METRICS)}. The conversation can be about one name, your whole coverage, the market, or anything a sharp trader would discuss.
{focus_line}
{framework_block()}

Talk like a trusted colleague: lead with your actual view, back it with the data below, say what the market is missing and how you would structure the trade. Be candid about where you have no edge. Never invent numbers that are not in your book.
Lessons you have taken from meetings: {json.dumps(analyst_lessons(board.get('analyst_id')) if board.get('analyst_id') else [], default=str)}

<your_book>
{json.dumps(book, indent=1, default=str)}
</your_book>
""" + (f"\n<latest_town_hall_memo>\n{memo_md}\n</latest_town_hall_memo>\n" if memo_md else "")


def _conversation(s, agent_key: str, conversation_id: int | None, new: bool) -> OfficeConversation:
    if conversation_id:
        c = s.get(OfficeConversation, conversation_id)
        if c is None or c.analyst_key != agent_key:
            raise llm.AnalystError("That conversation belongs to another office")
        return c
    if not new:
        c = s.scalar(select(OfficeConversation).where(OfficeConversation.analyst_key == agent_key)
                     .order_by(OfficeConversation.updated_at.desc()).limit(1))
        if c is not None and _now() - _aware(c.updated_at) < IDLE_GAP:
            return c
    c = OfficeConversation(analyst_key=agent_key, messages=[], insights=[], trade_ideas=[])
    s.add(c)
    s.flush()
    return c


def say(agent_key: str, text: str, conversation_id: int | None = None, focus: str | None = None, new: bool = False) -> dict:
    """The PM speaks in the analyst's office; returns the updated conversation with the analyst's reply."""
    info = analyst_info(agent_key)
    llm.require_budget("ask", 0.05)
    with session_scope() as s:
        c = _conversation(s, info["key"], conversation_id, new)
        cid, history = c.id, list(c.messages or [])
    messages = []
    for m in history[-HISTORY_TURNS * 2:]:
        messages.append({"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]})
    messages.append({"role": "user", "content": text})
    out, _ = llm.parse_call("office", OfficeReply, _system(info, focus), messages,
                            effort=config.ASK_EFFORT, max_tokens=config.QUESTION_MAX_TOKENS)
    now = _now().isoformat()
    ideas = [i.model_dump() for i in out.trade_ideas]
    with session_scope() as s:
        c = s.get(OfficeConversation, cid)
        c.messages = [*(c.messages or []), {"role": "user", "text": text, "focus": focus, "ts": now},
                      {"role": "agent", "text": out.reply, "insights": out.insights, "trade_ideas": ideas, "ts": now}]
        known = {i.lower() for i in c.insights or []}
        c.insights = [*(c.insights or []), *[i for i in out.insights if i.lower() not in known]]
        c.trade_ideas = [*(c.trade_ideas or []), *ideas]
        c.title = out.title or c.title
        c.updated_at = _now()
        return serialize(c)


def latest(agent_key: str) -> dict | None:
    info = analyst_info(agent_key)
    with session_scope() as s:
        c = s.scalar(select(OfficeConversation).where(OfficeConversation.analyst_key == info["key"])
                     .order_by(OfficeConversation.updated_at.desc()).limit(1))
        return serialize(c) if c else None


def get(conversation_id: int) -> dict | None:
    with session_scope() as s:
        c = s.get(OfficeConversation, conversation_id)
        return serialize(c) if c else None


def history(agent_key: str, limit: int = 20) -> list[dict]:
    info = analyst_info(agent_key)
    with session_scope() as s:
        rows = s.scalars(select(OfficeConversation).where(OfficeConversation.analyst_key == info["key"])
                         .order_by(OfficeConversation.updated_at.desc()).limit(limit)).all()
        return [{"conversation_id": c.id, "title": c.title, "started_at": _aware(c.started_at).isoformat(),
                 "updated_at": _aware(c.updated_at).isoformat(), "turns": len(c.messages or []) // 2,
                 "insights": len(c.insights or [])} for c in rows]
