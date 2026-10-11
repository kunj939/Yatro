"""Plan quota: FREE = 2 lifetime trip plans; PLUS = unlimited while active; ONE TRIP = one credit per trip.

Enforced in the backend with an atomic conditional UPDATE, so two concurrent requests can't both use the last
free plan. Replans of an already-planned trip never consume quota. A failed or infeasible plan is refunded.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core import runtime
from app.core.config import get_settings
from app.core.errors import AppError, QuotaExceeded
from app.models.trip import Trip
from app.models.user import Subscription, User

def quota_message() -> str:
    n = runtime.free_trip_plans()
    return f"Your {n} free trip plan{'s are' if n != 1 else ' is'} used."


def get_subscription(db: Session, user: User) -> Subscription:
    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id, plan="free", status="active", trip_credits=0, provider="mock")
        db.add(sub)
        db.flush()
        user.subscription = sub
    return sub


def plus_active(sub: Subscription) -> bool:
    if sub.plan != "plus" or sub.status != "active":
        return False
    end = sub.current_period_end
    if end is None:
        return True
    end = end if end.tzinfo else end.replace(tzinfo=UTC)
    return end > datetime.now(UTC)


def quota_status(db: Session, user: User) -> dict:
    s = get_settings()
    sub = get_subscription(db, user)
    free_total = runtime.free_trip_plans()  # the setting, or what an admin chose in the console
    left = max(0, free_total - user.free_plans_used)
    plus = plus_active(sub)
    return {"plan": "plus" if plus else "free", "status": sub.status, "free_plans_total": free_total,
            "free_plans_used": min(user.free_plans_used, free_total), "free_plans_left": left,
            "trip_credits": sub.trip_credits, "can_plan": plus or left > 0 or sub.trip_credits > 0,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end and plus else None,
            "prices": {"plus_monthly_inr": s.plus_price_inr, "one_trip_inr": s.one_trip_price_inr},
            "payments_mode": s.payments_mode}


def reserve(db: Session, user: User, trip: Trip) -> str | None:
    """Consume one plan for this trip if it hasn't consumed one yet. Returns the source used (or None)."""
    if trip.quota_consumed:
        return None
    sub = get_subscription(db, user)
    if plus_active(sub):
        source = "plus"
    else:
        res = db.execute(update(User).where(User.id == user.id, User.free_plans_used < runtime.free_trip_plans())
                         .values(free_plans_used=User.free_plans_used + 1))
        if res.rowcount == 1:
            source = "free"
        else:
            res = db.execute(update(Subscription).where(Subscription.id == sub.id, Subscription.trip_credits > 0)
                             .values(trip_credits=Subscription.trip_credits - 1))
            if res.rowcount != 1:
                raise QuotaExceeded(quota_message(), details=quota_status(db, user))
            source = "credit"
    trip.quota_consumed, trip.quota_source = True, source
    db.flush()
    db.refresh(user)
    return source


def refund(db: Session, user: User, trip: Trip, reason: str = "failed") -> None:
    """Give the plan back when planning failed or the trip was infeasible (the user got no itinerary).

    Exactly-once per plan job (ADR 0008): inside a job the refund first inserts a record keyed by the job id (unique), so a
    crashed-then-reaped job, a failing planner and a retry can all call this and only one of them changes anything."""
    if not trip.quota_consumed or trip.plan_count:
        return
    from app.services import plan_jobs
    job_id = plan_jobs.CURRENT_JOB.get()
    if job_id is not None and not plan_jobs.record_refund(db, job_id, reason):
        return  # already refunded for this job
    if trip.quota_source == "free":
        db.execute(update(User).where(User.id == user.id, User.free_plans_used > 0)
                   .values(free_plans_used=User.free_plans_used - 1))
    elif trip.quota_source == "credit":
        db.execute(update(Subscription).where(Subscription.user_id == user.id)
                   .values(trip_credits=Subscription.trip_credits + 1))
    trip.quota_consumed, trip.quota_source = False, None
    db.flush()


def upgrade(db: Session, user: User, product: str) -> dict:
    s = get_settings()
    if s.payments_mode == "disabled":
        raise AppError("Payments are not enabled yet.", code="payments_disabled", status_code=503)
    sub = get_subscription(db, user)
    now = datetime.now(UTC)
    if product == "plus":
        sub.plan, sub.status = "plus", "active"
        sub.current_period_end = now + timedelta(days=30)
        sub.provider, sub.provider_ref = "mock", f"mock_plus_{int(now.timestamp())}"
    elif product == "one_trip":
        sub.trip_credits += 1
        sub.provider, sub.provider_ref = "mock", f"mock_trip_{int(now.timestamp())}"
    else:
        raise AppError("Unknown product.", code="unknown_product", status_code=422)
    db.flush()
    return quota_status(db, user)


def cancel(db: Session, user: User) -> dict:
    sub = get_subscription(db, user)
    if sub.plan == "plus":
        sub.status = "canceled"
    db.flush()
    return quota_status(db, user)
