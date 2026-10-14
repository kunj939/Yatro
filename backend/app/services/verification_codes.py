"""One-time codes that prove a traveller owns the email address and mobile number they sign up with.

A code is six random digits, valid for OTP_TTL_MINUTES, good for OTP_MAX_ATTEMPTS tries, and only a keyed hash of it
is stored. Asking again retires the previous code (after a short OTP_RESEND_SECONDS wait, so nobody can flood a
phone with texts).

Email goes out by SMTP and texts by Twilio or Fast2SMS. Without them configured, the code is written to the server
log and, outside production, handed back to the sign-up page so the whole flow can be tried locally; production
without a provider refuses to send rather than pretending it did.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core import killswitch
from app.core.errors import AppError, RateLimited
from app.gateway.budget import budgets
from app.gateway.errors import BudgetExhausted
from app.models.user import VerificationCode

log = logging.getLogger("tripai.verification")
BRAND = "Yaitro"


# --- Mobile numbers -------------------------------------------------------------------------------------------------

INDIA_ONLY = "Only Indian mobile numbers (+91) are supported for now."


def normalize_phone(raw: str) -> str:
    """Any common way of writing an INDIAN mobile number -> +91XXXXXXXXXX ("098765 43210" and "+91 98765-43210" ->
    +919876543210). A bare 10-digit number starting 6-9 is taken as Indian. Anything else is refused: a text to another country is
    what SMS-pumping fraud abuses, and every text is paid for. Raises ValueError with a message fit for the form."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        if re.fullmatch(r"0?[6-9]\d{9}", s):
            s = "+91" + s[-10:]
        else:
            raise ValueError("Enter your 10-digit Indian mobile number, for example +91 98765 43210.")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", s):
        raise ValueError("Enter a valid mobile number.")
    if not s.startswith("+91"):
        raise ValueError(INDIA_ONLY)
    if not re.fullmatch(r"\+91[6-9]\d{9}", s):
        raise ValueError("An Indian mobile number has 10 digits and starts with 6, 7, 8 or 9.")
    return s


def mask(channel: str, target: str) -> str:
    """k•••••@outlook.com, +91 •••••• 3210: enough to recognise, not enough to read off a screen."""
    if channel == "email":
        name, _, domain = target.partition("@")
        return f"{name[:1]}{'•' * max(1, min(6, len(name) - 1))}@{domain}"
    return f"{target[:-10] if len(target) > 10 else target[:3]} •••••• {target[-4:]}"


# --- Codes ---------------------------------------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite hands timestamps back without a zone


def _hash(channel: str, target: str, code: str) -> str:
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{channel}:{target}:{code}".encode(), hashlib.sha256).hexdigest()


def _latest(db: Session, channel: str, target: str, purpose: str) -> VerificationCode | None:
    return db.scalar(select(VerificationCode)
                     .where(VerificationCode.channel == channel, VerificationCode.target == target,
                            VerificationCode.purpose == purpose, VerificationCode.consumed_at.is_(None))
                     .order_by(VerificationCode.created_at.desc()).limit(1))


def ip_hash(ip: str | None) -> str | None:
    """A keyed hash of the requesting address: enough to count requests per address, not enough to read the address back."""
    if not ip:
        return None
    return hmac.new(get_settings().jwt_secret.encode(), f"otp-ip:{ip}".encode(), hashlib.sha256).hexdigest()


def _since(db: Session, since: datetime, *where) -> tuple[int, datetime | None]:
    """How many codes were asked for since `since` (used or not), and when the oldest of them was."""
    row = db.execute(select(func.count(), func.min(VerificationCode.created_at)).where(VerificationCode.created_at >= since, *where)).one()
    return int(row[0]), _aware(row[1]) if row[1] else None


