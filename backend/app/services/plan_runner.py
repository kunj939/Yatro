"""Runs plans (ADR 0008): a bounded in-process runner with priority lanes, admission control, and a wait estimate.

Why this exists. Before, every request started its own thread, with no limit. Measured on one process (docs/perf/load-*.md): first
progress event p95 is 0.2 s with 1 plan at once, 2.0 s with 25, 3.6 s with 50 (SLO: 2 s). So `MAX_CONCURRENT_PLANS` caps how
many plans run at once; the rest wait in order, and are told so ("High demand — you're #N", with an estimate from real plan
durations). It sits behind one small interface (`submit`), so a separate worker fleet (arq on Redis) could replace it later
without touching the routes; nothing here needs Redis.

Order: replans, then paid plans, then new free plans, each in arrival order. A plan that has waited LANE_MAX_WAIT_SECONDS is
treated as first-lane, so nobody starves. Everything is per process: with several workers each has its own queue and limit, so
"#N" is per worker (the UI says "about").
"""
from __future__ import annotations

import asyncio
import logging
import math
import queue
import statistics
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core import brownout
from app.core.config import get_settings
from app.core.errors import AppError, ProviderUnavailable
from app.db.session import SessionLocal
from app.gateway import capacity
from app.models.trip import Trip
from app.models.user import User
from app.services import plan_jobs as PJ
from app.services import trip_service as TS
from app.services.engine_types import StageReporter

log = logging.getLogger("tripai.plan_runner")
HIGH_DEMAND_MESSAGE = "We're very busy right now. None of your free plans were used, and your trip details are saved."
ABSOLUTE_FACTOR = 3  # past this many times the hard limit even replans and paid plans wait outside


@dataclass
class Outcome:
    event: dict | None = None  # the last event of the stream: a result, or an error
    error: BaseException | None = None  # the exception behind an error event (the non-streaming route re-raises it)
    counted: bool = False  # a real, finished plan: its duration feeds the wait estimate


class Ticket:
    """What the request holds: a replayable stream of the job's events, and the outcome when it ends."""

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._subs: list[Callable[[dict | None], None]] = []
        self._done = threading.Event()
        self.outcome: Outcome | None = None
        self.finished_at: float | None = None

    def emit(self, event: dict) -> None:
        with self._lock:
            if event.get("type") == "queue":  # only the latest position matters to someone who joins late
                self._events = [e for e in self._events if e.get("type") != "queue"]
            self._events.append(event)
            for push in self._subs:
                push(event)

    def _attach(self, push: Callable[[dict | None], None]) -> None:
        """Replay what happened so far, then every later event, then None at the end."""
        with self._lock:
            for e in self._events:
                push(e)
            if self._done.is_set():
                push(None)
            else:
                self._subs.append(push)

    def subscribe(self) -> queue.Queue:
        """A thread-safe queue of every event so far and every later one, ending with None. Any number of requests can
        follow one job."""
        q: queue.Queue = queue.Queue()
        self._attach(q.put)
        return q

    def subscribe_async(self, loop) -> tuple[asyncio.Queue, Callable[[], None]]:
        """The same for an event loop, with a function to call when the client goes away. A waiting stream then holds no
        thread at all (the request pool is only 40 threads, and every open stream used to hold one: first event p95 was 21 s
        at 100 plans at once, ADR 0008)."""
        aq: asyncio.Queue = asyncio.Queue()

        def push(ev):
            try:
                loop.call_soon_threadsafe(aq.put_nowait, ev)
            except RuntimeError:  # the loop is closed: that client is gone, and the plan must not notice
                pass

        def detach():
            with self._lock:
                if push in self._subs:
                    self._subs.remove(push)
        self._attach(push)
        return aq, detach

    def finish(self, outcome: Outcome) -> None:
        with self._lock:
            self.outcome = outcome
            self.finished_at = time.monotonic()
            self._done.set()
            for push in self._subs:
                push(None)
            self._subs.clear()

    def wait(self, timeout: float | None = None) -> Outcome | None:
        return self.outcome if self._done.wait(timeout) else None


@dataclass(eq=False)
class _Entry:
    job_id: uuid.UUID
    lane: int
    seq: int
    enqueued: float
    work: Callable[[Ticket], float | None]
    ticket: Ticket
    last_pos: int = 0
    started: float = 0.0


def _round5(x: float) -> int:
    return max(5, int(5 * math.ceil(x / 5)))


