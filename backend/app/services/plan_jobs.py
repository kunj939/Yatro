"""Plan jobs: the durable half of planning (ADR 0008).

A plan request writes its job row in the same transaction as the quota change, so there is never a state where the quota is
spent and no job exists (the outbox). The job's life, and what happens to the quota at each step (the saga):

    pending --claim--> running --complete--> done          quota stays spent (an itinerary was made)
                          |                  failed         refunded once (infeasible, provider down, error)
                          |
         heartbeat stops (crash, deploy) --reaper--> pending again once, if young; otherwise refunded

A refund is exactly-once because of `plan_refunds.job_id` (unique): whoever inserts that row first does the refund, anyone
else finds the row already there and changes nothing. Every state change is a conditional UPDATE, so racing workers or
reapers cannot both win.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.plan_job import PlanJob, PlanRefund
from app.models.trip import Trip
from app.models.user import User

log = logging.getLogger("tripai.plan_jobs")

LANE_REPLAN, LANE_PAID, LANE_FREE = 0, 1, 2
ACTIVE = ("pending", "running")
CURRENT_JOB: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar("plan_job_id", default=None)


def _now() -> datetime:
    return datetime.now(UTC)


def lane_for(trip: Trip, is_replan: bool) -> int:
    """Replans first, then paid plans, then new free plans (Phase 6 adds a lowest lane for unverified accounts)."""
    if is_replan:
        return LANE_REPLAN
    return LANE_PAID if trip.quota_source in ("plus", "credit") else LANE_FREE


def lane_for_user(db: Session, user: User, is_replan: bool) -> int:
    """The lane a request will get, before its quota is reserved (reserve spends Plus, then a free plan, then a credit)."""
    if is_replan:
        return LANE_REPLAN
    from app.services import quota_service
    q = quota_service.quota_status(db, user)
    return LANE_PAID if q["plan"] == "plus" or q["free_plans_left"] == 0 else LANE_FREE


def derived_prefix(trip: Trip, kind: str, body_text: str) -> str:
    """Identifies "this request, for this trip" when the client sends no key."""
    return "d-" + hashlib.sha256(f"{trip.id}|{kind}|{body_text}".encode()).hexdigest()[:32] + "-"


def latest_for_trip(db: Session, trip_id: uuid.UUID) -> PlanJob | None:
    return db.scalar(select(PlanJob).where(PlanJob.trip_id == trip_id).order_by(PlanJob.created_at.desc()).limit(1))


def resolve_key(db: Session, user: User, trip: Trip, kind: str, header: str | None, body_text: str) -> tuple[str, PlanJob | None]:
    """The idempotency key for this request, and the job it already belongs to (if any).

    A key the client sent (one per tap of the Plan button) is exact: the same key is the same plan, even long after it ended.
    Without one, the same request for the same trip while that plan is still pending or running is the same plan (a double
    tap); once it has ended, a new request is a new plan, so a retry after a failure really runs again."""
    sent = clean_key(header)
    if sent:
        return sent, find(db, user.id, sent)
    prefix = derived_prefix(trip, kind, body_text)
    latest = latest_for_trip(db, trip.id)
    if latest is not None and latest.state in ACTIVE and latest.idempotency_key.startswith(prefix):
        return latest.idempotency_key, latest
    return prefix + (str(latest.id)[:8] if latest is not None else "0"), None

def clean_key(header: str | None) -> str | None:
    h = (header or "").strip()
    return h[:100] if h and all(c.isalnum() or c in "-_.:" for c in h) else None


def find(db: Session, user_id: uuid.UUID, key: str) -> PlanJob | None:
    return db.scalar(select(PlanJob).where(PlanJob.user_id == user_id, PlanJob.idempotency_key == key))


def create(db: Session, user: User, trip: Trip, *, kind: str, key: str, change_note: str | None) -> tuple[PlanJob, bool]:
    """Add the job in the caller's transaction (the caller commits, together with the quota change). Returns (job, created);
    `created` is False when the same key already has a job (a double tap, a retry): nothing else should be changed then."""
    existing = find(db, user.id, key)
    if existing is not None:
        return existing, False
    job = PlanJob(trip_id=trip.id, user_id=user.id, idempotency_key=key, kind=kind, lane=lane_for(trip, kind == "replan"),
                  state="pending", quota_source=trip.quota_source if kind == "new" else None, change_note=change_note,
                  attempt=0, heartbeat_at=_now())
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:  # two requests with one key at the same moment: the other one won
        existing = find(db, user.id, key)
        if existing is None:
            raise
        return existing, False
    return job, True


# --- state changes (all conditional; the loser of any race changes nothing) -----------------------------------------------

def claim(job_id: uuid.UUID) -> PlanJob | None:
    """pending -> running. None when somebody else already took it, or it was refunded."""
    with SessionLocal() as db:
        now = _now()
        res = db.execute(update(PlanJob).where(PlanJob.id == job_id, PlanJob.state == "pending")
                         .values(state="running", started_at=now, heartbeat_at=now, attempt=PlanJob.attempt + 1))
        db.commit()
        if res.rowcount != 1:
            return None
        return db.get(PlanJob, job_id)


def complete(job_id: uuid.UUID, state: str, *, result_status: str | None = None, error_code: str | None = None,
             duration_ms: int | None = None) -> bool:
    """running -> done | failed. False when the reaper got there first (the job was refunded or re-queued)."""
    with SessionLocal() as db:
        res = db.execute(update(PlanJob).where(PlanJob.id == job_id, PlanJob.state == "running")
                         .values(state=state, finished_at=_now(), result_status=result_status, error_code=error_code,
                                 duration_ms=duration_ms))
        db.commit()
        if res.rowcount != 1:
            # The reaper got there first: the job was refunded (or put back in the queue) while this plan was stalled. The person
            # keeps the plan and stays refunded; it is logged and counted, never undone (decided 2026-10-07).
            db.execute(update(PlanJob).where(PlanJob.id == job_id, PlanJob.late_finish_at.is_(None))
                       .values(late_finish_at=_now(), result_status=result_status))
            db.commit()
            log.warning("plan job %s finished late, after it had been taken back (stale heartbeat); the plan is kept and the refund stands", job_id)
        return res.rowcount == 1


def heartbeat(job_ids: list[uuid.UUID]) -> None:
    if not job_ids:
        return
    with SessionLocal() as db:
        db.execute(update(PlanJob).where(PlanJob.id.in_(job_ids), PlanJob.state.in_(ACTIVE)).values(heartbeat_at=_now()))
        db.commit()


def release(job_ids: list[uuid.UUID]) -> None:
    """A process that is shutting down gives up the jobs it holds: the reaper takes them at once instead of waiting out the
    heartbeat."""
    if not job_ids:
        return
    old = _now() - timedelta(days=1)
    with SessionLocal() as db:
        db.execute(update(PlanJob).where(PlanJob.id.in_(job_ids), PlanJob.state.in_(ACTIVE)).values(heartbeat_at=old))
        db.commit()


def record_refund(db: Session, job_id: uuid.UUID, reason: str) -> bool:
    """Insert the refund record. True: this caller does the refund. False: it was already done (the unique key said so)."""
    try:
        with db.begin_nested():
            db.add(PlanRefund(job_id=job_id, reason=reason[:40], created_at=_now()))
            db.flush()
        return True
    except IntegrityError:
        return False


def restore_trip(trip: Trip) -> None:
    trip.status = "ready" if trip.plan_count else "draft"


def refund_active(job_id: uuid.UUID, reason: str, *, only_stale_before: datetime | None = None) -> bool:
    """Take back a job that is still pending or running (a crash, a deploy, a refused enqueue): give the quota back once and
    restore the trip. Returns True when this call did it."""
    from app.services import quota_service
    with SessionLocal() as db:
        cond = [PlanJob.id == job_id, PlanJob.state.in_(ACTIVE)]
        if only_stale_before is not None:
            cond.append((PlanJob.heartbeat_at < only_stale_before) | PlanJob.heartbeat_at.is_(None))
        res = db.execute(update(PlanJob).where(*cond).values(state="refunded", finished_at=_now(), error_code=reason[:60]))
        if res.rowcount != 1:
            db.rollback()
            return False
        job = db.get(PlanJob, job_id)
        trip, user = db.get(Trip, job.trip_id), db.get(User, job.user_id)
        token = CURRENT_JOB.set(job_id)
        try:
            if trip is not None and user is not None:
                quota_service.refund(db, user, trip, reason=reason)
                if trip.status == "planning":
                    restore_trip(trip)
        finally:
            CURRENT_JOB.reset(token)
        db.commit()
        log.warning("plan job %s refunded (%s)", job_id, reason)
        return True


def reap(now: datetime | None = None) -> dict:
    """Find jobs whose process stopped heartbeating. Young ones (and only once) go back to `pending` for a live process to
    run; the rest are refunded. Safe to run in every process at the same time."""
    s = get_settings()
    now = now or _now()
    cutoff = now - timedelta(seconds=s.reaper_stale_seconds)
    requeued: list[uuid.UUID] = []
    refunded = 0
    with SessionLocal() as db:
        rows = db.execute(select(PlanJob.id, PlanJob.created_at, PlanJob.attempt, PlanJob.kind, PlanJob.state)
                          .where(PlanJob.state.in_(ACTIVE), (PlanJob.heartbeat_at < cutoff) | PlanJob.heartbeat_at.is_(None))
                          .limit(100)).all()
    for jid, created, attempt, _kind, _state in rows:
        created = created if created.tzinfo else created.replace(tzinfo=UTC)
        young = (now - created).total_seconds() < s.job_requeue_max_age_seconds and attempt < 2
        if young:
            with SessionLocal() as db:
                res = db.execute(update(PlanJob).where(PlanJob.id == jid, PlanJob.state.in_(ACTIVE),
                                                       (PlanJob.heartbeat_at < cutoff) | PlanJob.heartbeat_at.is_(None))
                                 .values(state="pending", heartbeat_at=now, started_at=None))
                db.commit()
            if res.rowcount == 1:
                requeued.append(jid)
        elif refund_active(jid, "stale", only_stale_before=cutoff):
            refunded += 1
    return {"requeued": requeued, "refunded": refunded}


def purge_old(db: Session, now: datetime | None = None) -> int:
    """Finished jobs (and their refund records) older than JOB_RETENTION_DAYS."""
    from sqlalchemy import delete
    cutoff = (now or _now()) - timedelta(days=get_settings().job_retention_days)
    res = db.execute(delete(PlanJob).where(PlanJob.state.notin_(ACTIVE), PlanJob.created_at < cutoff))
    return res.rowcount or 0


def late_finishes() -> dict:
    """How many plans finished after their job had been refunded or re-queued: today (UTC) and in the jobs still kept."""
    from sqlalchemy import func
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(PlanJob).where(PlanJob.late_finish_at.is_not(None))) or 0
        today = db.scalar(select(func.count()).select_from(PlanJob).where(PlanJob.late_finish_at >= start)) or 0
    return {"today": today, "total": total}


def recent_durations(limit: int) -> list[float]:
    """Seconds taken by the latest real plans, newest first; seeds the wait estimate after a restart."""
    with SessionLocal() as db:
        rows = db.scalars(select(PlanJob.duration_ms).where(PlanJob.state == "done", PlanJob.result_status == "ready",
                                                            PlanJob.duration_ms.is_not(None))
                          .order_by(PlanJob.finished_at.desc()).limit(limit)).all()
    return [r / 1000 for r in rows]
