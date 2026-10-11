"""The bounded plan runner: lanes, aging, admission, the wait estimate (ADR 0008)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import providers_dep
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.gateway import capacity
from app.main import app
from app.models.plan_job import PlanJob
from app.services import plan_jobs as PJ
from app.services import plan_runner as PR
from tests.conftest import register, trip_payload
from tests.sim_providers import sim_providers


@pytest.fixture
def R(monkeypatch):
    """A private runner with room for one plan at a time, so everything else queues."""
    s = get_settings()
    monkeypatch.setattr(s, "max_concurrent_plans", 1)
    monkeypatch.setattr(s, "plan_queue_limit", 3)
    monkeypatch.setattr(s, "plan_queue_hard_limit", 4)
    monkeypatch.setattr(s, "lane_max_wait_seconds", 3600)
    r = PR.InProcessRunner()
    r._started = True  # no heartbeat thread, no database seeding: these jobs have no rows
    return r


class Work:
    """A job's work: records when it ran, and waits to be released."""

    def __init__(self, name, log, gate=None):
        self.name, self.log, self.gate = name, log, gate
        self.ran = threading.Event()

    def __call__(self, ticket):
        self.log.append(self.name)
        self.ran.set()
        if self.gate is not None:
            self.gate.wait(30)
        ticket.finish(PR.Outcome({"type": "result"}))
        return 10.0


def _submit(r, name, lane, log, gate=None):
    w = Work(name, log, gate)
    return w, r.submit(uuid.uuid4(), lane, w)


def test_lanes_replan_then_paid_then_free_and_arrival_order_within_a_lane(R):
    log, gate = [], threading.Event()
    first, _ = _submit(R, "first", PJ.LANE_FREE, log, gate)
    assert first.ran.wait(5)
    ws = [_submit(R, n, lane, log)[0] for n, lane in (("free1", PJ.LANE_FREE), ("paid1", PJ.LANE_PAID), ("free2", PJ.LANE_FREE),
                                                      ("replan1", PJ.LANE_REPLAN), ("paid2", PJ.LANE_PAID))]
    gate.set()
    assert ws[-1].ran.wait(10) and all(w.ran.wait(10) for w in ws)
    assert log == ["first", "replan1", "paid1", "paid2", "free1", "free2"]


def test_a_plan_that_waited_too_long_is_first_lane_so_nobody_starves(R, monkeypatch):
    monkeypatch.setattr(get_settings(), "lane_max_wait_seconds", 0)  # every waiting plan counts as aged
    log, gate = [], threading.Event()
    first, _ = _submit(R, "first", PJ.LANE_FREE, log, gate)
    assert first.ran.wait(5)
    old_free = _submit(R, "old_free", PJ.LANE_FREE, log)[0]
    new_replan = _submit(R, "new_replan", PJ.LANE_REPLAN, log)[0]
    gate.set()
    assert old_free.ran.wait(10) and new_replan.ran.wait(10)
    assert log == ["first", "old_free", "new_replan"]  # the free plan, waiting longest, is not overtaken for ever


def test_concurrency_never_exceeds_the_limit_under_a_burst(R, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_concurrent_plans", 6)
    lock, state = threading.Lock(), {"now": 0, "peak": 0, "done": 0}

    def work(ticket):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.02)
        with lock:
            state["now"] -= 1
            state["done"] += 1
        ticket.finish(PR.Outcome({"type": "result"}))
        return 1.0
    for _ in range(100):
        R.submit(uuid.uuid4(), PJ.LANE_FREE, work)
    for _ in range(200):
        if state["done"] == 100:
            break
        time.sleep(0.05)
    assert state["done"] == 100 and state["peak"] <= 6


def test_a_waiting_plan_is_told_its_place_and_a_wait_from_real_plan_durations(R):
    R.seed([20.0, 20.0, 20.0, 30.0])  # recent real plans, newest first: median 20 s
    gate, log = threading.Event(), []
    first, _ = _submit(R, "first", PJ.LANE_FREE, log, gate)
    assert first.ran.wait(5)
    _, t2 = _submit(R, "second", PJ.LANE_FREE, log)
    _, t3 = _submit(R, "third", PJ.LANE_FREE, log)
    q2 = [e for e in t2.subscribe().queue if e and e["type"] == "queue"]
    q3 = [e for e in t3.subscribe().queue if e and e["type"] == "queue"]
    assert q2[-1]["position"] == 1 and q3[-1]["position"] == 2
    assert q2[-1]["eta_seconds"] == 20 and q3[-1]["eta_seconds"] == 40  # position x median / plans at once
    lo, hi = q3[-1]["eta_range"]
    assert lo <= 40 <= hi and lo < hi
    gate.set()
    t3.wait(10)