class InProcessRunner:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._waiting: list[_Entry] = []
        self._running: dict[uuid.UUID, _Entry] = {}
        self._tickets: dict[uuid.UUID, Ticket] = {}
        self._seq = 0
        self._durations: deque[float] = deque(maxlen=get_settings().eta_samples)
        self._waits: deque[float] = deque(maxlen=200)
        self._started = False
        self._hb_stop = threading.Event()
        self.accepting = True

    # --- configuration read at use time, so a changed setting takes effect without rebuilding the runner -----------------
    @staticmethod
    def _s():
        return get_settings()

    def reset(self) -> None:
        """For tests: forget everything (running plans keep running; they are just no longer counted)."""
        with self._cv:
            self._waiting.clear()
            self._running.clear()
            self._tickets.clear()
            self._durations.clear()
            self._waits.clear()
            self._seq = 0
            self.accepting = True
            self._started = False

    # --- admission ---------------------------------------------------------------------------------------------------------
    def admit(self, lane: int) -> capacity.Unavailable | None:
        """Called before the quota is touched: is there room for this plan? Free new plans stop at the hard limit; replans and
        paid plans still enter, up to ABSOLUTE_FACTOR times it."""
        s = self._s()
        with self._cv:
            waiting = len(self._waiting)
            if not self.accepting:
                return capacity.Unavailable("provider_busy", datetime.now(UTC) + capacity.MIN_WAIT, capacity.MESSAGE)
            hard = max(1, s.plan_queue_hard_limit)
            if (lane >= PJ.LANE_FREE and waiting >= hard) or waiting >= hard * ABSOLUTE_FACTOR:
                eta = self._eta_locked(waiting + 1)
                wait = timedelta(seconds=max(capacity.MIN_WAIT.total_seconds(), (eta[0] or 0) if eta else 0))
                return capacity.Unavailable("high_demand", datetime.now(UTC) + wait, HIGH_DEMAND_MESSAGE)
        return None

    # --- submitting and running ---------------------------------------------------------------------------------------------
    def ticket_for(self, job_id: uuid.UUID) -> Ticket | None:
        with self._cv:
            return self._tickets.get(job_id)

    def submit(self, job_id: uuid.UUID, lane: int, work: Callable[[Ticket], float | None]) -> Ticket:
        with self._cv:
            self._ensure_started_locked()
            now = time.monotonic()
            for jid in [j for j, t in self._tickets.items() if t.finished_at and now - t.finished_at > 120]:
                del self._tickets[jid]
            ticket = Ticket(job_id)
            self._tickets[job_id] = ticket
            self._seq += 1
            self._waiting.append(_Entry(job_id, lane, self._seq, now, work, ticket))
            self._pump_locked()
            return ticket

    def _key(self, e: _Entry, now: float) -> tuple[int, int]:
        aged = now - e.enqueued >= self._s().lane_max_wait_seconds
        return (PJ.LANE_REPLAN if aged else e.lane, e.seq)

    def _pump_locked(self) -> None:
        s = self._s()
        while self._waiting and len(self._running) < max(1, s.max_concurrent_plans):
            now = time.monotonic()
            entry = min(self._waiting, key=lambda e: self._key(e, now))
            self._waiting.remove(entry)
            entry.started = now
            self._waits.append(now - entry.enqueued)
            self._running[entry.job_id] = entry
            threading.Thread(target=self._run, args=(entry,), daemon=True, name=f"plan-{entry.job_id}").start()
        self._announce_positions_locked()

    def _run(self, entry: _Entry) -> None:
        counted: float | None = None
        try:
            counted = entry.work(entry.ticket)
        except Exception as exc:  # noqa: BLE001 - never leave a request waiting on a plan that died
            log.exception("plan job %s crashed in the runner", entry.job_id)
            if entry.ticket.outcome is None:
                entry.ticket.emit({"type": "error", "error": {"code": "internal_error",
                                                              "message": "Something went wrong while planning. Please try again."}})
                entry.ticket.finish(Outcome(error=exc))
        finally:
            with self._cv:
                self._running.pop(entry.job_id, None)
                if counted:
                    self._durations.append(counted)
                self._pump_locked()
                self._cv.notify_all()

    def _announce_positions_locked(self) -> None:
        now = time.monotonic()
        for i, e in enumerate(sorted(self._waiting, key=lambda e: self._key(e, now)), start=1):
            if e.last_pos != i:
                e.last_pos = i
                eta = self._eta_locked(i)
                ev = {"type": "queue", "position": i, "waiting": len(self._waiting)}
                if eta:
                    ev.update(eta_seconds=eta[0], eta_range=[eta[1], eta[2]])
                else:
                    ev.update(eta_seconds=None, eta_range=None)  # no finished plans yet to estimate from: say nothing
                e.ticket.emit(ev)

    def _eta_locked(self, position: int) -> tuple[int, int, int] | None:
        """(estimate, low, high) seconds until a plan at this queue position starts, from recent real plan durations: the
        runner finishes about MAX_CONCURRENT_PLANS plans per median-duration. None when no plan has finished yet."""
        if not self._durations:
            return None
        s = self._s()
        per = max(1, s.max_concurrent_plans)
        d = sorted(self._durations)
        med = statistics.median(d)
        p90 = d[min(len(d) - 1, int(0.9 * (len(d) - 1)))]
        mid = _round5(position * med / per)
        return mid, min(mid, _round5(position * med * 0.7 / per)), max(mid, _round5(position * p90 * 1.3 / per))

    def seed(self, seconds: list[float]) -> None:
        with self._cv:
            for x in reversed(seconds):  # newest last
                self._durations.append(x)

    # --- background upkeep ---------------------------------------------------------------------------------------------------
    def _ensure_started_locked(self) -> None:
        if self._started:
            return
        self._started = True
        self._hb_stop = threading.Event()
        threading.Thread(target=self._heartbeat_loop, args=(self._hb_stop,), daemon=True, name="plan-heartbeat").start()
        try:
            self.seed(PJ.recent_durations(self._s().eta_samples))
        except Exception:  # noqa: BLE001 - an estimate is optional
            log.debug("could not seed plan durations", exc_info=True)

    def _owned(self) -> list[uuid.UUID]:
        with self._cv:
            return [*self._running, *(e.job_id for e in self._waiting)]

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        while not stop.wait(max(1, self._s().job_heartbeat_seconds)):
            try:
                PJ.heartbeat(self._owned())
            except Exception:  # noqa: BLE001 - a missed beat is retried; a long silence is what the reaper acts on
                log.warning("heartbeat write failed", exc_info=True)

    def start(self) -> None:
        """The application is up: take plans (after a shutdown in the same process, as the tests do)."""
        with self._cv:
            self.accepting = True
            if not self._hb_stop.is_set():
                return
            self._started = False

    def shutdown(self) -> None:
        """Stop taking plans, and hand every job this process holds to the reaper straight away."""
        with self._cv:
            self.accepting = False
        self._hb_stop.set()
        try:
            PJ.release(self._owned())
        except Exception:  # noqa: BLE001
            log.warning("could not release plan jobs on shutdown", exc_info=True)

    def adopt(self, job_id: uuid.UUID) -> None:
        """Run a job the reaper put back in the queue (its first process died). Nobody is streaming it."""
        from app.providers.registry import get_providers
        with SessionLocal() as db:
            job = db.get(PJ.PlanJob, job_id)
            lane = job.lane if job else PJ.LANE_FREE
        self.submit(job_id, lane, make_work(job_id, get_providers(), read_only=False))
        log.warning("plan job %s was interrupted and has been queued to run again", job_id)

    def stats(self) -> dict:
        s = self._s()
        with self._cv:
            waits = sorted(self._waits)
            p95 = waits[min(len(waits) - 1, int(0.95 * (len(waits) - 1)))] if waits else None
            lanes = {"replan": 0, "paid": 0, "free": 0}
            for e in self._waiting:
                lanes[("replan", "paid", "free")[e.lane]] += 1
            d = sorted(self._durations)
            return {"running": len(self._running), "waiting": len(self._waiting), "waiting_by_lane": lanes,
                    "max_concurrent": s.max_concurrent_plans, "queue_limit": s.plan_queue_limit,
                    "hard_limit": s.plan_queue_hard_limit, "accepting": self.accepting,
                    "median_plan_seconds": round(statistics.median(d), 1) if d else None,
                    "wait_p95_seconds": round(p95, 1) if p95 is not None else None,
                    "samples": len(d), "late_finishes": _late(), "brownout_level": brownout.configured(), "brownout_auto": s.brownout_auto,
                    "brownout_suggested": brownout.suggest(p95, len(self._waiting), s.plan_queue_limit, s.plan_queue_hard_limit)}


