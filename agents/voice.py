"""Agent voices: ElevenLabs text-to-speech and ElevenLabs Scribe speech-to-text (one key: ELEVENLABS_API_KEY).

Four voice profiles, one per analyst (macro, fintech, internet, AI), plus a chair voice that narrates meetings.
Analysts added later from chat are mapped onto one of the four by a stable hash. Each profile names a preferred
ElevenLabs premade voice (override with ELEVENLABS_VOICE_<KEY>), but which premade voices an account has varies,
so profiles are resolved against the account's own voice list (GET /v1/voices): a missing voice is replaced by
an unused one of the same gender, keeping the five voices distinct.

Cost control: synthesized audio is cached on disk by (voice, model, text), so replays are free, and fresh
synthesis is capped at VOICE_DAILY_CHAR_LIMIT characters per US/Eastern day (VOICE_MAX_CHARS per request).
"""

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from utils import config

logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
VOICES_URL = "https://api.elevenlabs.io/v1/voices"
STT_MAX_BYTES = 25 * 1024 * 1024  # a chat recording is far smaller; the API itself allows much more


class VoiceError(Exception):
    pass


class VoiceNotConfigured(VoiceError):
    pass


class VoiceLimitReached(VoiceError):
    pass


@dataclass(frozen=True)
class VoiceProfile:
    key: str
    style_note: str
    preferred: str  # preferred voice name, used to pick a stand-in when the configured ID is missing
    gender: str
    voice_id: str  # configured ID; may not exist in this account (see resolve())
    stability: float
    style: float

    def public(self) -> dict:
        voice_id, name = resolve(self)
        return {"key": self.key, "label": f"{name}: {self.style_note}", "voice_id": voice_id}


def _profile(key, style_note, preferred, gender, default_id, stability, style) -> VoiceProfile:
    configured = os.getenv(f"ELEVENLABS_VOICE_{key.upper()}", default_id)
    return VoiceProfile(key, style_note, preferred, gender, configured, stability, style)


# Distinct delivery per desk: steady macro baritone, brisk fintech, upbeat internet, precise AI.
PROFILES = {
    "macro": _profile("macro", "measured, warm baritone", "George", "male", "JBFqnCBsd6RMkjVDRZzb", 0.6, 0.15),
    "fintech": _profile("fintech", "crisp and brisk", "Rachel", "female", "21m00Tcm4TlvDq8ikWAM", 0.45, 0.3),
    "internet": _profile("internet", "casual and upbeat", "Charlie", "male", "IKne3meq5aSn9XLyUdCD", 0.4, 0.45),
    "ai": _profile("ai", "precise and bright", "Matilda", "female", "XrExE9yKIg1WjnnlVkGX", 0.55, 0.25),
}
CHAIR = _profile("chair", "meeting chair", "Daniel", "male", "onwK4e9ZLuTAKqWW03F8", 0.65, 0.1)
PROFILE_ORDER = list(PROFILES)
ALL_PROFILES = [*PROFILES.values(), CHAIR]


def profile_for(agent_key: str | None) -> VoiceProfile:
    """The voice for an analyst key ('chair' for the meeting narrator)."""
    key = (agent_key or "").lower()
    if key == "chair":
        return CHAIR
    if key in PROFILES:
        return PROFILES[key]
    digest = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    return PROFILES[PROFILE_ORDER[digest % len(PROFILE_ORDER)]]


# --- Resolving profiles against the account's voices ------------------------------------------

# Standard English ElevenLabs voices (name, gender, id). When a voice is missing and the account's voice list
# can't be read (key without Voices read permission), synthesis walks this pool until a voice works. A
# voice-not-found response generates no audio, so the probing is free.
FALLBACK_POOL = [
    ("George", "male", "JBFqnCBsd6RMkjVDRZzb"), ("Sarah", "female", "EXAVITQu4vr4xnJW9oFQ"),
    ("Charlie", "male", "IKne3meq5aSn9XLyUdCD"), ("Matilda", "female", "XrExE9yKIg1WjnnlVkGX"),
    ("Brian", "male", "nPczCjzI2devNBz1zQrb"), ("Alice", "female", "Xb7hH8MSUJpSbSDYk0k2"),
    ("Roger", "male", "CwhRBWXzGAHq8TQ4Fs17"), ("Laura", "female", "FGY2WhTYpPnrIDTdsKH5"),
    ("Callum", "male", "N2lVS1w4EtoT3dr4eOWO"), ("Charlotte", "female", "XB0fDUnXU5powFXDhCwa"),
    ("Liam", "male", "TX3LPaxmHKxFdv7VOQHJ"), ("Jessica", "female", "cgSgspJ2msm6clMCkdW9"),
    ("Will", "male", "bIHbv24MWmeRgasZH58o"), ("Lily", "female", "pFZP5JQG7iQjIQuC4Bku"),
    ("Eric", "male", "cjVigY5qzO86Huf0OWal"), ("Aria", "female", "9BWtsMINqrJLrRacOk9x"),
    ("Chris", "male", "iP95p4xoKVk53GoZ742B"), ("Rachel", "female", "21m00Tcm4TlvDq8ikWAM"),
    ("Bill", "male", "pqHfZKP75CyOlSgLPmqH"), ("Adam", "male", "pNInz6obpgDQGcFmaJgB"),
    ("Daniel", "male", "onwK4e9ZLuTAKqWW03F8"), ("River", "neutral", "SAz9YHcvj6GT2YYXdXww"),
]
MAX_VOICE_PROBES = len(FALLBACK_POOL)
_bad_ids: set[str] = set()  # voice IDs this account turned out not to have

