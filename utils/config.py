"""Central configuration. All secrets come from environment variables (.env locally, Railway vars in prod)."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or f"sqlite:///{PROJECT_ROOT / 'analyst.db'}"
    # Railway/Render/Heroku hand out postgres:// URLs; SQLAlchemy 2 needs postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- API keys -------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
# SEC requires a descriptive User-Agent with a contact email: "Your Name your@email.com"
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")

# Optional shared secret protecting the POST endpoints (they spend Claude credits).
API_ACCESS_KEY = os.getenv("API_ACCESS_KEY", "")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

# --- Database -------------------------------------------------------------
DATABASE_URL = _database_url()

# --- Claude ---------------------------------------------------------------
# Deep dives (SEC filings, financial model, long-form memo): Claude Opus 5.5.
DEEP_MODEL = os.getenv("DEEP_MODEL", os.getenv("CLAUDE_MODEL", "claude-opus-5-5"))
CLAUDE_MODEL = DEEP_MODEL  # backwards-compatible name
# Frequent lightweight calls (thesis updates, meetings, /ask): Claude Sonnet 5.5.
LIGHT_MODEL = os.getenv("LIGHT_MODEL", "claude-sonnet-5-5")
# Effort controls thinking depth / token spend: low | medium | high | xhigh | max
RESEARCH_EFFORT = os.getenv("RESEARCH_EFFORT", "high")
LIGHT_EFFORT = os.getenv("LIGHT_EFFORT", "medium")
ASK_EFFORT = os.getenv("ASK_EFFORT", "low")  # fast conversational replies
RESEARCH_MAX_TOKENS = _int("RESEARCH_MAX_TOKENS", 64000)
QUESTION_MAX_TOKENS = _int("QUESTION_MAX_TOKENS", 16000)

# --- Budget (US/Eastern day) ---------------------------------------------
def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


DAILY_BUDGET_USD = _float("DAILY_BUDGET_USD", 5.0)
MEETING_RESERVE_USD = _float("MEETING_RESERVE_USD", 1.5)
ASK_RESERVE_USD = _float("ASK_RESERVE_USD", 0.5)
DEEP_DIVE_ESTIMATE_USD = _float("DEEP_DIVE_ESTIMATE_USD", 1.0)
THESIS_UPDATE_ESTIMATE_USD = _float("THESIS_UPDATE_ESTIMATE_USD", 0.06)
MEETING_ESTIMATE_USD = _float("MEETING_ESTIMATE_USD", 1.2)

# --- Real-time monitoring ---------------------------------------------------
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
PRICE_CHECK_MINUTES = _int("PRICE_CHECK_MINUTES", 30)
PRICE_MOVE_TRIGGER_PCT = _float("PRICE_MOVE_TRIGGER_PCT", 5.0)  # move since last thesis -> thesis update
THESIS_COOLDOWN_HOURS = _float("THESIS_COOLDOWN_HOURS", 3.0)  # min gap between triggered updates per ticker
DEEP_DIVE_MIN_GAP_MINUTES = _int("DEEP_DIVE_MIN_GAP_MINUTES", 60)  # spread deep dives through the day
MEETING_HOUR = _int("MEETING_HOUR", 16)
MEETING_MINUTE = _int("MEETING_MINUTE", 30)
COVERAGE_DIR = os.getenv("COVERAGE_DIR", str(PROJECT_ROOT / "coverage"))
# Max characters per SEC filing section sent to Claude (cost control; ~4 chars per token).
SEC_SECTION_CHAR_LIMIT = _int("SEC_SECTION_CHAR_LIMIT", 40000)

# --- Cache TTLs (seconds) -------------------------------------------------
SEC_CACHE_TTL = _int("SEC_CACHE_TTL", 7 * 24 * 3600)
MARKET_CACHE_TTL = _int("MARKET_CACHE_TTL", 3600)
NEWS_CACHE_TTL = _int("NEWS_CACHE_TTL", 24 * 3600)
MACRO_CACHE_TTL = _int("MACRO_CACHE_TTL", 24 * 3600)
PEER_CACHE_TTL = _int("PEER_CACHE_TTL", 7 * 24 * 3600)
# Peer (comps) fundamentals are only fetched while today's Alpha Vantage call count is below this.
# Free tier = 25/day and a daily run needs ~19 core calls, so 6 is left for comps. Raise on a paid plan.
ALPHAVANTAGE_PEER_BUDGET = _int("ALPHAVANTAGE_PEER_BUDGET", 6)

# --- Scheduler ------------------------------------------------------------
ENABLE_SCHEDULER = _bool("ENABLE_SCHEDULER", True)
SCHEDULE_HOUR = _int("SCHEDULE_HOUR", 6)
SCHEDULE_MINUTE = _int("SCHEDULE_MINUTE", 0)
SCHEDULE_TIMEZONE = os.getenv("SCHEDULE_TIMEZONE", "America/New_York")

# --- Notifications (optional) --------------------------------------------
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def setup_logging() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def missing_keys() -> list[str]:
    """Names of required settings that are not configured."""
    required = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "ALPHAVANTAGE_API_KEY": ALPHAVANTAGE_API_KEY,
        "NEWSAPI_KEY": NEWSAPI_KEY,
        "SEC_USER_AGENT": SEC_USER_AGENT,
        "FINNHUB_API_KEY": FINNHUB_API_KEY,
    }
    return [name for name, value in required.items() if not value]
