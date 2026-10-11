"""Plan jobs, the quota saga and the idempotency key (ADR 0008, invariants I3, I4)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.api.deps import providers_dep
from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.db.session import SessionLocal
from app.main import app
from app.models.plan_job import PlanJob, PlanRefund
from app.models.trip import Trip
from app.models.user import User
from app.services import plan_jobs as PJ
from app.services import plan_runner as PR
from app.services import quota_service
from app.services import trip_service as TS
from tests.conftest import register, trip_payload
from tests.sim_providers import sim_providers

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean():
    app.dependency_overrides.clear()
    PR.runner.reset()
    with SessionLocal() as db:  # jobs another test left pending or running would be found by this test`s reaper
        db.execute(update(PlanJob).where(PlanJob.state.in_(PJ.ACTIVE)).values(state="done", result_status="test_cleanup"))
        db.commit()
    yield
    app.dependency_overrides.clear()
    PR.runner.reset()


def _use(**over):
    p = sim_providers("normal")
    app.dependency_overrides[providers_dep] = lambda: p
    return p


def _setup(client, **over):
    auth = register(client)
    tid = client.post("/api/trips", json=trip_payload(**over), headers=auth).json()["id"]
    return auth, tid


def _left(client, auth):
    return client.get("/api/auth/me", headers=auth).json()["subscription"]["free_plans_left"]


def _jobs(trip_id):
    with SessionLocal() as db:
        return list(db.scalars(select(PlanJob).where(PlanJob.trip_id == uuid.UUID(trip_id)).order_by(PlanJob.created_at)))


def _refunds(trip_id) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(PlanRefund).join(PlanJob, PlanJob.id == PlanRefund.job_id)
                         .where(PlanJob.trip_id == uuid.UUID(trip_id)))


def _blocking(p):
    """Providers whose first call waits until `release()`, so a plan can be held mid-way."""
    gate, entered = threading.Event(), threading.Event()
    orig = p.places.resolve

    def slow(*a, **k):
        entered.set()
        gate.wait(60)
        return orig(*a, **k)
    p.places.resolve = slow
    return gate, entered


# --- the job row is written with the quota, and ends in the right state ---------------------------------------------------

def test_a_plan_leaves_one_finished_job_and_spends_one_plan(client):
    _use()
    auth, tid = _setup(client)
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "ready"
    (job,) = _jobs(tid)
    assert (job.state, job.result_status, job.kind, job.lane, job.quota_source) == ("done", "ready", "new", PJ.LANE_FREE, "free")
    assert job.duration_ms and job.started_at and job.finished_at
    assert _left(client, auth) == 1 and _refunds(tid) == 0


def test_a_replan_is_a_first_lane_job_that_costs_no_plan(client):
    _use()
    auth, tid = _setup(client)
    client.post(f"/api/trips/{tid}/plan", headers=auth)
    r = client.post(f"/api/trips/{tid}/replan", json={"message": "start at 9am"}, headers=auth)
    assert r.status_code == 200
    jobs = _jobs(tid)
    assert [j.kind for j in jobs] == ["new", "replan"] and jobs[1].lane == PJ.LANE_REPLAN and jobs[1].quota_source is None
    assert _left(client, auth) == 1


def test_a_failing_plan_refunds_the_quota_once_and_marks_the_job_failed(client):
    p = _use()
    p.places.attractions = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    auth, tid = _setup(client)
    with pytest.raises(RuntimeError):
        client.post(f"/api/trips/{tid}/plan", headers=auth)
    (job,) = _jobs(tid)
    assert job.state == "failed" and job.error_code == "internal_error"
    assert _left(client, auth) == 2 and _refunds(tid) == 1
    trip = client.get(f"/api/trips/{tid}", headers=auth).json()
    assert trip["status"] == "draft" and trip["plan_count"] == 0


def test_a_provider_outage_refunds_once_and_is_a_normal_answer(client):
    p = _use()
    p.places.attractions = lambda *a, **k: (_ for _ in ()).throw(ProviderUnavailable("down"))
    auth, tid = _setup(client)
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200 and r.json()["state"] == "unavailable" and r.json()["quota_used"] is False
    (job,) = _jobs(tid)
    assert job.state == "failed" and job.result_status == "unavailable"
    assert _left(client, auth) == 2 and _refunds(tid) == 1


def test_an_infeasible_plan_refunds_once(client):
    _use()
    auth, tid = _setup(client, budget=300)
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200 and (r.json().get("feasibility") or {}).get("feasible") is False
    (job,) = _jobs(tid)
    assert job.state == "done" and job.result_status == "infeasible"
    assert _left(client, auth) == 2 and _refunds(tid) == 1


def test_a_job_that_could_not_be_queued_hands_the_quota_back(client, monkeypatch):
    _use()
    auth, tid = _setup(client)

    def broken(*a, **k):
        raise RuntimeError("queue broke")
    monkeypatch.setattr(PR.runner, "submit", broken)
    with pytest.raises(RuntimeError):
        client.post(f"/api/trips/{tid}/plan", headers=auth)
    (job,) = _jobs(tid)
    assert job.state == "refunded" and job.error_code == "enqueue_failed"
    assert _left(client, auth) == 2 and _refunds(tid) == 1
    assert client.get(f"/api/trips/{tid}", headers=auth).json()["status"] == "draft"


# --- the refund can never run twice --------------------------------------------------------------------------------------

def test_the_refund_record_makes_a_refund_exactly_once(client):
    auth, tid = _setup(client)
    with SessionLocal() as db:
        uid = db.get(Trip, uuid.UUID(tid)).owner_id
        user, trip = db.get(User, uid), TS.load_trip(db, uuid.UUID(tid))
        quota_service.reserve(db, user, trip)
        job, _ = PJ.create(db, user, trip, kind="new", key="once", change_note=None)
        db.commit()
    assert _left(client, auth) == 1
    for _ in range(5):  # the planner, a retry, the reaper: whoever calls
        with SessionLocal() as db:
            user, trip = db.get(User, uid), db.get(Trip, uuid.UUID(tid))
            token = PJ.CURRENT_JOB.set(job.id)
            try:
                quota_service.refund(db, user, trip)
            finally:
                PJ.CURRENT_JOB.reset(token)
            db.commit()
    assert _left(client, auth) == 2 and _refunds(tid) == 1  # given back once, not five times


# --- the idempotency key: a tap is one plan -------------------------------------------------------------------------------

def test_the_same_key_is_the_same_plan_and_one_charge(client):
    _use()
    auth, tid = _setup(client)
    h = {**auth, "Idempotency-Key": "tap-0001"}
    first = client.post(f"/api/trips/{tid}/plan", headers=h)
    again = client.post(f"/api/trips/{tid}/plan", headers=h)  # a network retry of the same tap, long after it finished
    assert first.status_code == again.status_code == 200
    assert first.json()["status"] == again.json()["status"] == "ready"
    assert len(_jobs(tid)) == 1 and _left(client, auth) == 1


def test_a_new_tap_after_a_failure_really_runs_again(client):
    p = _use()
    original = p.places.attractions
    p.places.attractions = lambda *a, **k: (_ for _ in ()).throw(ProviderUnavailable("down"))
    auth, tid = _setup(client)
    assert client.post(f"/api/trips/{tid}/plan", headers={**auth, "Idempotency-Key": "tap-a"}).json()["state"] == "unavailable"
    p.places.attractions = original  # the provider is back
    r = client.post(f"/api/trips/{tid}/plan", headers={**auth, "Idempotency-Key": "tap-b"})
    assert r.json()["status"] == "ready" and len(_jobs(tid)) == 2 and _left(client, auth) == 1
    # without a key, a retry after the failure is also a new plan (the old job has ended)


def test_a_double_tap_while_the_plan_runs_is_one_job_and_one_charge(client):
    p = _use()
    gate, entered = _blocking(p)
    auth, tid = _setup(client)
    results: list = []

    def tap():
        with TestClient(app) as c:
            r = c.post(f"/api/trips/{tid}/plan", headers=auth)
            results.append((r.status_code, r.json().get("status")))
    t1 = threading.Thread(target=tap)
    t1.start()
    assert entered.wait(20)  # the first plan is running, held mid-way
    t2 = threading.Thread(target=tap)
    t2.start()
    time.sleep(0.5)
    gate.set()
    t1.join(60)
    t2.join(60)
    assert results == [(200, "ready"), (200, "ready")]
    assert len(_jobs(tid)) == 1 and _left(client, auth) == 1


def test_a_streamed_plan_tells_how_to_follow_the_same_job_twice(client):
    _use()
    auth, tid = _setup(client)
    h = {**auth, "Idempotency-Key": "stream-1"}
    a = [json.loads(x) for x in client.post(f"/api/trips/{tid}/plan?stream=true", headers=h).text.splitlines() if x.strip()]
    b = [json.loads(x) for x in client.post(f"/api/trips/{tid}/plan?stream=true", headers=h).text.splitlines() if x.strip()]
    assert a[-1]["type"] == b[-1]["type"] == "result" and a[-1]["status"] == b[-1]["status"] == "ready"
    assert len(_jobs(tid)) == 1


# --- a crash: the reaper refunds, exactly once ----------------------------------------------------------------------------

def _stuck_job(client, auth, tid, *, age_seconds: int, heartbeat_age: int, state="running", attempt=1) -> uuid.UUID:
    """A job as a dead process leaves it: quota reserved, trip planning, a heartbeat that stopped."""
    with SessionLocal() as db:
        trip = TS.load_trip(db, uuid.UUID(tid))
        user = db.get(User, trip.owner_id)
        quota_service.reserve(db, user, trip)
        job, _ = PJ.create(db, user, trip, kind="new", key=f"stuck-{uuid.uuid4()}", change_note=None)
        trip.status = "planning"
        db.commit()
        now = datetime.now(UTC)
        db.execute(update(PlanJob).where(PlanJob.id == job.id).values(
            state=state, attempt=attempt, created_at=now - timedelta(seconds=age_seconds),
            heartbeat_at=now - timedelta(seconds=heartbeat_age)))
        db.commit()
        return job.id


def test_ten_racing_reapers_refund_a_dead_plan_exactly_once(client, monkeypatch):
    auth, tid = _setup(client)
    s = get_settings()
    monkeypatch.setattr(s, "reaper_stale_seconds", 60)
    monkeypatch.setattr(s, "job_requeue_max_age_seconds", 300)
    jid = _stuck_job(client, auth, tid, age_seconds=3600, heartbeat_age=600)  # too old to run again
    assert _left(client, auth) == 1
    out: list = []
    start = threading.Barrier(10)

    def go():
        start.wait()
        out.append(PJ.reap())
    ts = [threading.Thread(target=go) for _ in range(10)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sum(o["refunded"] for o in out) == 1  # one reaper did it; the other nine changed nothing
    assert _left(client, auth) == 2 and _refunds(tid) == 1
    with SessionLocal() as db:
        job = db.get(PlanJob, jid)
        assert job.state == "refunded" and job.error_code == "stale"
        assert db.get(Trip, uuid.UUID(tid)).status == "draft"
    assert PJ.reap()["refunded"] == 0 and _left(client, auth) == 2  # and again later: still once


def test_a_young_interrupted_plan_is_run_again_once_not_refunded(client, monkeypatch):
    _use()
    auth, tid = _setup(client)
    s = get_settings()
    monkeypatch.setattr(s, "reaper_stale_seconds", 60)
    jid = _stuck_job(client, auth, tid, age_seconds=90, heartbeat_age=120)
    out = PJ.reap()
    assert out["requeued"] == [jid] and out["refunded"] == 0
    PR.runner.adopt(jid)  # a live process picks it up
    for _ in range(100):
        with SessionLocal() as db:
            if db.get(PlanJob, jid).state == "done":
                break
        time.sleep(0.2)
    with SessionLocal() as db:
        job = db.get(PlanJob, jid)
        assert job.state == "done" and job.result_status == "ready" and job.attempt == 2
    assert _left(client, auth) == 1 and _refunds(tid) == 0  # the plan was made, so the plan stays used


def test_a_job_with_a_live_heartbeat_is_left_alone(client, monkeypatch):
    auth, tid = _setup(client)
    monkeypatch.setattr(get_settings(), "reaper_stale_seconds", 60)
    _stuck_job(client, auth, tid, age_seconds=3600, heartbeat_age=5)
    assert PJ.reap() == {"requeued": [], "refunded": 0} and _left(client, auth) == 1


def test_a_plan_killed_midway_is_refunded_exactly_once(client, monkeypatch):
    """A real process is killed while it holds a running plan. Nothing in this test cleans up after it except the reaper."""
    auth, tid = _setup(client)
    s = get_settings()
    with SessionLocal() as db:
        trip = TS.load_trip(db, uuid.UUID(tid))
        user = db.get(User, trip.owner_id)
        quota_service.reserve(db, user, trip)
        job, _ = PJ.create(db, user, trip, kind="new", key="kill-me", change_note=None)
        trip.status = "planning"
        db.commit()
        jid = job.id
    assert _left(client, auth) == 1
    script = (
        "import sys, time, uuid\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import app.models\n"
        "from app.services import plan_jobs as PJ, plan_runner as PR\n"
        "from tests.sim_providers import sim_providers\n"
        "p = sim_providers('normal')\n"
        "def hang(*a, **k):\n    time.sleep(3600)\n"
        "p.places.resolve = hang\n"
        "jid = uuid.UUID(sys.argv[1])\n"
        "PR.runner.submit(jid, PJ.LANE_FREE, PR.make_work(jid, p, read_only=False))\n"
        "print('submitted', flush=True)\n"
        "time.sleep(3600)\n")
    env = {**os.environ, "JOB_HEARTBEAT_SECONDS": "1"}
    proc = subprocess.Popen([sys.executable, "-c", script, str(jid)], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "submitted"
        beat = None
        for _ in range(100):  # the other process claims the job and keeps its heartbeat going
            with SessionLocal() as db:
                j = db.get(PlanJob, jid)
                if j.state == "running":
                    beat = j.heartbeat_at
                    break
            time.sleep(0.2)
        assert beat is not None, "the other process never started the plan"
        proc.kill()  # no cleanup, no shutdown hook: the process is just gone
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
    monkeypatch.setattr(s, "reaper_stale_seconds", 2)
    monkeypatch.setattr(s, "job_requeue_max_age_seconds", 0)  # too old to run again: refund
    with SessionLocal() as db:
        assert db.get(PlanJob, jid).state == "running"  # the dead process left it running; nobody has acted yet
    time.sleep(2.6)
    out = PJ.reap()
    assert out["refunded"] == 1
    assert _left(client, auth) == 2 and _refunds(tid) == 1
    with SessionLocal() as db:
        assert db.get(PlanJob, jid).state == "refunded" and db.get(Trip, uuid.UUID(tid)).status == "draft"
    for _ in range(3):  # however often the reaper looks again
        assert PJ.reap()["refunded"] == 0
    assert _left(client, auth) == 2 and _refunds(tid) == 1


# --- housekeeping ----------------------------------------------------------------------------------------------------------

def test_finished_plan_jobs_are_deleted_after_their_retention(client):
    _use()
    auth, tid = _setup(client)
    client.post(f"/api/trips/{tid}/plan", headers=auth)
    with SessionLocal() as db:
        assert PJ.purge_old(db) == 0  # fresh
        assert PJ.purge_old(db, datetime.now(UTC) + timedelta(days=get_settings().job_retention_days + 1)) >= 1
        db.rollback()


def test_a_derived_key_ignores_nothing_the_client_cares_about(client):
    auth, tid = _setup(client)
    with SessionLocal() as db:
        trip = TS.load_trip(db, uuid.UUID(tid))
        a = PJ.derived_prefix(trip, "new", '{"stream":false}')
        assert a == PJ.derived_prefix(trip, "new", '{"stream":false}')
        assert a != PJ.derived_prefix(trip, "replan", '{"stream":false}')
        assert a != PJ.derived_prefix(trip, "new", '{"stream":true}')
    assert PJ.clean_key("tap-1") == "tap-1" and PJ.clean_key("bad key!") is None and PJ.clean_key(None) is None
    assert len(PJ.clean_key("x" * 500)) == 100


def test_a_plan_that_finishes_after_its_refund_is_kept_logged_and_counted(client, monkeypatch, caplog):
    """Decided 2026-10-07: a stalled plan that finishes after the reaper refunded it keeps its plan and stays refunded."""
    auth, tid = _setup(client)
    monkeypatch.setattr(get_settings(), "reaper_stale_seconds", 60)
    monkeypatch.setattr(get_settings(), "job_requeue_max_age_seconds", 300)
    before = PJ.late_finishes()
    jid = _stuck_job(client, auth, tid, age_seconds=3600, heartbeat_age=600)
    assert PJ.reap()["refunded"] == 1 and _left(client, auth) == 2
    with caplog.at_level("WARNING", logger="tripai.plan_jobs"):
        assert PJ.complete(jid, "done", result_status="ready", duration_ms=1234) is False  # the plan comes back and finishes
    assert "finished late" in caplog.text and str(jid) in caplog.text
    with SessionLocal() as db:
        job = db.get(PlanJob, jid)
        assert job.state == "refunded" and job.late_finish_at is not None and job.result_status == "ready"
    assert _left(client, auth) == 2 and _refunds(tid) == 1  # still refunded, not charged again, not refunded twice
    after = PJ.late_finishes()
    assert after["total"] == before["total"] + 1 and after["today"] == before["today"] + 1
    assert PR.runner.stats()["late_finishes"] == after  # the plans admin view shows the same count
    PJ.complete(jid, "done", result_status="ready")  # a second report of the same plan is not counted again
    assert PJ.late_finishes() == after
