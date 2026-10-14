"""Live settings an admin can change without a restart (ADR 0010).

A row in `runtime_settings` means "an admin chose this value"; no row means the environment (or the built-in default) applies, so
clearing a control hands it back to them. Reads are cached in-process for 5 seconds, so a change reaches every worker within 5 s and
the hot paths (every SMS send, every plan) do not query the database each time.

Reading never raises. If the table is missing (a database not yet migrated) or the database is slow, the caller gets its default and
the app carries on (I3: a lever for operators must never become a cause of outages).

    switches   `switch:<name>`   True = the thing is stopped        (killswitch.killed reads these)
    numbers    `<key>`           a bounded whole number              (brownout level, free plans per account)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("tripai.runtime")

CACHE_SECONDS = 5.0
_MISSING = object()
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_warned = False


@dataclass(frozen=True)
class Switch:
    name: str
    label: str
    effect: str  # what turning it ON does
    people_see: str  # and what the person on the site sees


@dataclass(frozen=True)
class Number:
    key: str
    label: str
    hint: str
    low: int
    high: int


SWITCHES: dict[str, Switch] = {s.name: s for s in (
    Switch("signups", "New sign-ups", "Nobody can create a new account. Signing in is unaffected.",
           "“We're not taking new sign-ups for a little while. Please try again later.”"),
    Switch("plans", "New plans and re-plans", "No trip is planned or re-planned. Trips are saved and no free plan is used.",
           "The calm “we're busy” screen with a time to come back; the trip details are kept."),
    Switch("sms", "Text messages (verification codes)", "No SMS is sent, so nobody can finish creating an account.",
           "“We can't send text codes right now. Please try again a little later.”"),
    Switch("plan_emails", "Plan emails", "No email is sent when a plan is ready, and the “Email me this plan” button stops.",
           "Nothing: plans are simply not emailed. The plan itself and WhatsApp sharing are unaffected."),
    Switch("sharing", "Share links", "Nobody can create or open a share link.",
           "The share button says sharing is off; existing links show “not found”."),
    Switch("referrals", "Invite-a-friend rewards", "Invite codes stop working: new accounts get no bonus and inviters earn nothing. Credits already given stay.",
           "The Invite friends card says invites are paused; a code typed at sign-up is ignored."),
    Switch("ai_narration", "AI-written trip descriptions", "New plans use the plain template text instead of an AI summary (saves model spend).",
           "“Trip description simplified to keep things fast” in the plan's notes."),
)}

NUMBERS: dict[str, Number] = {n.key: n for n in (
    Number("brownout_level", "Busy mode level", "0 = normal. 1 template text · 2 + no alternatives · 3 + fewer options compared · 4 + saved train timetables only.", 0, 4),
    Number("free_trip_plans", "Free plans per month", "How many new trip plans a free account gets each month (the count starts again on the 1st).", 0, 50),
    Number("referral_reward_friend", "Invite: plans for the friend", "Extra free trip plans a new account gets when it signs up with a friend's code.", 0, 10),
    Number("referral_reward_inviter", "Invite: plans for the inviter", "Extra free trip plans the inviter gets when their friend's first plan is ready.", 0, 10),
    Number("referral_plus_discount_pct", "Invite: Plus discount for the friend (%)", "Off the friend's first month of Plus. 0 = no discount.", 0, 90),
    Number("referral_monthly_cap", "Invite: rewards per inviter per month", "After this many rewarded friends in a month, more friends still join but the inviter earns nothing more that month. 0 = no cap.", 0, 100),
    Number("daily_plan_cap", "New plans per day (whole site)", "After this many new plans today, everyone (Plus too) is told new plans reopen at midnight. Each plan costs up to about $0.36 of Google calls, so keep it under GOOGLE_DAILY_USD / 0.36 (28 for $10), or raise that budget too. 0 = no cap.", 0, 5000),
)}


def _load(key: str) -> Any:
    global _warned
    try:
        from app.db.session import SessionLocal
        from app.models.admin import RuntimeSetting
        with SessionLocal() as db:
            row = db.get(RuntimeSetting, key)
            return _MISSING if row is None else row.value
    except Exception:  # noqa: BLE001 - never let a lever take the site down
        if not _warned:
            _warned = True
            log.warning("runtime settings could not be read; using the environment and defaults (is migration 0013 applied?)", exc_info=True)
        return _MISSING


def get(key: str, default: Any = None) -> Any:
    """The admin's value for `key`, or `default` when no admin has chosen one."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            value = hit[1]
            return default if value is _MISSING else value
    value = _load(key)
    with _lock:
        _cache[key] = (now, value)
    return default if value is _MISSING else value


def invalidate(key: str | None = None) -> None:
    """Forget what was read, so the next read sees a change made in this process straight away (other workers within 5 s)."""
    with _lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


def put(db, key: str, value: Any, reason: str | None, actor_email: str) -> None:
    """Choose a value (insert or replace). The caller commits, then calls `invalidate`."""
    from app.models.admin import RuntimeSetting
    row = db.get(RuntimeSetting, key)
    now = datetime.now(UTC)
    if row is None:
        db.add(RuntimeSetting(key=key, value=value, reason=(reason or None), updated_by=actor_email[:255], updated_at=now))
    else:
        row.value, row.reason, row.updated_by, row.updated_at = value, (reason or None), actor_email[:255], now


def remove(db, key: str) -> bool:
    """Give the control back to the environment. The caller commits, then calls `invalidate`."""
    from app.models.admin import RuntimeSetting
    row = db.get(RuntimeSetting, key)
    if row is None:
        return False
    db.delete(row)
    return True


# --- typed accessors the rest of the app uses ------------------------------------------------------------------------------

def number(key: str, default: int) -> int:
    v = get(key)
    spec = NUMBERS.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        return default
    return max(spec.low, min(spec.high, v)) if spec else v


def free_trip_plans() -> int:
    from app.core.config import get_settings
    return number("free_trip_plans", get_settings().free_trip_plans)


def share_links_enabled() -> bool:
    from app.core.config import get_settings
    from app.core.killswitch import killed
    return get_settings().share_links_enabled and not killed("sharing")