def test_with_no_finished_plans_yet_the_wait_is_not_invented(R):
    gate, log = threading.Event(), []
    first, _ = _submit(R, "first", PJ.LANE_FREE, log, gate)
    assert first.ran.wait(5)
    _, t2 = _submit(R, "second", PJ.LANE_FREE, log)
    ev = [e for e in t2.subscribe().queue if e and e["type"] == "queue"][-1]
    assert ev["position"] == 1 and ev["eta_seconds"] is None and ev["eta_range"] is None
    gate.set()
    t2.wait(10)


def test_the_estimate_follows_what_plans_really_took(R):
    log = []
    for _ in range(3):
        w, t = _submit(R, "x", PJ.LANE_FREE, log)
        t.wait(5)
    time.sleep(0.2)
    assert R.stats()["median_plan_seconds"] == 10.0 and R.stats()["samples"] == 3  # what the work reported, not a constant
    R._durations.clear()
    R.seed([60.0])
    assert R.stats()["median_plan_seconds"] == 60.0


def test_the_queue_position_updates_as_plans_ahead_finish(R):
    gate1, gate2, log = threading.Event(), threading.Event(), []
    a, _ = _submit(R, "a", PJ.LANE_FREE, log, gate1)
    assert a.ran.wait(5)
    b, tb = _submit(R, "b", PJ.LANE_FREE, log, gate2)
    _, tc = _submit(R, "c", PJ.LANE_FREE, log)
    assert [e["position"] for e in tc.subscribe().queue if e and e["type"] == "queue"] == [2]
    gate1.set()
    assert b.ran.wait(5)
    time.sleep(0.2)
    positions = [e["position"] for e in tc.subscribe().queue if e and e["type"] == "queue"]
    assert positions == [1]  # only the latest place is kept for someone who joins late
    gate2.set()
    tc.wait(10)


# --- admission control -------------------------------------------------------------------------------------------------------

def test_past_the_hard_limit_new_free_plans_are_refused_but_replans_and_paid_still_enter(R):
    gate, log = threading.Event(), []
    first, _ = _submit(R, "first", PJ.LANE_FREE, log, gate)
    assert first.ran.wait(5)
    for i in range(4):  # the hard limit is 4 waiting
        _submit(R, f"w{i}", PJ.LANE_FREE, log)
    refused = R.admit(PJ.LANE_FREE)
    assert refused is not None and refused.reason == "high_demand" and refused.retry_at > datetime.now(UTC)
    assert "None of your free plans were used" in refused.message and "trip details are saved" in refused.message
    assert R.admit(PJ.LANE_PAID) is None and R.admit(PJ.LANE_REPLAN) is None
    for i in range(8):  # 12 waiting = three times the hard limit: even those wait outside now
        _submit(R, f"x{i}", PJ.LANE_REPLAN, log)
    assert R.admit(PJ.LANE_REPLAN) is not None
    gate.set()


def test_below_the_limits_everyone_is_admitted(R):
    assert R.admit(PJ.LANE_FREE) is None and R.admit(PJ.LANE_PAID) is None and R.admit(PJ.LANE_REPLAN) is None


def test_a_runner_that_is_shutting_down_says_busy_not_error(R):
    R.accepting = False
    out = R.admit(PJ.LANE_FREE)
    assert out is not None and out.reason == "provider_busy"


