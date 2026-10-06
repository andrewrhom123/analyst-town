"""SQLAlchemy models.

Analyst (an AI persona) covers many Tickers. Each ticker has a trading thesis history, deep-dive
research, a financial model and five coverage files. Everything the agents spend is in cost_entries.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Analyst(Base):
    __tablename__ = "analysts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # e.g. "trade-desk"
    name: Mapped[str] = mapped_column(String(100))
    agent_type: Mapped[str] = mapped_column(String(20))  # company | macro | sector
    focus_notes: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)  # town/house accent color
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tickers: Mapped[list["Ticker"]] = relationship(back_populates="analyst")


class Ticker(Base):
    __tablename__ = "tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # also the coverage folder name
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e.g. ["SQ"] for XYZ
    name: Mapped[str] = mapped_column(String(200))
    ticker_type: Mapped[str] = mapped_column(String(20))  # public | foreign | private | index
    analyst_id: Mapped[int] = mapped_column(ForeignKey("analysts.id"), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    sec_ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    news_query: Mapped[str] = mapped_column(Text, default="")
    news_keywords: Mapped[list] = mapped_column(JSON, default=list)
    competitors: Mapped[list] = mapped_column(JSON, default=list)
    comps_buckets: Mapped[dict] = mapped_column(JSON, default=dict)
    focus_notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_deep_dive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_thesis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_filing_accession: Mapped[str | None] = mapped_column(String(30), nullable=True)

    analyst: Mapped[Analyst] = relationship(back_populates="tickers")


class TradingThesis(Base):
    """Every version of a ticker's trading thesis (latest = current)."""

    __tablename__ = "trading_theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    trigger: Mapped[str] = mapped_column(String(30))  # deep_dive | price_move | level_cross | meeting | manual
    trigger_detail: Mapped[str] = mapped_column(Text, default="")
    price_at_update: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis: Mapped[dict] = mapped_column(JSON)
    model_used: Mapped[str] = mapped_column(String(50), default="")


class ResearchOutput(Base):
    """Deep-dive research in the requested format (long_form_memo, executive_summary, ...)."""

    __tablename__ = "research_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    research_memo: Mapped[dict] = mapped_column(JSON)
    conviction_level: Mapped[int] = mapped_column(Integer)
    rating: Mapped[str] = mapped_column(String(20), default="")
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model_used: Mapped[str] = mapped_column(String(50), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    data_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class FinancialModel(Base):
    __tablename__ = "financial_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    research_id: Mapped[int | None] = mapped_column(ForeignKey("research_outputs.id"), nullable=True)
    revenue_projections: Mapped[list] = mapped_column(JSON)  # annual rows
    margin_analysis: Mapped[dict] = mapped_column(JSON)  # SEC historical tables
    assumptions: Mapped[dict] = mapped_column(JSON)  # {"inputs": ModelInputs, "outputs": computed model}
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CoverageFile(Base):
    """The five per-ticker files (company_memo.md, latest_events.md, financial_model.json, ...)."""

    __tablename__ = "coverage_files"
    __table_args__ = (UniqueConstraint("ticker_id", "filename", name="uq_coverage_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    filename: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PriceTick(Base):
    __tablename__ = "price_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    price: Mapped[float] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # vs previous close
    day_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    day_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), index=True)
    user_question: Mapped[str] = mapped_column(Text)
    agent_response: Mapped[str] = mapped_column(Text)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))  # scheduled | manual
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | done | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    minutes_md: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentLesson(Base):
    """Generalizable lessons an analyst takes from meetings; fed back into its future prompts."""

    __tablename__ = "agent_lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analyst_id: Mapped[int] = mapped_column(ForeignKey("analysts.id"), index=True)
    lesson: Mapped[str] = mapped_column(Text)
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """Work queue for Claude-backed tasks (deep dives, thesis updates). Processed one at a time."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # deep_dive | thesis_update
    ticker_id: Mapped[int | None] = mapped_column(ForeignKey("tickers.id"), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued | running | done | failed | deferred
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class CostEntry(Base):
    """One row per Claude call: the budget governor sums these."""

    __tablename__ = "cost_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(30))  # deep_dive | thesis_update | meeting | ask
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)


class DataCache(Base):
    __tablename__ = "data_cache"
    __table_args__ = (UniqueConstraint("data_type", "ticker", name="uq_cache_type_ticker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_type: Mapped[str] = mapped_column(String(50))  # sec | market_quote | market_overview | news | macro | ...
    ticker: Mapped[str] = mapped_column(String(50))
    raw_data: Mapped[dict] = mapped_column(JSON)
    fetch_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduledRunLock(Base):
    """Guarantees each scheduled slot (e.g. the daily meeting) runs once even with several processes."""

    __tablename__ = "scheduled_run_locks"

    run_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SmsAlert(Base):
    """One row per price alert (per ticker, per trading day, per 5% band), whether or not the text went out."""

    __tablename__ = "sms_alerts"
    __table_args__ = (UniqueConstraint("symbol", "alert_date", "band", name="uq_sms_alert_band"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    alert_date: Mapped[str] = mapped_column(String(10))  # US/Eastern trading day, YYYY-MM-DD
    band: Mapped[int] = mapped_column(Integer)  # +1 = up 5%+, +2 = up 10%+, -1 = down 5%+, ...
    change_pct: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # sent | partial | failed | skipped
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # Twilio SIDs or error / skip reason
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class StrategySession(Base):
    """All-hands strategy session: macro context, the PM's strategy, the pod's replies, memo and sign-offs."""
    __tablename__ = "strategy_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="thinking")  # thinking | awaiting_user | done | closed
    phase: Mapped[str] = mapped_column(String(20), default="context")  # context | discussion | memo | done
    progress: Mapped[str | None] = mapped_column(String(200), nullable=True)  # who is talking right now
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    strategy: Mapped[str] = mapped_column(Text, default="")  # the PM's opening strategy statement
    messages: Mapped[list] = mapped_column(JSON, default=list)
    market_context: Mapped[dict] = mapped_column(JSON, default=dict)
    memo_title: Mapped[str] = mapped_column(String(300), default="")
    memo_md: Mapped[str] = mapped_column(Text, default="")
    signoffs: Mapped[list] = mapped_column(JSON, default=list)
    revisions: Mapped[list] = mapped_column(JSON, default=list)
