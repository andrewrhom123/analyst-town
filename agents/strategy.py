"""Ad-hoc town halls, started by the PM (light model). Two modes:

STRATEGY SESSION (mode "strategy"): reset the pod's research direction. The memo becomes the Research Charter
that every agent prompt follows until the next strategy session replaces it.
RESEARCH CONVERSATION (mode "research"): broad questions ("where's the biggest mispricing?"); every analyst
answers in turn with observations, pitches and agreement/disagreement with colleagues; wrapping up writes a
research memo. Pitches land in the pitches table.

Strategy flow (each step runs in the background; every agent turn is stored the moment it is spoken):
  1. Context  - the macro strategist opens with the market backdrop: rates, the Fed, sentiment, beta.
  2. Strategy - the PM types or speaks a strategy. Every analyst replies: "aligned" (with what it means
                for their names) or "question" (a respectful challenge citing the conflicting data).
  3. Discussion - the PM answers; only analysts with open questions reply again. Repeat as needed.
  4. Memo     - the chair writes the strategy memo; each analyst signs off (noting any reservations) and
                re-states every one of its theses under the new strategy, revising where it changes the call.

Everything is captured in strategy_sessions. Cost: 2-3 Sonnet calls per analyst plus two, about $1.
Data is read from caches only (no market-data API calls).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents import llm
from agents.coverage_files import refresh_files
from agents.meeting import PitchOut, Revision, _board, headlines, speaking_order
from agents.philosophy import framework_block
from agents.registry import FOCUS_METRICS, get_context
from agents.thesis import save_thesis
from data_sources.cache import get_cached
from database.db import session_scope
from database.models import Memo, Pitch, ResearchCharter, StrategySession
from utils import config

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=12)  # a step still "thinking" after this long was interrupted (e.g. a redeploy)
SIGNOFF_MAX_TOKENS = 32000  # one revision per ticker for 8-ticker desks
MACRO_SERIES = ["fed_funds_rate", "treasury_2y", "treasury_10y", "cpi", "unemployment"]
SPOKEN = "Your words are read aloud: speak naturally in first person to the PM, no markdown, no lists."


class SessionError(Exception):
    pass


# --- Schemas ----------------------------------------------------------------------------------

class MarketContext(BaseModel):
    headline: str = Field(description="One line: the market regime in a nutshell")
    spoken: str = Field(description="Your opening to the room, 4-6 conversational sentences covering rates, the Fed, "
                                    "sentiment and beta, ending by inviting the PM to set the strategy. " + SPOKEN)
    rates: str = Field(description="Rates and the curve, with numbers")
    fed: str = Field(description="Fed policy path and what is priced")
    sentiment: str = Field(description="Risk appetite and news-flow tone across the pod's names")
    beta: str = Field(description="Which covered names are high/low beta and what that means for positioning")
    watch_items: list[str] = Field(description="2-4 things that could change the backdrop")


class TickerImplication(BaseModel):
    ticker: str
    implication: str = Field(description="What the strategy means for this name's position")


class AgentReply(BaseModel):
    stance: Literal["aligned", "question"] = Field(description="question only if specific data conflicts with the strategy")
    spoken: str = Field(description="2-4 sentences. Aligned: acknowledge and say what you will do with your names. "
                                    "Question: respectfully name the conflicting data and ask one clear question. " + SPOKEN)
    conflicts: list[str] = Field(description="Specific data points, with numbers, that conflict with the strategy; empty when aligned")
    implications: list[TickerImplication] = Field(description="Your names most affected by the strategy (1-4)")


class StrategyMemo(BaseModel):
    title: str = Field(description="The research charter's title")
    memo_md: str = Field(description="Markdown research charter for the PM and the pod: ## Strategy, ## Market context, "
                                     "## Research directives, ## Positioning by desk, ## Guardrails and risks, ## Dissent and open questions")
    directives: list[str] = Field(description="3-7 crisp directives every analyst will apply to all future research, "
                                              "e.g. 'Prefer idiosyncratic long/short pairs that net out sector beta'")
    spoken_summary: str = Field(description="3-4 sentences the chair reads aloud to close the session. " + SPOKEN)


class ResearchReply(BaseModel):
    kind: Literal["observation", "pitch", "agree", "disagree"] = Field(
        description="pitch only with a concrete trade; agree/disagree when reacting mainly to a colleague")
    spoken: str = Field(description="2-5 sentences answering the PM, reacting to colleagues where useful. " + SPOKEN)
    references: list[str] = Field(description="The data behind what you said, with numbers")
    pitch: PitchOut | None = Field(description="Your trade idea when kind is pitch; otherwise null")


class ResearchMemo(BaseModel):
    title: str
    memo_md: str = Field(description="Markdown research memo: ## Questions asked, ## What the pod sees, ## Trade ideas "
                                     "(structure, edge, catalysts, conviction, pushback), ## What the market is missing, ## Follow-ups")
    spoken_summary: str = Field(description="3-4 sentences the chair reads aloud to close. " + SPOKEN)


class SignOff(BaseModel):
    signed: bool = Field(description="True to sign the memo (you can sign and still note reservations)")
    statement: str = Field(description="One or two spoken sentences signing off. " + SPOKEN)
    reservations: str = Field(description="Any remaining reservation, or an empty string")
    revisions: list[Revision] = Field(description="Exactly one per ticker you cover, under the new strategy")


# --- Storage helpers --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt  # SQLite drops tz


def _update(session_id: int, **fields) -> None:
    with session_scope() as s:
        row = s.get(StrategySession, session_id)
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_at = _now()


def _say(session_id: int, role: str, kind: str, text: str, *, agent_key: str | None = None,
         speaker: str = "", meta: dict | None = None) -> dict:
    """Append one turn to the session transcript."""
    with session_scope() as s:
        row = s.get(StrategySession, session_id)
        msg = {"id": len(row.messages or []) + 1, "role": role, "kind": kind, "agent_key": agent_key,
               "speaker": speaker, "text": text, "meta": meta or {}, "ts": _now().isoformat()}
        row.messages = [*(row.messages or []), msg]  # reassign so the JSON change is persisted
        row.updated_at = _now()
        return msg


def serialize(row: StrategySession) -> dict:
    return {
        "session_id": row.id, "mode": row.mode or "strategy", "status": row.status, "phase": row.phase, "progress": row.progress,
        "started_at": _aware(row.started_at).isoformat(), "finished_at": _aware(row.finished_at).isoformat() if row.finished_at else None,
        "strategy": row.strategy, "messages": row.messages or [], "market_context": row.market_context or {},
        "memo_title": row.memo_title, "memo_md": row.memo_md, "signoffs": row.signoffs or [], "revisions": row.revisions or [],
        "open_questions": open_questions(row.messages or []) if (row.mode or "strategy") == "strategy" else [],
    }


def _expire_stale(s) -> None:
    for row in s.scalars(select(StrategySession).where(StrategySession.status == "thinking")):
        if _now() - _aware(row.updated_at) > STALE_AFTER:
            row.status, row.progress, row.updated_at = "awaiting_user", None, _now()
            row.messages = [*(row.messages or []), {
                "id": len(row.messages or []) + 1, "role": "system", "kind": "error", "agent_key": None, "speaker": "",
                "text": "That step was interrupted (the server probably restarted). Send your message again or generate the memo.",
                "meta": {}, "ts": _now().isoformat()}]


def get_session(session_id: int) -> dict | None:
    with session_scope() as s:
        _expire_stale(s)
        row = s.get(StrategySession, session_id)
        return serialize(row) if row else None


def latest_session() -> dict | None:
    with session_scope() as s:
        _expire_stale(s)
        row = s.scalar(select(StrategySession).order_by(StrategySession.started_at.desc()).limit(1))
        return serialize(row) if row else None


def list_sessions(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(StrategySession).order_by(StrategySession.started_at.desc()).limit(limit)).all()
        return [{"session_id": r.id, "mode": r.mode or "strategy", "status": r.status, "phase": r.phase, "started_at": _aware(r.started_at).isoformat(),
                 "strategy": r.strategy[:200], "memo_title": r.memo_title, "turns": len(r.messages or [])} for r in rows]


def open_questions(messages: list[dict]) -> list[str]:
    """Agent keys whose latest reply is still a question."""
    latest: dict[str, str] = {}
    for m in messages:
        if m["role"] == "agent" and m["kind"] in ("aligned", "question"):
            latest[m["agent_key"]] = m["kind"]
    return [k for k, kind in latest.items() if kind == "question"]


# --- Pod data -----------------------------------------------------------------------------

def _pod() -> dict:
    """Analysts with their tickers (price, thesis, beta) from the meeting board, macro desk first."""
    board = _board()
    for a in board.values():
        for sym, t in a["tickers"].items():
            overview = get_cached("market_overview", sym, None) or {}
            t["beta"] = overview.get("beta")
    return dict(sorted(board.items(), key=lambda kv: kv[1]["agent_type"] != "macro"))


def _macro_key(pod: dict) -> str:
    return next((k for k, a in pod.items() if a["agent_type"] == "macro"), next(iter(pod)))


def _macro_inputs(pod: dict) -> dict:
    indicators = {}
    for name in MACRO_SERIES:
        cached = get_cached("macro", name, None)
        if cached:
            indicators[name] = {"name": cached.get("name"), "unit": cached.get("unit"), "recent": (cached.get("series") or [])[:4]}
    try:
        from agents.dashboard import dashboard
        themes = [{k: t[k] for k in ("name", "tone", "tickers", "mentions")} for t in dashboard()["themes"]]
    except Exception as exc:  # the dashboard is a nicety; never block the session on it
        logger.warning("Strategy session: themes unavailable (%s)", exc)
        themes = []
    names = {sym: {"price": (t["price"] or {}).get("price") if isinstance(t["price"], dict) else t["price"],
                   "change_pct": (t["price"] or {}).get("change_pct") if isinstance(t["price"], dict) else None,
                   "beta": t["beta"], "signal": (t["thesis"] or {}).get("signal"), "analyst": a["analyst"]}
             for a in pod.values() for sym, t in a["tickers"].items()}
    return {"macro_indicators": indicators, "news_themes": themes, "covered_names": names}


def _transcript(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "system":
            continue
        entry = {"speaker": m["speaker"] or "PM", "kind": m["kind"], "said": m["text"]}
        if m["meta"].get("conflicts"):
            entry["conflicts"] = m["meta"]["conflicts"]
        if m["meta"].get("pitch"):
            entry["pitch"] = {k: m["meta"]["pitch"].get(k) for k in ("title", "long_ticker", "short_ticker", "structure",
                                                                    "market_missing", "data_points", "catalysts", "conviction")}
        if m["meta"].get("references"):
            entry["data"] = m["meta"]["references"]
        out.append(entry)
    return out


def _analyst_system(a: dict) -> str:
    return (f"You are {a['analyst']} ({a['agent_type']} analyst) in the research pod's all-hands strategy session, "
            f"in the boardroom overlooking Central Park. You cover: {', '.join(a['tickers'])}. The PM cares about "
            f"{', '.join(FOCUS_METRICS)} and actionable position management.\n"
            "The PM sets the strategy; your job is to make it work for your names. Align by default. Question only "
            "when specific data you were given conflicts with it, and then do it respectfully, citing the numbers. "
            "Never invent data.\n\n" + framework_block())


def _research_system(a: dict) -> str:
    return (f"You are {a['analyst']} ({a['agent_type']} analyst) in an ad-hoc research town hall the PM called in the "
            f"boardroom. You cover: {', '.join(a['tickers'])}. The PM is asking broad questions, not always about specific "
            "stocks. Answer with your real view, bring data, pitch a trade if you have edge, and react to colleagues: "
            "agree, disagree or build. Peer research, not hierarchy. Never invent data.\n\n" + framework_block())


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)


# --- Steps ----------------------------------------------------------------------------------------

def start_session(mode: str = "strategy") -> int:
    if mode not in ("strategy", "research"):
        raise SessionError("Mode must be strategy or research")
    llm.require_budget("strategy", 0.1)
    with session_scope() as s:
        active = s.scalar(select(StrategySession.id).where(StrategySession.status.in_(["thinking", "awaiting_user"])).limit(1))
        if active:
            raise SessionError(f"Strategy session #{active} is still open. Finish or close it first.")
        if mode == "research":  # no opening briefing: the floor is the PM's
            row = StrategySession(mode="research", status="awaiting_user", phase="discussion", messages=[{
                "id": 1, "role": "agent", "kind": "open", "agent_key": "chair", "speaker": "Chair", "meta": {},
                "text": "The whole pod is here. Ask us anything: what's interesting, where the market is wrong, what you'd do.",
                "ts": _now().isoformat()}])
        else:
            row = StrategySession(mode="strategy", status="thinking", phase="context", progress="Macro is reading the tape", messages=[])
        s.add(row)
        s.flush()
        return row.id


def run_context(session_id: int) -> None:
    """Step 1: the macro strategist opens with the market backdrop."""
    def step():
        pod = _pod()
        key = _macro_key(pod)
        a = pod[key]
        inputs = _macro_inputs(pod)
        ctx, _ = llm.parse_call(
            "strategy", MarketContext, _analyst_system(a),
            [{"role": "user", "content": f"<market_data>\n{_dump(inputs)}\n</market_data>\n\nThe PM has called an "
              "all-hands strategy session. Open it: give the room the market context (rates, the Fed, sentiment, beta) "
              "before the PM sets the strategy."}],
        )
        _say(session_id, "agent", "context", ctx.spoken, agent_key=key, speaker=a["analyst"],
             meta={"headline": ctx.headline, "rates": ctx.rates, "fed": ctx.fed, "sentiment": ctx.sentiment,
                   "beta": ctx.beta, "watch_items": ctx.watch_items})
        _update(session_id, market_context=ctx.model_dump(), status="awaiting_user", progress=None)
    _guard(session_id, step)


def post_message(session_id: int, text: str) -> None:
    """The PM speaks: the first message is the strategy; later ones answer the pod's questions."""
    with session_scope() as s:
        row = s.get(StrategySession, session_id)
        if row is None:
            raise SessionError("No such strategy session")
        if row.status != "awaiting_user":
            raise SessionError("The pod is still talking" if row.status == "thinking" else "This session is finished")
        first = not row.strategy
    estimate = 0.1 * len(_board()) if first else 0.25
    llm.require_budget("strategy", estimate)
    _say(session_id, "user", "strategy" if first else "reply", text, speaker="PM")
    fields = {"status": "thinking", "phase": "discussion", "progress": "The pod is considering your strategy"}
    if session_mode(session_id) == "research":
        fields["progress"] = "The pod is thinking about your question"
    if first:
        fields["strategy"] = text
    _update(session_id, **fields)