def test_the_stats_report_lanes_limits_and_what_brownout_the_numbers_suggest(R):
    gate, log = threading.Event(), []
    first, _ = _submit(R, "first", PJ.LANE_FREE, log, gate)
    assert first.ran.wait(5)
    for lane in (PJ.LANE_FREE, PJ.LANE_PAID, PJ.LANE_REPLAN):
        _submit(R, "w", lane, log)
    st = R.stats()
    assert st["running"] == 1 and st["waiting"] == 3 and st["waiting_by_lane"] == {"replan": 1, "paid": 1, "free": 1}
    assert st["max_concurrent"] == 1 and st["queue_limit"] == 3 and st["hard_limit"] == 4 and st["brownout_auto"] is False
    assert st["brownout_suggested"] == 2  # three waiting = the queue limit: suggested, never applied
    gate.set()


# --- through the API -------------------------------------------------------------------------------------------------------

def _use():
    p = sim_providers("normal")
    app.dependency_overrides[providers_dep] = lambda: p
    return p


def test_the_hard_limit_is_a_normal_answer_that_keeps_every_free_plan(client, monkeypatch):
    _use()
    try:
        auth = register(client)
        tid = client.post("/api/trips", json=trip_payload(), headers=auth).json()["id"]
        refusal = capacity.Unavailable("high_demand", datetime.now(UTC).replace(microsecond=0), PR.HIGH_DEMAND_MESSAGE)
        monkeypatch.setattr(PR.runner, "admit", lambda lane: refusal if lane == PJ.LANE_FREE else None)
        r = client.post(f"/api/trips/{tid}/plan", headers=auth)
        body = r.json()
        assert r.status_code == 200 and body["state"] == "unavailable" and body["reason"] == "high_demand"
        assert body["quota_used"] is False and "None of your free plans were used" in body["message"]
        assert client.get("/api/auth/me", headers=auth).json()["subscription"]["free_plans_left"] == 2
        with SessionLocal() as db:
            assert not db.scalars(select(PlanJob).where(PlanJob.trip_id == uuid.UUID(tid))).all()  # nothing was created
        streamed = client.post(f"/api/trips/{tid}/plan?stream=true", headers=auth)
        ev = [json.loads(x) for x in streamed.text.splitlines() if x.strip()]
        assert ev[-1]["type"] == "result" and ev[-1]["reason"] == "high_demand" and not [e for e in ev if e["type"] == "error"]
    finally:
        app.dependency_overrides.clear()


def test_a_streamed_plan_behind_a_busy_runner_starts_with_its_place_in_line(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "max_concurrent_plans", 1)
    PR.runner.reset()
    p = _use()
    gate, entered = threading.Event(), threading.Event()
    orig = p.places.resolve

    def slow(*a, **k):
        entered.set()
        gate.wait(60)
        return orig(*a, **k)
    p.places.resolve = slow
    try:
        a1, a2 = register(client), register(client)
        t1 = client.post("/api/trips", json=trip_payload(), headers=a1).json()["id"]
        t2 = client.post("/api/trips", json=trip_payload(), headers=a2).json()["id"]
        first = threading.Thread(target=lambda: TestClient(app).post(f"/api/trips/{t1}/plan", headers=a1), daemon=True)
        first.start()
        assert entered.wait(20)  # the one running plan is held mid-way
        events: list[dict] = []

        def second():
            with TestClient(app) as c, c.stream("POST", f"/api/trips/{t2}/plan?stream=true", headers=a2) as r:
                for line in r.iter_lines():
                    if line.strip():
                        events.append(json.loads(line))
        th = threading.Thread(target=second, daemon=True)
        th.start()
        for _ in range(100):  # (the test client hands over a stream only when it ends, so wait for the line to form)
            if PR.runner.stats()["waiting"] == 1:
                break
            time.sleep(0.1)
        assert PR.runner.stats()["waiting"] == 1
        gate.set()
        th.join(60)
        first.join(60)
        q = next(e for e in events if e["type"] == "queue")  # the first thing it was told, before any stage
        assert events.index(q) == 0 and q["position"] == 1 and q["waiting"] == 1  # "High demand — you're #1"
        assert events[-1]["type"] == "result" and events[-1]["status"] == "ready"  # then it ran, in order
        assert not [e for e in events if e["type"] == "error"]
    finally:
        gate.set()
        app.dependency_overrides.clear()
        PR.runner.reset()


def test_the_admin_plans_view_is_hidden_from_everyone_else(client):
    assert client.get("/api/system/plans").status_code == 404
    assert client.get("/api/system/plans", headers=register(client)).status_code == 404