RESOLVE_TTL = 3600  # re-check the account's voice list hourly (or right after a voice-not-found error)
_resolve_lock = threading.Lock()
_resolved: dict = {"at": 0.0, "map": {}, "listed": False}


def _account_voices() -> list[dict] | None:
    """The account's voices, or None if they can't be listed (network error, key without voices_read)."""
    try:
        res = requests.get(VOICES_URL, headers={"xi-api-key": config.ELEVENLABS_API_KEY}, timeout=15)
    except requests.RequestException as exc:
        logger.warning("Could not list ElevenLabs voices: %s", exc)
        return None
    if res.status_code != 200:
        logger.warning("Could not list ElevenLabs voices (%s): %s", res.status_code, _error_text(res))
        return None
    return res.json().get("voices") or []


def _pick(profile: VoiceProfile, voices: list[dict], used: set[str]) -> dict:
    """Best stand-in: unused first, then the preferred name, then the same gender, then premade voices."""
    def rank(v: dict):
        labels = v.get("labels") or {}
        return (v["voice_id"] in used,
                not (v.get("name") or "").lower().startswith(profile.preferred.lower()),
                (labels.get("gender") or "").lower() != profile.gender,
                v.get("category") != "premade")
    return min(voices, key=rank)


def resolve_all(force: bool = False) -> dict[str, tuple[str, str]]:
    """profile key -> (voice_id, voice name) usable with this account. Cached for RESOLVE_TTL."""
    with _resolve_lock:
        if not force and _resolved["map"] and time.time() - _resolved["at"] < RESOLVE_TTL:
            return _resolved["map"]
        voices = _account_voices() if config.ELEVENLABS_API_KEY else None
        mapping: dict[str, tuple[str, str]] = {}
        if not voices:  # can't check: keep stand-ins found by probing, else the configured IDs
            previous = _resolved["map"]
            mapping = {p.key: previous[p.key] if p.key in previous and previous[p.key][0] not in _bad_ids
                       else (p.voice_id, p.preferred) for p in ALL_PROFILES}
        else:
            by_id = {v["voice_id"]: v for v in voices}
            used: set[str] = set()
            for p in ALL_PROFILES:
                if p.voice_id in by_id:
                    mapping[p.key] = (p.voice_id, by_id[p.voice_id].get("name") or p.preferred)
                    used.add(p.voice_id)
            for p in ALL_PROFILES:
                if p.key not in mapping:
                    v = _pick(p, voices, used)
                    mapping[p.key] = (v["voice_id"], v.get("name") or p.preferred)
                    used.add(v["voice_id"])
                    logger.warning("ElevenLabs voice %s (%s) for '%s' is not in this account; using %s (%s)",
                                   p.voice_id, p.preferred, p.key, v.get("name"), v["voice_id"])
        _resolved.update(at=time.time(), map=mapping, listed=voices is not None)
        return mapping


def resolve(profile: VoiceProfile, force: bool = False) -> tuple[str, str]:
    return resolve_all(force).get(profile.key, (profile.voice_id, profile.preferred))


def _next_fallback(profile: VoiceProfile, missing_id: str) -> str | None:
    """Swap a missing voice for an untried English voice no other desk uses (same gender first)."""
    resolve_all()
    with _resolve_lock:
        _bad_ids.add(missing_id)
        mapping = dict(_resolved["map"])
        taken = {vid for key, (vid, _) in mapping.items() if key != profile.key}
        options = [c for c in FALLBACK_POOL if c[2] not in _bad_ids and c[2] not in taken]
        if not options:
            return None
        name, _, vid = min(options, key=lambda c: c[1] != profile.gender)  # stable: pool order breaks ties
        mapping[profile.key] = (vid, name)
        _resolved["map"] = mapping
        logger.warning("ElevenLabs voice %s for '%s' is not available; trying %s (%s)", missing_id, profile.key, name, vid)
        return vid


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


def _cache_path(profile: VoiceProfile, voice_id: str, text: str) -> Path:
    digest = hashlib.sha256(f"{voice_id}|{config.ELEVENLABS_MODEL}|{profile.stability}|{profile.style}|{text}".encode()).hexdigest()
    return Path(config.VOICE_CACHE_DIR) / f"{digest}.mp3"