def session_mode(session_id: int) -> str:
    with session_scope() as s:
        row = s.get(StrategySession, session_id)
        return (row.mode or "strategy") if row else "strategy"


def run_replies(session_id: int) -> None:
    """Steps 2-3: analysts reply to the PM. After the first round, only those with open questions."""
    if session_mode(session_id) == "research":
        return run_research_replies(session_id)

    def step():
        session = get_session(session_id)
        pod = _pod()
        open_q = open_questions(session["messages"])
        first_round = not any(m["role"] == "agent" and m["kind"] in ("aligned", "question") for m in session["messages"])
        keys = list(pod) if first_round or not open_q else [k for k in pod if k in open_q]
        for key in keys:
            a = pod[key]
            _update(session_id, progress=f"{a['analyst']} is responding")
            messages = get_session(session_id)["messages"]  # include colleagues who just spoke
            mine = {sym: {k: t.get(k) for k in ("name", "price", "beta", "thesis")} for sym, t in a["tickers"].items()}
            ask = ("Reply to the PM's strategy: align, or respectfully question it if your data conflicts."
                   if first_round or key not in open_q else
                   "The PM has answered. Reply: align if that resolves your concern, otherwise ask one sharper question.")
            reply, _ = llm.parse_call(
                "strategy", AgentReply, _analyst_system(a),
                [{"role": "user", "content": f"<market_context>\n{_dump(session['market_context'])}\n</market_context>\n"
                  f"<your_names>\n{_dump(mine)}\n</your_names>\n<pm_strategy>\n{session['strategy']}\n</pm_strategy>\n"
                  f"<session_so_far>\n{_dump(_transcript(messages))}\n</session_so_far>\n\n{ask}"}],
            )
            _say(session_id, "agent", reply.stance, reply.spoken, agent_key=key, speaker=a["analyst"],
                 meta={"conflicts": reply.conflicts, "implications": [i.model_dump() for i in reply.implications]})
        _update(session_id, status="awaiting_user", progress=None)
    _guard(session_id, step)


