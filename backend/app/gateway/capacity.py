"""When a plan can't be made right now (ADR 0007, invariant I3).

A required provider that is down, an exhausted Google budget, or the day's plan cap each end in the same typed answer, with
HTTP 200, never an error:

    {"state": "unavailable", "status": "unavailable", "reason": "provider_busy" | "daily_cap",
     "retry_at": "<ISO time>", "quota_used": false, "message": ..., "trip": {...}}

The free plan is never used (I4): the checks run before the quota is reserved, and a failure mid-plan refunds it.

The plan cap (DAILY_PLAN_CAP) sits below what the Google budget can pay for, so people meet the polite cap message before
Google is cut off. `check_consistency` logs a warning if the two settings disagree.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.gateway.breaker import breakers
from app.gateway.budget import budgets
from app.gateway.errors import BudgetExhausted, GatewayError

log = logging.getLogger("tripai.gateway.capacity")
MESSAGE = "We're busy right now. None of your free plans were used, and your trip details are saved."
CAP_MESSAGE = "We've reached today's limit on new plans. None of your free plans were used, and your trip details are saved."
MIN_WAIT = timedelta(minutes=2)
DEFAULT_WAIT = timedelta(minutes=5)


PAUSED_MESSAGE = ("Planning is paused for a little while. None of your free plans were used, and your trip details are saved. "
                  "Please come back soon.")
PAUSED_WAIT = timedelta(minutes=30)


@dataclass
class Unavailable:
    reason: str  # provider_busy | daily_cap | paused
    retry_at: datetime
    message: str


def check_paused() -> Unavailable | None:
    """The admin console's "new plans and re-plans" switch (ADR 0010). Checked before anything is reserved, for new plans and
    re-plans alike, and answered like every other "can't right now": HTTP 200, `unavailable`, no free plan used (I3, I4)."""
    from app.core import killswitch
    if killswitch.killed("plans"):
        return Unavailable("paused", datetime.now(UTC) + PAUSED_WAIT, PAUSED_MESSAGE)
    return None


def _at_least(when: datetime | None, floor: timedelta = MIN_WAIT) -> datetime:
    soonest = datetime.now(UTC) + floor
    if when is None:
        return datetime.now(UTC) + DEFAULT_WAIT
    return when if when > soonest else soonest


def _live(providers) -> bool:
    s = get_settings()
    return s.gateway_enabled and s.budget_mode != "off" and bool(getattr(providers.places, "live", False))


def check_new_plan(providers) -> Unavailable | None:
    """Before a new plan reserves the quota: is there room today, and are the required (Google) sources open?"""
    s = get_settings()
    if not _live(providers):
        return None
    if budgets.exhausted("plans"):
        return Unavailable("daily_cap", budgets.reset_at(), CAP_MESSAGE)
    if budgets.exhausted("google"):
        return Unavailable("provider_busy", budgets.reset_at(), MESSAGE)
    if s.gateway_breakers:
        for prov in ("google-places", "google-routes"):  # places are required; a routes outage degrades, it doesn't stop
            if prov == "google-places" and breakers.is_open(prov):
                until = breakers.open_until(prov)
                return Unavailable("provider_busy", _at_least(datetime.fromtimestamp(until, UTC) if until else None), MESSAGE)
    return None


def count_plan(providers) -> None:
    """A new plan starts: count it against DAILY_PLAN_CAP. (A race can overshoot by a plan or two; the Google budget is the
    hard stop.)"""
    if _live(providers):
        try:
            budgets.reserve("plans", 1)
        except BudgetExhausted:
            pass


def uncount_plan(providers) -> None:
    if _live(providers):
        budgets.settle("plans", 1, 0)


def from_exception(exc: ProviderUnavailable) -> Unavailable:
    """A required provider failed mid-plan (the quota is refunded by the planner)."""
    if isinstance(exc, BudgetExhausted):
        return Unavailable("provider_busy", exc.retry_at or budgets.reset_at(), MESSAGE)
    when = exc.retry_at if isinstance(exc, GatewayError) else None
    return Unavailable("provider_busy", _at_least(when), MESSAGE)


def payload(u: Unavailable, trip_out: dict) -> dict:
    return {"state": "unavailable", "status": "unavailable", "reason": u.reason, "retry_at": u.retry_at.isoformat(),
            "quota_used": False, "message": u.message, "feasibility": None, "trip": trip_out}


def check_consistency() -> None:
    s = get_settings()
    need = s.daily_plan_cap * s.cold_plan_worst_usd
    if s.budget_mode != "off" and need > s.google_daily_usd:
        log.warning("DAILY_PLAN_CAP=%d cold plans at up to $%.2f each needs $%.2f a day, but GOOGLE_DAILY_USD is $%.2f: "
                    "Google would be cut off before the plan cap applies", s.daily_plan_cap, s.cold_plan_worst_usd, need,
                    s.google_daily_usd)