def _late() -> dict | None:
    try:
        return PJ.late_finishes()
    except Exception:  # noqa: BLE001 - an admin number never breaks the view
        return None


runner = InProcessRunner()


# --- one job, start to finish -------------------------------------------------------------------------------------------------

def execute_job(job_id: uuid.UUID, providers, read_only: bool, emit: Callable[[dict], None]) -> Outcome:
    """Claim the job, plan, and settle it. Never raises. Quota refunds happen inside the planner (once, keyed by the job);
    this only makes sure the job row ends in the right state."""
    job = PJ.claim(job_id)
    if job is None:
        u = capacity.Unavailable("provider_busy", datetime.now(UTC) + capacity.MIN_WAIT, capacity.MESSAGE)
        with SessionLocal() as db:
            j = db.get(PJ.PlanJob, job_id)
            trip_out = TS.trip_out(TS.load_trip(db, j.trip_id)) if j is not None else {}
        event = {"type": "result", **capacity.payload(u, trip_out)}
        emit(event)
        return Outcome(event)
    t0 = time.perf_counter()
    token = PJ.CURRENT_JOB.set(job_id)
    db = SessionLocal()
    try:
        if get_settings().plan_test_delay_seconds:  # VERIFY.md: gives a person time to kill the process mid-plan
            time.sleep(get_settings().plan_test_delay_seconds)
        trip = TS.load_trip(db, job.trip_id)
        user = db.get(User, job.user_id)
        with brownout.plan_scope():
            result = TS.run_plan(db, providers, user, trip, reporter=StageReporter(emit=emit), change_note=job.change_note,
                                 is_replan=job.kind == "replan")
        db.expire_all()
        trip = TS.load_trip(db, job.trip_id)
        event = {"type": "result", "status": result.status, "feasibility": result.feasibility,
                 "trip": TS.trip_out(trip, read_only=read_only)}
        ms = int((time.perf_counter() - t0) * 1000)
        PJ.complete(job_id, "done", result_status=result.status, duration_ms=ms)
        emit(event)
        return Outcome(event, counted=result.status == "ready")
    except ProviderUnavailable as exc:  # a required provider is down or out of budget: a normal result, not an error (I3)
        db.rollback()
        u = capacity.from_exception(exc)
        log.warning("plan unavailable (%s): %s", u.reason, exc)
        PJ.complete(job_id, "failed", result_status="unavailable", error_code=u.reason)
        event = {"type": "result", **capacity.payload(u, TS.trip_out(TS.load_trip(db, job.trip_id)))}
        emit(event)
        return Outcome(event)
    except AppError as exc:
        db.rollback()
        _settle_failed(job_id, exc.code)
        event = {"type": "error", "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        emit(event)
        return Outcome(event, error=exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("planning failed")
        db.rollback()
        _settle_failed(job_id, "internal_error")
        event = {"type": "error", "error": {"code": "internal_error",
                                            "message": "Something went wrong while planning. Please try again."}}
        emit(event)
        return Outcome(event, error=exc)
    finally:
        db.close()
        PJ.CURRENT_JOB.reset(token)


def _settle_failed(job_id: uuid.UUID, code: str) -> None:
    """The planner refunds on its own failure paths; this covers a failure outside it, and records the end state."""
    PJ.complete(job_id, "failed", result_status="error", error_code=code)
    from app.services import quota_service
    with SessionLocal() as db:
        job = db.get(PJ.PlanJob, job_id)
        trip = db.get(Trip, job.trip_id) if job else None
        user = db.get(User, job.user_id) if job else None
        if trip is not None and user is not None:
            token = PJ.CURRENT_JOB.set(job_id)
            try:
                quota_service.refund(db, user, trip, reason="failed")
                if trip.status == "planning":
                    PJ.restore_trip(trip)
            finally:
                PJ.CURRENT_JOB.reset(token)
            db.commit()


def make_work(job_id: uuid.UUID, providers, *, read_only: bool) -> Callable[[Ticket], float | None]:
    def work(ticket: Ticket) -> float | None:
        t0 = time.perf_counter()
        outcome = execute_job(job_id, providers, read_only, ticket.emit)
        ticket.finish(outcome)
        return time.perf_counter() - t0 if outcome.counted else None
    return work


def start_background_threads() -> None:
    """The reaper (and, once, a pass at startup). Not started in tests, which call `PJ.reap()` themselves."""
    s = get_settings()
    if not s.reaper_enabled:
        return

    def loop():
        stop = threading.Event()
        while True:
            try:
                out = PJ.reap()
                for jid in out["requeued"]:
                    runner.adopt(jid)
            except Exception:  # noqa: BLE001
                log.exception("plan job reaper failed; will retry")
            stop.wait(30)
    threading.Thread(target=loop, daemon=True, name="plan-reaper").start()