def run_research_replies(session_id: int) -> None:
    """Research conversation: every analyst answers the PM's question in speaking order, hearing colleagues first."""
    def step():
        pod = _pod()
        for key in speaking_order(pod):
            a = pod[key]
            if not a["tickers"]:
                continue
            _update(session_id, progress=f"{a['analyst']} is answering")
            messages = get_session(session_id)["messages"]
            mine = {sym: {k: t.get(k) for k in ("name", "price", "beta", "thesis")} for sym, t in a["tickers"].items()}
            payload = {"your_names": mine, "headlines": headlines(list(a["tickers"]), per_ticker=3)}
            out, _ = llm.parse_call(
                "strategy", ResearchReply, _research_system(a),
                [{"role": "user", "content": f"<your_book>\n{_dump(payload)}\n</your_book>\n"
                  f"<conversation_so_far>\n{_dump(_transcript(messages))}\n</conversation_so_far>\n\n"
                  "Answer the PM's latest question; react to colleagues who already spoke where it adds something."}],
            )
            meta = {"references": out.references}
            if out.pitch:
                p = out.pitch
                allowed = {h["url"] for hs in payload["headlines"].values() for h in hs if h.get("url")}
                p.news = [n for n in p.news if n.url in allowed]
                with session_scope() as s:
                    row = Pitch(session_id=session_id, analyst_key=key, analyst_name=a["analyst"], title=p.title,
                                long_ticker=p.long_ticker, short_ticker=p.short_ticker, structure=p.structure,
                                conviction=max(1, min(10, p.conviction)), data=p.model_dump(), discussion=[])
                    s.add(row)
                    s.flush()
                    meta["pitch"] = {"db_id": row.id, **p.model_dump()}
            _say(session_id, "agent", "pitch" if out.pitch else out.kind, out.spoken, agent_key=key, speaker=a["analyst"], meta=meta)
        _update(session_id, status="awaiting_user", progress=None)
    _guard(session_id, step)


