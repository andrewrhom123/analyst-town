"""Agent voices: ElevenLabs text-to-speech and OpenAI Whisper speech-to-text.

Four voice profiles, one per analyst (macro, fintech, internet, AI), plus a chair voice that narrates meetings.
Analysts added later from chat are mapped onto one of the four by a stable hash. Voice IDs are ElevenLabs
premade voices and can be swapped with ELEVENLABS_VOICE_<KEY> env vars.

Cost control: synthesized audio is cached on disk by (voice, model, text), so replays are free, and fresh
synthesis is capped at VOICE_DAILY_CHAR_LIMIT characters per US/Eastern day (VOICE_MAX_CHARS per request).
"""

import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from utils import config

logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
STT_URL = "https://api.openai.com/v1/audio/transcriptions"
STT_MAX_BYTES = 25 * 1024 * 1024  # Whisper's upload limit


class VoiceError(Exception):
    pass


class VoiceNotConfigured(VoiceError):
    pass


class VoiceLimitReached(VoiceError):
    pass


@dataclass(frozen=True)
class VoiceProfile:
    key: str
    label: str
    voice_id: str
    stability: float
    style: float

    def public(self) -> dict:
        return {"key": self.key, "label": self.label}


def _profile(key: str, label: str, default_id: str, stability: float, style: float) -> VoiceProfile:
    return VoiceProfile(key, label, os.getenv(f"ELEVENLABS_VOICE_{key.upper()}", default_id), stability, style)


# Distinct delivery per desk: steady macro baritone, brisk fintech, upbeat internet, precise AI.
PROFILES = {
    "macro": _profile("macro", "George: measured, warm baritone", "JBFqnCBsd6RMkjVDRZzb", 0.6, 0.15),
    "fintech": _profile("fintech", "Rachel: crisp and brisk", "21m00Tcm4TlvDq8ikWAM", 0.45, 0.3),
    "internet": _profile("internet", "Charlie: casual and upbeat", "IKne3meq5aSn9XLyUdCD", 0.4, 0.45),
    "ai": _profile("ai", "Matilda: precise and bright", "XrExE9yKIg1WjnnlVkGX", 0.55, 0.25),
}
CHAIR = _profile("chair", "Daniel: meeting chair", "onwK4e9ZLuTAKqWW03F8", 0.65, 0.1)
PROFILE_ORDER = list(PROFILES)


def profile_for(agent_key: str | None) -> VoiceProfile:
    """The voice for an analyst key ('chair' for the meeting narrator)."""
    key = (agent_key or "").lower()
    if key == "chair":
        return CHAIR
    if key in PROFILES:
        return PROFILES[key]
    digest = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    return PROFILES[PROFILE_ORDER[digest % len(PROFILE_ORDER)]]


# --- Text preparation ----------------------------------------------------------------------

def speakable(text: str) -> str:
    """Markdown answer -> plain spoken text (no tables, code, link targets or symbols read aloud)."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.M)  # table rows
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", r"\1.", text, flags=re.M)  # headings end a sentence
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_`~>#]", "", text)
    text = text.replace("&", " and ").replace("%", " percent").replace("+/-", "plus or minus")
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s*\n\s*\n\s*", ". ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([.!?:])\s*\.", r"\1", text)
    return text.strip(" .") + "." if text.strip(" .") else ""


def clip(text: str, limit: int) -> str:
    """Cut at the last sentence end within `limit` characters."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    return head[: cut + 1] if cut > limit * 0.4 else head.rsplit(" ", 1)[0] + "…"


# --- Text to speech ------------------------------------------------------------------------

_usage_lock = threading.Lock()
_usage = {"day": None, "chars": 0}


def _charge(chars: int) -> None:
    today = datetime.now(EASTERN).date()
    with _usage_lock:
        if _usage["day"] != today:
            _usage.update(day=today, chars=0)
        if _usage["chars"] + chars > config.VOICE_DAILY_CHAR_LIMIT:
            raise VoiceLimitReached(f"Daily voice limit reached ({_usage['chars']}/{config.VOICE_DAILY_CHAR_LIMIT} characters). "
                                    "Cached clips still play; raise VOICE_DAILY_CHAR_LIMIT for more.")
        _usage["chars"] += chars


def usage_today() -> dict:
    today = datetime.now(EASTERN).date()
    with _usage_lock:
        used = _usage["chars"] if _usage["day"] == today else 0
    return {"chars_today": used, "daily_char_limit": config.VOICE_DAILY_CHAR_LIMIT}


def _cache_path(profile: VoiceProfile, text: str) -> Path:
    digest = hashlib.sha256(f"{profile.voice_id}|{config.ELEVENLABS_MODEL}|{profile.stability}|{profile.style}|{text}".encode()).hexdigest()
    return Path(config.VOICE_CACHE_DIR) / f"{digest}.mp3"


