from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, providers_dep
from app.core import runtime
from app.core.config import get_settings
from app.core.errors import AppError, NotFound, ProviderUnavailable
from app.gateway import capacity
from app.core.middleware import limit
from app.db.session import SessionLocal, get_db
from app.models.trip import TripMember
from app.models.user import User
from app.providers.registry import Providers
from app.schemas.expense import ExpenseCreate
from app.schemas.trip import MemberCreate, MemberUpdate, PreferencesPatch, ReplanRequest, TripCreate, TripUpdate
from app.services import chat_service, expense_service, google_content, live_reflow, plan_jobs, plan_runner, quota_service, share_service
from app.services import trip_service as TS
from app.services.engine_types import StageReporter

log = logging.getLogger("tripai.api.trips")
router = APIRouter(prefix="/trips", tags=["trips"])
_plan_limit = limit("plan", get_settings().rate_limit_plan_per_minute)


class TrainReport(BaseModel):
    item_id: uuid.UUID
    problem: Literal["didnt_run", "wrong_time"]
    note: str | None = Field(default=None, max_length=500)


class ShareCreate(BaseModel):
    show_budget: bool = False  # the owner's budget and "left" stay hidden unless asked
    include_split: bool = False  # "Plan + split": the expense split appears only when chosen here
    expires: Literal["default", "never"] = "default"  # default: 30 days after the trip's last day


class ShareText(BaseModel):
    token: str = Field(min_length=10, max_length=128)  # an existing link of this trip, as the browser kept it


class SharePatch(BaseModel):
    show_budget: bool | None = None
    include_split: bool | None = None
    expires: Literal["default", "never"] | None = None
    extend_days: int | None = Field(default=None, ge=1, le=365)


class PlanRequest(BaseModel):
    changes: PreferencesPatch | None = None
    stream: bool = False


# ------------------------------------------------------------------------------------------------------
# Streaming helper: the planner runs in a worker thread with its own DB session; stage events are
# forwarded as NDJSON lines so the UI can animate real progress.
# ------------------------------------------------------------------------------------------------------

