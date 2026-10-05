"""SMS price alerts via Twilio.

The price check (every PRICE_CHECK_MINUTES in market hours) hands each fresh quote to check_price_alerts().
A covered ticker that is up or down ALERT_MOVE_PCT (default 5%) or more vs. the previous close gets a text
from its agent, to every number in ALERT_PHONE_NUMBERS. Each 5% band alerts once per trading day: +5%,
then +10% if it keeps going, and separately -5% if it reverses. Every alert is logged in sms_alerts,
including ones that could not be sent (Twilio not configured, send failure).
"""

import logging
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import session_scope
from database.models import SmsAlert
from utils import config

logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
_client = None


class SmsNotConfigured(RuntimeError):
    pass


def _twilio():
    global _client
    if not config.sms_configured():
        raise SmsNotConfigured(
            "Twilio is not configured: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER "
            "(or TWILIO_MESSAGING_SERVICE_SID) and ALERT_PHONE_NUMBERS")
    if _client is None:
        from twilio.rest import Client  # imported lazily so the app runs without Twilio installed/configured

        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def send_sms(body: str, to: list[str] | None = None) -> list[dict]:
    """Text `body` to each number (default ALERT_PHONE_NUMBERS). Returns one result per number; never raises
    for a single bad number, but raises SmsNotConfigured if Twilio isn't set up."""
    client = _twilio()
    sender = ({"messaging_service_sid": config.TWILIO_MESSAGING_SERVICE_SID} if config.TWILIO_MESSAGING_SERVICE_SID
              else {"from_": config.TWILIO_FROM_NUMBER})
    results = []
    for number in to or config.ALERT_PHONE_NUMBERS:
        try:
            msg = client.messages.create(body=body, to=number, **sender)
            results.append({"to": _mask(number), "ok": True, "sid": msg.sid, "status": msg.status})
        except Exception as exc:  # TwilioRestException carries a readable message (bad number, unverified, ...)
            logger.warning("SMS to %s failed: %s", _mask(number), exc)
            results.append({"to": _mask(number), "ok": False, "error": str(getattr(exc, "msg", exc))})
    return results


def _mask(number: str) -> str:
    return f"...{number[-4:]}" if len(number) > 4 else number


def _band(change_pct: float) -> int:
    """+1 for +5..10%, +2 for +10..15%, -1 for -5..-10%, ... (0 below the threshold)."""
    step = config.ALERT_MOVE_PCT
    if step <= 0 or abs(change_pct) < step:
        return 0
    return int(math.copysign(math.floor(abs(change_pct) / step), change_pct))


def format_alert(ctx, quote: dict, thesis: dict | None) -> str:
    chg = quote["change_pct"]
    # Plain GSM-7 characters only: arrows/em-dashes force UCS-2 and roughly triple the billed segments.
    parts = [f"{ctx.symbol} {'UP' if chg > 0 else 'DOWN'} {chg:+.1f}% today to ${quote['price']:,.2f}"]
    if quote.get("prev_close"):
        parts[0] += f" (prev close ${quote['prev_close']:,.2f})"
    t = thesis or {}
    if t.get("signal"):
        call = f"Current call: {t['signal'].upper()}"
        if t.get("target_price") is not None:
            call += f", target ${t['target_price']:,.2f}"
        if t.get("stop_loss") is not None:
            call += f", stop ${t['stop_loss']:,.2f}"
        parts.append(call + ".")
    else:
        parts.append("No thesis yet (initial research pending).")
    parts.append(f"- {ctx.analyst_name}, Analyst Town")
    return "\n".join(parts)


def check_price_alerts(ctx, quote: dict, thesis: dict | None) -> dict | None:
    """Alert if this quote crosses a new 5% band today. Returns the logged alert, or None if nothing new."""
    chg = quote.get("change_pct")
    if not config.SMS_ALERTS_ENABLED or chg is None or quote.get("price") is None:
        return None
    band = _band(chg)
    if band == 0:
        return None
    day = datetime.now(EASTERN).date().isoformat()
    message = format_alert(ctx, quote, thesis)
    # Claim the (ticker, day, band) slot first so concurrent price checks never double-text.
    try:
        with session_scope() as s:
            row = SmsAlert(symbol=ctx.symbol, alert_date=day, band=band, change_pct=chg, price=quote["price"],
                           message=message, status="pending")
            s.add(row)
            s.flush()
            alert_id = row.id
    except IntegrityError:
        return None  # already alerted for this band today

    try:
        results = send_sms(message)
        sent = sum(r["ok"] for r in results)
        status = "sent" if sent == len(results) else "partial" if sent else "failed"
        detail = "; ".join(f"{r['to']}: {r.get('sid') or r.get('error')}" for r in results)
    except SmsNotConfigured as exc:
        status, detail = "skipped", str(exc)
    except Exception as exc:
        logger.exception("SMS alert for %s failed", ctx.symbol)
        status, detail = "failed", f"{type(exc).__name__}: {exc}"
    with session_scope() as s:
        row = s.get(SmsAlert, alert_id)
        row.status, row.detail = status, detail
    logger.info("Price alert %s %+.1f%% (band %+d): %s", ctx.symbol, chg, band, status)
    return {"id": alert_id, "symbol": ctx.symbol, "change_pct": chg, "band": band, "status": status}


def recent_alerts(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(SmsAlert).order_by(SmsAlert.created_at.desc()).limit(limit)).all()
        return [{"id": r.id, "symbol": r.symbol, "date": r.alert_date, "band": r.band, "change_pct": r.change_pct,
                 "price": r.price, "status": r.status, "detail": r.detail, "message": r.message,
                 "created_at": r.created_at.isoformat()} for r in rows]