def synthesize(text: str, agent_key: str | None) -> tuple[bytes, dict]:
    """MP3 bytes of `text` in the agent's voice. Returns (audio, info)."""
    if not config.ELEVENLABS_API_KEY:
        raise VoiceNotConfigured("Voice output is off: set ELEVENLABS_API_KEY on the backend")
    spoken = clip(speakable(text), config.VOICE_MAX_CHARS)
    if not spoken:
        raise VoiceError("Nothing to say")
    profile = profile_for(agent_key)
    path = _cache_path(profile, spoken)
    info = {"voice": profile.key, "chars": len(spoken), "cached": True}
    if path.exists():
        return path.read_bytes(), info
    _charge(len(spoken))
    try:
        res = requests.post(
            TTS_URL.format(voice_id=profile.voice_id),
            headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Accept": "audio/mpeg"},
            json={"text": spoken, "model_id": config.ELEVENLABS_MODEL,
                  "voice_settings": {"stability": profile.stability, "similarity_boost": 0.8,
                                     "style": profile.style, "use_speaker_boost": True}},
            timeout=60,
        )
    except requests.RequestException as exc:
        _charge(-len(spoken))
        raise VoiceError(f"ElevenLabs unreachable: {exc}") from exc
    if res.status_code != 200:
        _charge(-len(spoken))
        raise VoiceError(f"ElevenLabs error {res.status_code}: {_error_text(res)}")
    audio = res.content
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    except OSError as exc:
        logger.warning("Could not cache voice clip: %s", exc)
    return audio, {**info, "cached": False}


# --- Speech to text --------------------------------------------------------------------------

EXTENSIONS = {"webm": "webm", "ogg": "ogg", "mp4": "mp4", "m4a": "m4a", "x-m4a": "m4a", "aac": "m4a",
              "mpeg": "mp3", "mp3": "mp3", "wav": "wav", "x-wav": "wav", "flac": "flac"}


def transcribe(audio: bytes, content_type: str, vocabulary: list[str] | None = None) -> str:
    """Whisper transcription. `vocabulary` (tickers, company names) is passed as a prompt so symbols spell right."""
    if not config.OPENAI_API_KEY:
        raise VoiceNotConfigured("Voice input is off: set OPENAI_API_KEY on the backend")
    if not audio:
        raise VoiceError("No audio received")
    if len(audio) > STT_MAX_BYTES:
        raise VoiceError("Recording is too long (25 MB max)")
    subtype = (content_type or "audio/webm").split(";")[0].split("/")[-1].lower()
    ext = EXTENSIONS.get(subtype, "webm")
    data = {"model": config.WHISPER_MODEL, "response_format": "json", "language": "en"}
    if vocabulary:
        data["prompt"] = "Equity research discussion. Tickers and companies: " + ", ".join(vocabulary)[:800]
    try:
        res = requests.post(STT_URL, headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                            files={"file": (f"speech.{ext}", audio, content_type or "audio/webm")}, data=data, timeout=60)
    except requests.RequestException as exc:
        raise VoiceError(f"OpenAI unreachable: {exc}") from exc
    if res.status_code != 200:
        raise VoiceError(f"Whisper error {res.status_code}: {_error_text(res)}")
    return (res.json().get("text") or "").strip()


def _error_text(res: requests.Response) -> str:
    try:
        body = res.json()
        detail = body.get("detail") or body.get("error") or body
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        return str(detail)[:300]
    except ValueError:
        return res.text[:300]


# --- Meeting script (boardroom playback) -----------------------------------------------------

LINE_CHARS = 420  # keep each spoken turn short; the full text is in the minutes


def meeting_script(meeting_id: int, transcript: dict, minutes_md: str | None) -> list[dict]:
    """Turn a meeting transcript into spoken turns: [{speaker, agent_key, round, text}]."""
    lines: list[dict] = []

    def say(agent_key: str, speaker: str, rnd: str, text: str) -> None:
        text = clip(speakable(text or ""), LINE_CHARS)
        if text:
            lines.append({"speaker": speaker, "agent_key": agent_key, "round": rnd, "text": text})

    contributions = (transcript or {}).get("contributions") or {}
    responses = (transcript or {}).get("responses") or {}
    if not contributions:
        return lines
    say("chair", "Chair", "open", f"Welcome to pod meeting number {meeting_id}. Round one: market reads and challenges.")
    for key, c in contributions.items():
        name = c.get("analyst", key)
        say(key, name, "challenges", c.get("market_read", ""))
        for ch in c.get("challenges") or []:
            say(key, name, "challenges", f"A challenge on {ch.get('ticker')}. {ch.get('challenge', '')} {ch.get('suggested_change', '')}")
    if responses:
        say("chair", "Chair", "responses", "Round two: responses and revisions.")
    verdicts = {"accepted": "I accept that.", "partially_accepted": "I partly agree.", "rejected": "I disagree."}
    for key, r in responses.items():
        name = r.get("analyst", key)
        for resp in r.get("responses") or []:
            say(key, name, "responses", f"On {resp.get('ticker')}: {verdicts.get(resp.get('verdict'), '')} {resp.get('response', '')}")
        for rev in r.get("revisions") or []:
            if rev.get("revised"):
                say(key, name, "responses", f"I'm revising my {rev.get('ticker')} call. {rev.get('change_summary', '')}")
    if minutes_md:
        body = re.sub(r"^# .*$", "", minutes_md, count=1, flags=re.M)
        say("chair", "Chair", "close", "To sum up. " + body)
    return lines