def _minutes_until(oldest: datetime | None, window: timedelta, now: datetime) -> int:
    return max(1, int(((oldest or now) + window - now).total_seconds() // 60) + 1)


def issue(db: Session, channel: str, target: str, purpose: str = "signup", ip: str | None = None) -> str:
    """A fresh code for this address or number (the previous one stops working: only one is ever live). Returns the code itself,
    to send.

    Limits, all counted from the table so they hold across workers and restarts, and identical whether or not the number or
    address already has an account (nothing here depends on that): one a minute per target (OTP_RESEND_SECONDS), OTP_PER_PHONE_HOUR
    an hour and OTP_PER_PHONE_DAY a day per target, and OTP_PER_IP_HOUR an hour per requesting address (0 = no limit)."""
    s = get_settings()
    now = datetime.now(UTC)
    where = "email address" if channel == "email" else "number"
    last = _latest(db, channel, target, purpose)
    if last is not None:
        wait = s.otp_resend_seconds - int((now - _aware(last.created_at)).total_seconds())
        if wait > 0:
            raise RateLimited(f"Please wait {wait} seconds before asking for another code.",
                              details={"retry_in": wait})
    same = (VerificationCode.channel == channel, VerificationCode.target == target, VerificationCode.purpose == purpose)
    if s.otp_per_phone_hour:
        n, oldest = _since(db, now - timedelta(hours=1), *same)
        if n >= s.otp_per_phone_hour:
            m = _minutes_until(oldest, timedelta(hours=1), now)
            raise RateLimited(f"We've already sent several codes to this {where}. Please try again in about {m} minute{'s' if m != 1 else ''}.",
                              details={"retry_in": m * 60})
    if s.otp_per_phone_day:
        n, oldest = _since(db, now - timedelta(days=1), *same)
        if n >= s.otp_per_phone_day:
            raise RateLimited(f"We've sent the most codes we can to this {where} today. Please try again tomorrow.",
                              details={"retry_in": _minutes_until(oldest, timedelta(days=1), now) * 60})
    h = ip_hash(ip)
    if h and s.otp_per_ip_hour:
        n, oldest = _since(db, now - timedelta(hours=1), VerificationCode.requested_ip_hash == h)
        if n >= s.otp_per_ip_hour:
            m = _minutes_until(oldest, timedelta(hours=1), now)
            raise RateLimited(f"Too many codes have been asked for from this connection. Please try again in about {m} minute{'s' if m != 1 else ''}.",
                              details={"retry_in": m * 60})
    db.execute(update(VerificationCode)
               .where(VerificationCode.channel == channel, VerificationCode.target == target,
                      VerificationCode.purpose == purpose, VerificationCode.consumed_at.is_(None))
               .values(consumed_at=now))
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db.add(VerificationCode(channel=channel, target=target, purpose=purpose, code_hash=_hash(channel, target, code),
                            expires_at=now + timedelta(minutes=s.otp_ttl_minutes), attempts=0, created_at=now, requested_ip_hash=h))
    db.flush()
    return code


def check(db: Session, channel: str, target: str, code: str, purpose: str = "signup") -> tuple[VerificationCode | None, str | None]:
    """(the matching code row, None) when right; (None, what to tell the traveller) when not. A wrong try counts
    against the code, so the caller must commit even when it then refuses."""
    s = get_settings()
    row = _latest(db, channel, target, purpose)
    where = "email" if channel == "email" else "phone"
    if row is None:
        return None, f"Ask for a code to your {where} first."
    if _aware(row.expires_at) < datetime.now(UTC):
        return None, "This code has expired. Ask for a new one."
    if row.attempts >= s.otp_max_attempts:
        return None, "Too many wrong tries. Ask for a new code."
    if not hmac.compare_digest(row.code_hash, _hash(channel, target, re.sub(r"\D", "", code or ""))):
        row.attempts += 1
        left = s.otp_max_attempts - row.attempts
        return None, "That code isn't right. " + (f"{left} tr{'y' if left == 1 else 'ies'} left." if left
                                                  else "Ask for a new code.")
    return row, None


# --- Sending ---------------------------------------------------------------------------------------------------------

@dataclass
class Sent:
    via: str  # smtp | twilio | fast2sms | console

    @property
    def console(self) -> bool:
        return self.via == "console"


def _message(code: str) -> str:
    return (f"{code} is your {BRAND} verification code. It expires in {get_settings().otp_ttl_minutes} minutes. "
            f"Don't share it with anyone.")


def _console_or_refuse(channel: str, target: str, code: str) -> Sent:
    if get_settings().environment == "production":
        what = "Email" if channel == "email" else "SMS"
        log.error("%s sending is not configured; can't send a verification code to %s", what, mask(channel, target))
        raise AppError(f"We can't send codes by {what.lower()} right now. Please try again later.",
                       code="delivery_unavailable", status_code=503)
    log.warning("DEV MODE (no %s provider configured): verification code for %s is %s",
                "email" if channel == "email" else "SMS", target, code)
    return Sent("console")


def send_email_code(to: str, code: str) -> Sent:
    s = get_settings()
    if not s.smtp_host:
        return _console_or_refuse("email", to, code)
    msg = EmailMessage()
    msg["Subject"] = f"{code} is your {BRAND} code"
    msg["From"] = s.smtp_from or s.smtp_user or f"no-reply@{s.smtp_host}"
    msg["To"] = to
    msg.set_content(f"Hi,\n\n{_message(code)}\n\nIf you didn't try to create a {BRAND} account, ignore this email.\n")
    msg.add_alternative(
        f"""<div style="font-family:system-ui,sans-serif;max-width:420px;margin:auto;padding:24px">
<p style="font-size:15px;color:#333">Your {BRAND} verification code:</p>
<p style="font-size:34px;font-weight:700;letter-spacing:8px;margin:12px 0">{code}</p>
<p style="font-size:14px;color:#666">It expires in {s.otp_ttl_minutes} minutes. Don't share it with anyone.<br>
If you didn't try to create a {BRAND} account, ignore this email.</p></div>""", subtype="html")
    try:
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=ssl.create_default_context()) as smtp:
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.error("Couldn't send the verification email to %s: %s", mask("email", to), exc)
        if s.environment == "development":
            # On a developer's machine a blocked or misconfigured mail server must not lock them out of sign-up: show the code
            # (printed in the server log and returned to the page) exactly as when no mail server is set. Never in production.
            return _console_or_refuse("email", to, code)
        raise AppError("We couldn't send the email just now. Check the address and try again.",
                       code="delivery_failed", status_code=502) from exc
    return Sent("smtp")


def _sms_provider() -> str:
    s = get_settings()
    if s.sms_provider != "auto":
        return s.sms_provider
    if s.twilio_account_sid and s.twilio_auth_token and s.twilio_from:
        return "twilio"
    if s.fast2sms_api_key:
        return "fast2sms"
    return "console"


SMS_PAUSED = "We can't send text codes right now. Please try again a little later."
SMS_CAP = "We've sent as many text codes as we can for today. Please try again tomorrow."


def send_sms_code(to: str, code: str) -> Sent:
    """Send the code by text. Each real text is counted in the budget ledger (SMS_DAILY_LIMIT a day, group `sms`, midnight in
    BUDGET_TZ) and stopped by the kill switch (KILL_SMS). A stop is a calm 429 with a plain sentence, never a 5xx, and no code is
    kept (the caller commits only after this returns)."""
    provider = _sms_provider()
    if killswitch.killed("sms"):
        raise AppError(SMS_PAUSED, code="sms_paused", status_code=429)
    reserved = 0.0
    if provider != "console":  # the console provider sends nothing and costs nothing
        try:
            reserved = budgets.reserve("sms", 1)
        except BudgetExhausted as exc:
            raise AppError(SMS_CAP, code="sms_daily_limit", status_code=429,
                           details={"retry_at": exc.retry_at.isoformat() if exc.retry_at else None}) from exc
    try:
        return _send_sms(to, code, provider)
    except BaseException:
        budgets.settle("sms", reserved, 0.0)  # it did not go out: give the count back
        raise


def _send_sms(to: str, code: str, provider: str) -> Sent:
    s = get_settings()
    try:
        if provider == "twilio":
            sender = {"MessagingServiceSid": s.twilio_from} if (s.twilio_from or "").startswith("MG") else {"From": s.twilio_from}
            r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                           auth=(s.twilio_account_sid or "", s.twilio_auth_token or ""),
                           data={"To": to, "Body": _message(code), **sender}, timeout=15)
            r.raise_for_status()
            return Sent("twilio")
        if provider == "fast2sms":
            if not to.startswith("+91"):
                raise AppError("Texts can only be sent to Indian (+91) numbers for now.", code="sms_country",
                               status_code=422, details=[{"field": "phone", "message": "Use an Indian (+91) mobile number."}])
            r = httpx.post("https://www.fast2sms.com/dev/bulkV2", headers={"authorization": s.fast2sms_api_key or ""},
                           json={"route": "otp", "variables_values": code, "numbers": to[3:]}, timeout=15)
            r.raise_for_status()
            if not (r.json() or {}).get("return", False):
                raise httpx.HTTPError(str(r.json())[:200])
            return Sent("fast2sms")
    except httpx.HTTPError as exc:
        # the provider's own words say why (an empty wallet, an unverified website, a wrong key): without them a 400 is a mystery
        why = (getattr(getattr(exc, "response", None), "text", "") or "")[:300]
        log.error("Couldn't send the verification SMS to %s via %s: %s %s", mask("phone", to), provider, exc, why)
        raise AppError("We couldn't send the text just now. Check the number and try again.",
                       code="delivery_failed", status_code=502) from exc
    return _console_or_refuse("phone", to, code)