def stream_plan(trip_id: uuid.UUID, user_id: uuid.UUID, providers: Providers, *, change_note: str | None,
                is_replan: bool, read_only: bool = False) -> StreamingResponse:
    events: queue.Queue = queue.Queue()

    def worker():
        db = SessionLocal()
        try:
            trip = TS.load_trip(db, trip_id)
            user = db.get(User, user_id)
            reporter = StageReporter(emit=events.put)
            result = TS.run_plan(db, providers, user, trip, reporter=reporter, change_note=change_note,
                                 is_replan=is_replan)
            db.expire_all()
            trip = TS.load_trip(db, trip_id)
            events.put({"type": "result", "status": result.status, "feasibility": result.feasibility,
                        "trip": TS.trip_out(trip, read_only=read_only)})
        except ProviderUnavailable as exc:  # a required provider is down or out of budget: a normal result, not an error (I3)
            db.rollback()
            unavailable = capacity.from_exception(exc)
            log.warning("plan unavailable (%s): %s", unavailable.reason, exc)
            events.put({"type": "result", **capacity.payload(unavailable, TS.trip_out(TS.load_trip(db, trip_id)))})
        except AppError as exc:
            events.put({"type": "error", "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        except Exception:  # pragma: no cover - defensive
            log.exception("streamed planning failed")
            events.put({"type": "error", "error": {"code": "internal_error",
                                                   "message": "Something went wrong while planning. Please try again."}})
        finally:
            db.close()
            events.put(None)

    threading.Thread(target=worker, daemon=True, name=f"plan-{trip_id}").start()

    def body():
        while True:
            try:
                event = events.get(timeout=15)
            except queue.Empty:
                yield b'{"type":"ping"}\n'
                continue
            if event is None:
                break
            yield (json.dumps(event, default=str, ensure_ascii=False) + "\n").encode()

    return StreamingResponse(body(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _nothing_to_change(trip, message: str, source: str | None, stream: bool):
    """The reply to a request that changes nothing: the plan is untouched and the message says why. A normal result
    (status "no_change"), in the same shape as a plan result, so a client needs no special error handling."""
    result = {"status": "no_change", "message": message, "feasibility": None, "trip": TS.trip_out(trip),
              "interpreted_by": source}
    if not stream:
        return result
    line = (json.dumps({"type": "result", **result}, default=str, ensure_ascii=False) + "\n").encode()
    return StreamingResponse(iter([line]), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})


def _unavailable(trip, u: capacity.Unavailable, stream: bool):
    """The answer when a plan can't be made now: HTTP 200 with a typed state, quota untouched (I3, I4)."""
    body = capacity.payload(u, TS.trip_out(trip))
    if not stream:
        return body
    line = (json.dumps({"type": "result", **body}, default=str, ensure_ascii=False) + "\n").encode()
    return StreamingResponse(iter([line]), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})


def _plan_now(db: Session, providers: Providers, user: User, trip, *, change_note: str | None, is_replan: bool) -> dict:
    try:
        result = TS.run_plan(db, providers, user, trip, change_note=change_note, is_replan=is_replan)
    except ProviderUnavailable as exc:  # the planner has refunded the quota and restored the trip's status
        u = capacity.from_exception(exc)
        log.warning("plan unavailable (%s): %s", u.reason, exc)
        db.expire_all()
        return capacity.payload(u, TS.trip_out(TS.load_trip(db, trip.id)))
    db.expire_all()
    trip = TS.load_trip(db, trip.id)
    return {"status": result.status, "feasibility": result.feasibility, "trip": TS.trip_out(trip)}


SYNC_WAIT_SECONDS = 300  # the non-streaming route waits at most this long for its plan (queue wait included)
FOLLOW_POLL_SECONDS = 1.0


def _line(event: dict) -> bytes:
    return (json.dumps(event, default=str, ensure_ascii=False) + "\n").encode()


STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _follow_ticket(ticket) -> StreamingResponse:
    """Relay a job's events to the client without holding a request thread while it waits (async: the pool is 40 threads, and
    every open stream used to hold one). A ping every 15 s keeps proxies from closing a quiet stream."""
    async def body():
        aq, detach = ticket.subscribe_async(asyncio.get_running_loop())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(aq.get(), 15)
                except asyncio.TimeoutError:
                    yield b'{"type":"ping"}\n'
                    continue
                if event is None:
                    return
                yield _line(event)
        finally:
            detach()  # the client left (or the plan ended): stop pushing to it
    return StreamingResponse(body(), media_type="application/x-ndjson", headers=STREAM_HEADERS)

def _busy_event(job_id: uuid.UUID) -> dict:
    from app.models.plan_job import PlanJob
    u = capacity.Unavailable("provider_busy", datetime.now(UTC) + capacity.MIN_WAIT, capacity.MESSAGE)
    with SessionLocal() as db:
        job = db.get(PlanJob, job_id)
        return {"type": "result", **capacity.payload(u, TS.trip_out(TS.load_trip(db, job.trip_id)))}


def _job_event(job_id: uuid.UUID) -> dict | None:
    """The end of a job as an event, read from the database; None while it is still pending or running."""
    from app.models.plan_job import PlanJob
    with SessionLocal() as db:
        job = db.get(PlanJob, job_id)
        if job is None:
            raise NotFound("That plan wasn't found.")
        if job.state in plan_jobs.ACTIVE:
            return None
        if job.state == "refunded" or job.result_status == "unavailable":
            return _busy_event(job_id)
        if job.state == "done":
            trip = TS.load_trip(db, job.trip_id)
            return {"type": "result", "status": job.result_status, "feasibility": trip.feasibility, "trip": TS.trip_out(trip)}
        return {"type": "error", "error": {"code": job.error_code or "internal_error",
                                           "message": "Something went wrong while planning. Please try again."}}


def _db_result(job_id: uuid.UUID, timeout: float) -> dict | None:
    """Wait up to `timeout` seconds for a job this process isn't running (another worker holds it, or it ended a while ago)."""
    import time
    end = time.monotonic() + timeout
    while True:
        event = _job_event(job_id)
        if event is not None or time.monotonic() >= end:
            return event
        time.sleep(FOLLOW_POLL_SECONDS)


def _respond_ticket(ticket, user: User, want_stream: bool):
    if want_stream:
        return _follow_ticket(ticket)
    outcome = ticket.wait(SYNC_WAIT_SECONDS)
    if outcome is None:  # still waiting after five minutes: an answer, not a hang
        return {k: v for k, v in _busy_event(ticket.job_id).items() if k != "type"}
    if outcome.error is not None:
        raise outcome.error
    return {k: v for k, v in (outcome.event or {}).items() if k != "type"}


def _respond_job(job, user: User, want_stream: bool):
    """A request whose idempotency key already has a job: the same plan, not a second one. It follows the live job when this
    process runs it, and reads the outcome from the database otherwise."""
    ticket = plan_runner.runner.ticket_for(job.id)
    if ticket is not None:
        return _respond_ticket(ticket, user, want_stream)
    if not want_stream:
        ev = _db_result(job.id, SYNC_WAIT_SECONDS) or _busy_event(job.id)
        if ev["type"] == "error":
            raise AppError(ev["error"]["message"], code=ev["error"]["code"], status_code=500)
        return {k: v for k, v in ev.items() if k != "type"}

    async def body():
        waited = 0
        while True:
            ev = await asyncio.to_thread(_job_event, job.id)  # a short database read; no thread is held between polls
            if ev is not None:
                yield _line(ev)
                return
            if waited >= SYNC_WAIT_SECONDS:
                yield _line(await asyncio.to_thread(_busy_event, job.id))
                return
            if waited % 15 == 0:
                yield b'{"type":"ping"}\n'
            await asyncio.sleep(FOLLOW_POLL_SECONDS)
            waited += FOLLOW_POLL_SECONDS
    return StreamingResponse(body(), media_type="application/x-ndjson", headers=STREAM_HEADERS)

def _enqueue(db: Session, p: Providers, user: User, trip, *, kind: str, key: str, note: str | None, want_stream: bool):
    """The job goes in the same commit as the quota change that is already pending in `db` (the outbox), then to the runner."""
    job, created = plan_jobs.create(db, user, trip, kind=kind, key=key, change_note=note)
    if not created:  # lost a race with an identical request: undo this one`s quota change (not committed yet) and follow the winner
        db.rollback()
        if kind == "new":
            capacity.uncount_plan(p)
        return _respond_job(job, user, want_stream)
    trip.status = "planning"
    db.commit()
    try:
        ticket = plan_runner.runner.submit(job.id, job.lane, plan_runner.make_work(job.id, p, read_only=False))
    except Exception:  # noqa: BLE001 - the job exists but never started: hand the quota back at once
        log.exception("could not queue plan job %s", job.id)
        plan_jobs.refund_active(job.id, "enqueue_failed")
        raise
    return _respond_ticket(ticket, user, want_stream)


def _guard_concurrent(trip) -> None:
    if trip.status == "planning":
        updated = trip.updated_at if trip.updated_at.tzinfo else trip.updated_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - updated < timedelta(minutes=2):
            raise AppError("This trip is already being planned. Please wait a moment.", code="plan_in_progress",
                           status_code=409)


# ------------------------------------------------------------------------------------------------------
# Trips
# ------------------------------------------------------------------------------------------------------

@router.get("")
def list_trips(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"trips": [TS.trip_card(t) for t in TS.list_trips(db, user)]}


@router.post("", status_code=201)
def create_trip(body: TripCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.create_trip(db, user, body)
    db.commit()
    return TS.trip_out(TS.load_trip(db, trip.id))


@router.get("/{trip_id}")
def get_trip(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db),
             providers: Providers = Depends(providers_dep)):
    trip = TS.load_trip(db, trip_id, user)
    if google_content.refresh_trip(db, trip, providers)["refreshed"]:  # Google content older than 30 days (I6)
        trip = TS.load_trip(db, trip_id, user)
    return TS.trip_out(trip)


@router.patch("/{trip_id}")
def update_trip(trip_id: uuid.UUID, body: TripUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    if body.title:
        trip.title = body.title
    if body.status:
        if body.status in ("ready", "active", "completed") and not trip.plan_count:
            raise AppError("Plan this trip first.", code="not_planned", status_code=409)
        trip.status = body.status
    db.commit()
    return TS.trip_out(TS.load_trip(db, trip_id, user))


@router.delete("/{trip_id}", status_code=204)
def delete_trip(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    db.delete(trip)
    db.commit()


@router.patch("/{trip_id}/preferences")
def update_preferences(trip_id: uuid.UUID, body: PreferencesPatch, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    TS.apply_patch(trip, body)
    db.commit()
    return TS.trip_out(TS.load_trip(db, trip_id, user))


@router.post("/{trip_id}/plan", dependencies=[Depends(_plan_limit)])
def plan_trip(trip_id: uuid.UUID, body: PlanRequest | None = None, stream: bool = Query(False),
              idempotency_key: str | None = Header(default=None), user: User = Depends(current_user),
              db: Session = Depends(get_db), p: Providers = Depends(providers_dep)):
    """Plan (or re-plan with new settings). The first successful plan of a trip consumes one plan from the quota.
    `Idempotency-Key` (one per tap of the Plan button): the same key is the same plan, never a second one or a second charge."""
    body = body or PlanRequest()
    trip = TS.load_trip(db, trip_id, user)
    jobs = get_settings().plan_jobs_enabled
    key, existing = plan_jobs.resolve_key(db, user, trip, "replan" if trip.plan_count else "new", idempotency_key,
                                          body.model_dump_json())
    if jobs and existing is not None:
        return _respond_job(existing, user, stream or body.stream)
    _guard_concurrent(trip)
    is_replan = bool(trip.plan_count)
    if is_replan:
        TS.check_replan_allowed(trip)
    note = None
    if body.changes:
        notes = TS.apply_patch(trip, body.changes)
        note = ("Adjusted " + ", ".join(notes)) if notes else None
    if not is_replan:
        blocked = capacity.check_paused() or capacity.check_new_plan(p)  # before the quota is touched: paused by an admin, today's cap, or a required provider is closed
        if blocked is None and jobs:
            blocked = plan_runner.runner.admit(plan_jobs.lane_for_user(db, user, False))  # too many waiting: a polite refusal
        if blocked is not None:
            db.rollback()
            return _unavailable(trip, blocked, stream or body.stream)
        quota_service.reserve(db, user, trip)
        capacity.count_plan(p)
    else:
        blocked = capacity.check_paused() or (plan_runner.runner.admit(plan_jobs.LANE_REPLAN) if jobs else None)
        if blocked is not None:
            db.rollback()
            return _unavailable(trip, blocked, stream or body.stream)
    if jobs:
        return _enqueue(db, p, user, trip, kind="replan" if is_replan else "new", key=key, note=note,
                        want_stream=stream or body.stream)
    db.commit()
    if stream or body.stream:
        return stream_plan(trip.id, user.id, p, change_note=note, is_replan=is_replan)
    return _plan_now(db, p, user, trip, change_note=note, is_replan=is_replan)


@router.post("/{trip_id}/replan", dependencies=[Depends(_plan_limit)])
def replan_trip(trip_id: uuid.UUID, body: ReplanRequest, stream: bool = Query(False),
                idempotency_key: str | None = Header(default=None), user: User = Depends(current_user),
                db: Session = Depends(get_db), p: Providers = Depends(providers_dep)):
    """Change an existing plan: swap an item for an alternative, apply structured changes, or describe the change
    in plain language ("make it cheaper", "skip Hawa Mahal"). Replans never consume the free-plan quota."""
    trip = TS.load_trip(db, trip_id, user)
    jobs = get_settings().plan_jobs_enabled
    key, existing = plan_jobs.resolve_key(db, user, trip, "replan", idempotency_key, body.model_dump_json())
    if jobs and existing is not None:
        return _respond_job(existing, user, stream or body.stream)
    _guard_concurrent(trip)
    TS.check_replan_allowed(trip)
    note_parts: list[str] = []
    source = None
    if body.swap:
        patch, note = TS.swap_patch(trip, body.swap.item_id, body.swap.alternative_id)
        if patch:
            TS.apply_patch(trip, patch)
        note_parts.append(note)
    if body.changes:
        notes = TS.apply_patch(trip, body.changes)
        if notes:
            note_parts.append("Adjusted " + ", ".join(notes))
    if body.message:
        outcome = TS.intent_patch(db, p, user, trip, body.message)
        source = outcome.source
        if outcome.changed:
            TS.apply_intent(trip, outcome)
        elif not (body.swap or body.changes):
            # Nothing to change is an answer, not an error: say why, keep the plan as it is, and keep what was typed
            db.commit()
            return _nothing_to_change(trip, outcome.nothing_message(), source, stream or body.stream)
        if outcome.note():
            note_parts.append(outcome.note())
    db.commit()
    note = "; ".join(note_parts) or None
    blocked = capacity.check_paused()  # an admin has paused planning: the change is saved, the re-plan waits
    if blocked is None and jobs:
        blocked = plan_runner.runner.admit(plan_jobs.LANE_REPLAN)
    if blocked is not None:
        db.rollback()
        return _unavailable(trip, blocked, stream or body.stream)
    if jobs:
        out = _enqueue(db, p, user, trip, kind="replan", key=key, note=note, want_stream=stream or body.stream)
        if isinstance(out, dict):
            out["interpreted_by"] = source
        return out
    if stream or body.stream:
        return stream_plan(trip.id, user.id, p, change_note=note, is_replan=True)
    out = _plan_now(db, p, user, trip, change_note=note, is_replan=True)
    out["interpreted_by"] = source
    return out


# ------------------------------------------------------------------------------------------------------
# Live trip mode (ADR 0021): "running late" moves the rest of one day; offline, so it uses no re-plan
# ------------------------------------------------------------------------------------------------------

class LateRequest(BaseModel):
    minutes: int = Field(ge=5, le=360)
    day_index: int = Field(ge=0, le=400)
    from_item_id: uuid.UUID | None = None  # the first stop to move; default: the first one that hasn't started
    now: datetime | None = None  # the traveller's wall clock at the destination, for "hasn't started yet"


_live_limit = limit("live", 20)


@router.post("/{trip_id}/live/late", dependencies=[Depends(_live_limit)])
def live_late(trip_id: uuid.UUID, body: LateRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    now = body.now.replace(tzinfo=None) if body.now else None
    result = live_reflow.apply_late(db, trip, body.day_index, body.minutes, body.from_item_id, now=now)
    db.commit()
    return {"result": result.as_dict(), "trip": TS.trip_out(TS.load_trip(db, trip_id, user))}


@router.post("/{trip_id}/live/undo", dependencies=[Depends(_live_limit)])
def live_undo(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    if not live_reflow.undo(db, trip):
        raise AppError("There's no running-late change to undo.", code="nothing_to_undo", status_code=409)
    db.commit()
    return {"trip": TS.trip_out(TS.load_trip(db, trip_id, user))}


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    item_id: uuid.UUID | None = None  # the stop "Ask" was tapped on


_chat_limit = limit("chat", 20)


@router.post("/{trip_id}/chat", dependencies=[Depends(_chat_limit)])
def chat(trip_id: uuid.UUID, body: ChatRequest, user: User = Depends(current_user), db: Session = Depends(get_db),
         providers: Providers = Depends(providers_dep)):
    """The trip chat (ADR 0016): answers a question about this trip, or says "change" so the client re-plans with the
    same message through /replan (the one path that can change a plan)."""
    trip = TS.load_trip(db, trip_id, user)
    out = chat_service.respond(db, providers, user, trip, body.message, [h.model_dump() for h in body.history],
                               str(body.item_id) if body.item_id else None)
    db.commit()
    return out


@router.get("/{trip_id}/alternatives")
def alternatives(trip_id: uuid.UUID, item_id: uuid.UUID | None = None, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    it = trip.current_itinerary
    if it is None:
        raise NotFound("This trip has no itinerary yet.")
    items = [i for i in it.items if item_id is None or i.id == item_id]
    if item_id and not items:
        raise NotFound("Itinerary item not found.")
    outs = [(i, TS.item_out(i, trip.destination_name)) for i in items if i.alternatives or item_id]  # through the I1/I6 gates
    return {"items": [{"item_id": o["id"], "title": o["title"], "kind": o["kind"], "alternatives": o["alternatives"]}
                      for _, o in outs]}


@router.get("/{trip_id}/budget")
def budget(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    it = trip.current_itinerary
    return {"budget": float(trip.budget), "breakdown": it.budget_breakdown if it else None,
            "expenses": expense_service.summary(trip)}


# ------------------------------------------------------------------------------------------------------
# Group members & expenses
# ------------------------------------------------------------------------------------------------------

@router.post("/{trip_id}/members", status_code=201)
def add_member(trip_id: uuid.UUID, body: MemberCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    if len(trip.members) >= 20:
        raise AppError("A trip can have at most 20 members.", code="too_many_members", status_code=422)
    if any(m.name.lower() == body.name.lower() for m in trip.members):
        raise AppError("Someone with that name is already in the group.", code="duplicate_member", status_code=409)
    m = TripMember(trip_id=trip.id, name=body.name, email=body.email, budget=body.budget, food_pref=body.food_pref)
    db.add(m)
    db.commit()
    return TS.member_out(m)


@router.patch("/{trip_id}/members/{member_id}")
def update_member(trip_id: uuid.UUID, member_id: uuid.UUID, body: MemberUpdate, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    m = next((m for m in trip.members if m.id == member_id), None)
    if m is None:
        raise NotFound("Member not found.")
    for field in ("name", "budget", "food_pref"):
        value = getattr(body, field)
        if value is not None:
            setattr(m, field, value)
    db.commit()
    return TS.member_out(m)


@router.delete("/{trip_id}/members/{member_id}", status_code=204)
def remove_member(trip_id: uuid.UUID, member_id: uuid.UUID, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    m = next((m for m in trip.members if m.id == member_id), None)
    if m is None:
        raise NotFound("Member not found.")
    if m.is_owner:
        raise AppError("The trip owner can't be removed.", code="owner_member", status_code=422)
    if any(e.paid_by_member_id == m.id or any(s.member_id == m.id for s in e.splits) for e in trip.expenses):
        raise AppError("This person is part of recorded expenses. Delete those expenses first.",
                       code="member_has_expenses", status_code=409)
    db.delete(m)
    db.commit()


@router.get("/{trip_id}/expenses")
def list_expenses(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    return {"expenses": [TS.expense_out(e) for e in trip.expenses], "summary": expense_service.summary(trip)}


@router.post("/{trip_id}/expenses", status_code=201)
def add_expense(trip_id: uuid.UUID, body: ExpenseCreate, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    if len(trip.expenses) >= 500:
        raise AppError("Expense limit reached for this trip.", code="too_many_expenses", status_code=422)
    exp = expense_service.add_expense(db, trip, user.id, body)
    db.commit()
    trip = TS.load_trip(db, trip_id, user)
    return {"expense": TS.expense_out(exp), "summary": expense_service.summary(trip)}


@router.delete("/{trip_id}/expenses/{expense_id}", status_code=204)
def delete_expense(trip_id: uuid.UUID, expense_id: uuid.UUID, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    exp = next((e for e in trip.expenses if e.id == expense_id), None)
    if exp is None:
        raise NotFound("Expense not found.")
    db.delete(exp)
    db.commit()


@router.get("/{trip_id}/settlement")
def settlement(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return expense_service.summary(TS.load_trip(db, trip_id, user))


def _sharing_on() -> None:
    if not runtime.share_links_enabled():  # the setting, or the admin console's "share links" switch
        raise NotFound("Sharing is switched off.")


def _shareable(db: Session, providers: Providers, user: User, trip_id: uuid.UUID):
    """The owner's trip with a plan, its Google content refreshed first when it is more than 30 days old (I6)."""
    _sharing_on()
    trip = TS.load_trip(db, trip_id, owner=user)
    if trip.current_itinerary is None:
        raise AppError("Plan this trip before sharing it.", code="not_planned", status_code=409)
    if google_content.refresh_trip(db, trip, providers)["refreshed"]:
        trip = TS.load_trip(db, trip_id, owner=user)
    return trip


def _text_bundle(trip, link, token: str, user: User) -> dict:
    """Made now, from refreshed data, and not stored anywhere (ADR 0006). `wa_me_url` opens WhatsApp on the person's OWN saved mobile
    number (null if they have none), for "send this plan to my WhatsApp"; it is never built from anyone else's number."""
    url = share_service.public_url(token)
    view = share_service.share_view(trip, link)
    text = share_service.share_text(view, url)  # the full day-by-day text, for copying
    card = share_service.card_text(view, url)  # what WhatsApp gets: a short line and the link, which it shows as a card
    return {"url": url, "text": text, "wa_url": share_service.wa_link(card),
            "wa_me_url": share_service.wa_link(card, user.phone) if user.phone else None}


@router.post("/{trip_id}/share", status_code=201, dependencies=[Depends(limit("share-create", 20))])
def create_share(trip_id: uuid.UUID, body: ShareCreate, user: User = Depends(current_user), db: Session = Depends(get_db),
                 providers: Providers = Depends(providers_dep)):
    """A new share link. The full link is returned once; only its hash is kept. Also returns the WhatsApp text."""
    trip = _shareable(db, providers, user, trip_id)
    link, token = share_service.create_link(db, trip, user, show_budget=body.show_budget,
                                            include_split=body.include_split, never_expires=body.expires == "never")
    return {"share": share_service.link_out(link), **_text_bundle(trip, link, token, user)}


@router.post("/{trip_id}/share-text", dependencies=[Depends(limit("share-create", 20))])
def share_text(trip_id: uuid.UUID, body: ShareText, user: User = Depends(current_user), db: Session = Depends(get_db),
               providers: Providers = Depends(providers_dep)):
    """The WhatsApp text again for a link the browser already holds, generated now from refreshed data."""
    trip = _shareable(db, providers, user, trip_id)
    link = share_service.find_active(db, body.token)
    if link is None or link.trip_id != trip.id:
        raise NotFound("That share link isn't active. Create a new one.")
    return _text_bundle(trip, link, body.token, user)


@router.post("/{trip_id}/email", dependencies=[Depends(limit("share-create", 20))])
def email_plan(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """"Email me this plan": the current plan to the account's own verified address, now. (A new plan is also emailed automatically.)"""
    from app.core import killswitch
    from app.services import mailer, plan_email
    if killswitch.killed("plan_emails") or not mailer.configured():
        raise AppError("Email isn't available right now.", code="email_unavailable", status_code=503)
    trip = TS.load_trip(db, trip_id, owner=user)
    try:
        sent_to = plan_email.send_for(db, user, trip)
    except plan_email.NotReady as exc:
        raise AppError(str(exc), code="not_ready", status_code=409) from exc
    except mailer.MailError as exc:
        log.warning("plan email to the owner of trip %s failed: %s", trip_id, exc)
        raise AppError("We couldn't send the email just now. Please try again in a moment.", code="delivery_failed", status_code=502) from exc
    return {"sent_to": sent_to}


@router.get("/{trip_id}/shares")
def list_shares(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _sharing_on()
    trip = TS.load_trip(db, trip_id, owner=user)
    return {"shares": [share_service.link_out(s) for s in share_service.active_links(db, trip)]}


def _own_link(db: Session, user: User, trip_id: uuid.UUID, share_id: uuid.UUID):
    trip = TS.load_trip(db, trip_id, owner=user)
    link = db.get(share_service.ShareLink, share_id)
    if link is None or link.trip_id != trip.id or link.revoked_at is not None:
        raise NotFound("Share link not found.")
    return trip, link


@router.patch("/{trip_id}/shares/{share_id}")
def update_share(trip_id: uuid.UUID, share_id: uuid.UUID, body: SharePatch, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Extend a link, remove its expiry, put the default expiry back, or change what it shows."""
    _sharing_on()
    trip, link = _own_link(db, user, trip_id, share_id)
    if body.show_budget is not None:
        link.show_budget = body.show_budget
    if body.include_split is not None:
        link.include_split = body.include_split
    if body.expires == "never":
        link.expires_at = None
    elif body.expires == "default":
        link.expires_at = share_service.default_expiry(trip)
    if body.extend_days:
        share_service.extend(link, body.extend_days)
    db.commit()
    return share_service.link_out(link)


@router.delete("/{trip_id}/shares/{share_id}", status_code=204)
def revoke_share(trip_id: uuid.UUID, share_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, link = _own_link(db, user, trip_id, share_id)
    link.revoked_at = datetime.now(UTC)
    db.commit()


@router.post("/{trip_id}/report-train")
def report_train(trip_id: uuid.UUID, body: TrainReport, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """"This train didn't run" / "it ran at another time": stored, and its route is refreshed first (ADR 0003)."""
    from app.services.rail_refresh import report_train as report
    trip = TS.load_trip(db, trip_id, owner=user)
    return report(db, user, trip, body.item_id, body.problem, body.note)