def synthesize(text: str, agent_key: str | None) -> tuple[bytes, dict]:
    """MP3 bytes of `text` in the agent's voice. Returns (audio, info)."""
    if not config.ELEVENLABS_API_KEY:
        raise VoiceNotConfigured("Voice output is off: set ELEVENLABS_API_KEY on the backend")
    spoken = clip(speakable(text), config.VOICE_MAX_CHARS)
    if not spoken:
        raise VoiceError("Nothing to say")
    profile = profile_for(agent_key)
    voice_id, _ = resolve(profile)
    path = _cache_path(profile, voice_id, spoken)
    info = {"voice": profile.key, "chars": len(spoken), "cached": True}
    if path.exists():
        return path.read_bytes(), info
    _charge(len(spoken))
    try:
        res = _tts_request(profile, voice_id, spoken)
        for attempt in range(MAX_VOICE_PROBES):
            if not _voice_missing(res):
                break
            # first re-read the account's voices (they may have changed); if that can't help, probe the pool
            retry_id = resolve(profile, force=True)[0] if attempt == 0 else voice_id
            if retry_id == voice_id:
                retry_id = _next_fallback(profile, voice_id)
                if retry_id is None:
                    break
            voice_id, path = retry_id, _cache_path(profile, retry_id, spoken)
            res = _tts_request(profile, voice_id, spoken)
    except requests.RequestException as exc:
        _charge(-len(spoken))
        raise VoiceError(f"ElevenLabs unreachable: {exc}") from exc
    if res.status_code != 200:
        _charge(-len(spoken))
        if _voice_missing(res):
            raise VoiceError("None of the standard ElevenLabs voices are available to this API key. Add any voice "
                             "under My Voices in ElevenLabs and try again.")
        raise VoiceError(f"ElevenLabs error {res.status_code}: {_error_text(res)}")
    audio = res.content
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    except OSError as exc:
        logger.warning("Could not cache voice clip: %s", exc)
    return audio, {**info, "cached": False}


def _tts_request(profile: VoiceProfile, voice_id: str, spoken: str) -> requests.Response:
    return requests.post(
        TTS_URL.format(voice_id=voice_id),
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Accept": "audio/mpeg"},
        json={"text": spoken, "model_id": config.ELEVENLABS_MODEL,
              "voice_settings": {"stability": profile.stability, "similarity_boost": 0.8,
                                 "style": profile.style, "use_speaker_boost": True}},
        timeout=60,
    )


def _voice_missing(res: requests.Response) -> bool:
    return res.status_code == 404 and "voice" in _error_text(res).lower()


# --- Speech to text --------------------------------------------------------------------------

EXTENSIONS = {"webm": "webm", "ogg": "ogg", "mp4": "mp4", "m4a": "m4a", "x-m4a": "m4a", "aac": "m4a",
              "mpeg": "mp3", "mp3": "mp3", "wav": "wav", "x-wav": "wav", "flac": "flac"}


# Keyterm prompting biases Scribe toward these words (tickers, company names). It adds 20% to the
# transcription cost, and more than 100 keyterms imposes a 20-second minimum bill per request, so cap at 100.
MAX_KEYTERMS = 100
_KEYTERM_BANNED = str.maketrans("", "", "<>{}[]\\")


def keyterms(vocabulary: list[str] | None) -> list[str]:
    """Clean, de-duplicated keyterms within ElevenLabs' limits (<50 chars, <=5 words, no <>{}[] or backslash)."""
    out, seen = [], set()
    for term in vocabulary or []:
        term = re.sub(r"\s*\([^)]*\)?", "", str(term))  # "S&P 500 (SPY)" -> "S&P 500"; the symbol is its own keyterm
        term = " ".join(term.translate(_KEYTERM_BANNED).split()[:5])[:49].strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
        if len(out) == MAX_KEYTERMS:
            break
    return out


def transcribe(audio: bytes, content_type: str, vocabulary: list[str] | None = None) -> str:
    """ElevenLabs Scribe transcription. `vocabulary` (tickers, company names) is sent as keyterms so symbols spell right."""
    if not config.ELEVENLABS_API_KEY:
        raise VoiceNotConfigured("Voice input is off: set ELEVENLABS_API_KEY on the backend")
    if not audio:
        raise VoiceError("No audio received")
    if len(audio) > STT_MAX_BYTES:
        raise VoiceError("Recording is too long (25 MB max)")
    subtype = (content_type or "audio/webm").split(";")[0].split("/")[-1].lower()
    ext = EXTENSIONS.get(subtype, "webm")
    # a list value makes requests send one "keyterms" form part per term
    data = {"model_id": config.ELEVENLABS_STT_MODEL, "language_code": "en", "tag_audio_events": "false"}
    if terms := keyterms(vocabulary):
        data["keyterms"] = terms
    try:
        res = requests.post(STT_URL, headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                            files={"file": (f"speech.{ext}", audio, content_type or "audio/webm")}, data=data, timeout=60)
    except requests.RequestException as exc:
        raise VoiceError(f"ElevenLabs unreachable: {exc}") from exc
    if res.status_code != 200:
        raise VoiceError(f"ElevenLabs speech-to-text error {res.status_code}: {_error_text(res)}")
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