def begin_finalize(session_id: int) -> None:
    with session_scope() as s:
        row = s.get(StrategySession, session_id)
        if row is None:
            raise SessionError("No such strategy session")
        if row.status != "awaiting_user":
            raise SessionError("The pod is still talking" if row.status == "thinking" else "This session is finished")
        if not row.strategy:
            raise SessionError("Ask the pod something first" if row.mode == "research" else "Set a strategy first")
        research = row.mode == "research"
    llm.require_budget("strategy", 0.15 if research else 0.15 + 0.2 * len(_board()))
    _update(session_id, status="thinking", phase="memo",
            progress="The chair is writing the research memo" if research else "The chair is drafting the research charter")


def run_finalize(session_id: int) -> None:
    """Step 4: strategy memo, sign-offs, and every analyst's theses re-stated under the new strategy."""
    if session_mode(session_id) == "research":
        return run_research_memo(session_id)

    def step():
        session = get_session(session_id)
        pod = _pod()
        transcript = _transcript(session["messages"])
        memo, _ = llm.parse_call(
            "strategy", StrategyMemo,
            "You chair the research pod's strategy session and write the Research Charter every analyst will sign and "
            "apply to all future research. Capture the PM's strategy faithfully as concrete research directives, plus "
            "the market context, what each desk will do, guardrails, and any dissent that remains. Crisp, no filler.",
            [{"role": "user", "content": f"<market_context>\n{_dump(session['market_context'])}\n</market_context>\n"
              f"<session>\n{_dump(transcript)}\n</session>\n\nWrite the strategy memo."}],
        )
        _update(session_id, memo_title=memo.title, memo_md=memo.memo_md)
        with session_scope() as s:  # the memo becomes the research charter every agent follows from now on
            for old in s.scalars(select(ResearchCharter).where(ResearchCharter.active.is_(True))):
                old.active = False
            s.add(ResearchCharter(session_id=session_id, title=memo.title, directives=memo.directives, charter_md=memo.memo_md, active=True))
            s.add(Memo(kind="strategy", session_id=session_id, title=memo.title, memo_md=memo.memo_md))
        _say(session_id, "agent", "memo", memo.spoken_summary, agent_key="chair", speaker="Chair",
             meta={"title": memo.title, "directives": memo.directives})

        signoffs, revisions = [], []
        for key, a in pod.items():
            _update(session_id, progress=f"{a['analyst']} is signing off and updating theses")
            mine = {sym: {k: t.get(k) for k in ("name", "price", "beta", "thesis")} for sym, t in a["tickers"].items()}
            out, message = llm.parse_call(
                "strategy", SignOff, _analyst_system(a),
                [{"role": "user", "content": f"<research_charter>\n{memo.memo_md}\n</research_charter>\n<your_names>\n{_dump(mine)}\n"
                  f"</your_names>\n\nSign off on the charter, then give one revision per ticker you cover so each thesis "
                  "reflects the new strategy (revise only where it changes the call; repeat it unchanged otherwise)."}],
                max_tokens=SIGNOFF_MAX_TOKENS,
            )
            changed = []
            for rev in out.revisions:
                ctx = get_context(rev.ticker)
                if not (ctx and ctx.symbol in a["tickers"]):
                    continue
                if rev.revised and rev.thesis:
                    save_thesis(ctx, rev.thesis, "strategy", f"strategy session #{session_id}: {rev.change_summary[:200]}", message.model)
                    refresh_files(ctx.symbol, ["trading_thesis.md"])
                    changed.append(ctx.symbol)
                revisions.append({"ticker": ctx.symbol, "analyst": a["analyst"], "agent_key": key, "revised": bool(rev.revised and rev.thesis),
                                  "change_summary": rev.change_summary,
                                  "signal": rev.thesis.signal if rev.thesis else None,
                                  "conviction": rev.thesis.conviction_level if rev.thesis else None})
            signoff = {"agent_key": key, "analyst": a["analyst"], "signed": out.signed, "statement": out.statement,
                       "reservations": out.reservations, "revised": changed}
            signoffs.append(signoff)
            _update(session_id, signoffs=list(signoffs), revisions=list(revisions))
            _say(session_id, "agent", "signoff", out.statement, agent_key=key, speaker=a["analyst"],
                 meta={"signed": out.signed, "reservations": out.reservations, "revised": changed})
        _update(session_id, status="done", phase="done", progress=None, finished_at=_now())
    _guard(session_id, step, retry_hint="Generate the memo again to retry.")


def run_research_memo(session_id: int) -> None:
    """Research conversation wrap-up: the chair's research memo (also stored in memos)."""
    def step():
        session = get_session(session_id)
        memo, _ = llm.parse_call(
            "strategy", ResearchMemo,
            "You chair the pod's ad-hoc research town hall and write the research memo the PM reviews afterwards: the "
            "questions, what each analyst sees, every trade idea with its structure, edge, catalysts, conviction and "
            "pushback, what the market is missing, and follow-ups. Trades and narrative, never ratings.\n\n" + framework_block(),
            [{"role": "user", "content": f"<conversation>\n{_dump(_transcript(session['messages']))}\n</conversation>\n\nWrite the research memo."}],
        )
        with session_scope() as s:
            s.add(Memo(kind="research", session_id=session_id, title=memo.title, memo_md=memo.memo_md))
        _update(session_id, memo_title=memo.title, memo_md=memo.memo_md)
        _say(session_id, "agent", "memo", memo.spoken_summary, agent_key="chair", speaker="Chair", meta={"title": memo.title})
        _update(session_id, status="done", phase="done", progress=None, finished_at=_now())
    _guard(session_id, step, retry_hint="Wrap up again to retry.")


def close_session(session_id: int) -> None:
    """End a session without a memo."""
    with session_scope() as s:
        row = s.get(StrategySession, session_id)
        if row is None:
            raise SessionError("No such strategy session")
        if row.status == "thinking":
            raise SessionError("The pod is still talking; close it when they finish")
        if row.status == "awaiting_user":
            row.status, row.finished_at, row.updated_at = "closed", _now(), _now()


def _guard(session_id: int, step, retry_hint: str = "Send your message again to retry.") -> None:
    """Run a background step; on failure keep the session usable and say what went wrong."""
    try:
        step()
    except Exception as exc:
        logger.exception("Strategy session %s step failed", session_id)
        detail = f"over today's budget ({exc})." if isinstance(exc, llm.BudgetExceeded) else f"{str(exc)[:300]}."
        _say(session_id, "system", "error", f"Something went wrong: {detail} {retry_hint}")
        _update(session_id, status="awaiting_user", progress=None)
