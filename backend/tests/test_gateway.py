"""Phase 4, the provider gateway (ADR 0007): timeouts and deadlines, retries under a budget, circuit breakers and the quota
circuit, bulkheads, daily budgets and the plan cap, the typed "unavailable" answer (I3, I4), and the admin gate.
No real call is made: the HTTP layer is an httpx MockTransport and the clock is patched where time matters."""
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from app.api.deps import providers_dep
from app.core.config import Settings, get_settings
from app.core.errors import ProviderUnavailable
from app.core.telemetry import ContextThreadPool
from app.db.session import SessionLocal
from app.gateway import BudgetExhausted, BulkheadFull, CircuitOpen, DeadlineExceeded, QuotaExhausted, capacity, context as C, limits, request
from app.gateway import routing
from app.gateway.breaker import ASSUMED, breakers
from app.gateway.budget import budgets
from app.gateway.ledger import ledger
from app.gateway.retry import backoff, retry_budget
from app.main import app
from app.models.circuit import ProviderCircuit
from app.models.counters import DailyCounter
from app.providers import http
from tests.conftest import register, trip_payload
from tests.sim_providers import sim_providers

SEARCH = "https://places.googleapis.com/v1/places:searchText"
ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"
RAIL = "https://api.railradar.in/v1/trains/between/NDLS/AGC"
IRCTC = "https://irctc1.p.rapidapi.com/api/v1/searchStation"
LLM = "https://openrouter.ai/api/v1/chat/completions"
NOON = datetime(2026, 10, 7, 6, 30, tzinfo=UTC).timestamp()  # 12:00 in Asia/Kolkata


class Net:
    """The wire: records every request, answers with `handler`."""

    def __init__(self):
        self.calls: list[dict] = []
        self.handler = lambda req: httpx.Response(200, json={"ok": True})
        self.lock = threading.Lock()

    def _h(self, req):
        with self.lock:
            self.calls.append({"method": req.method, "url": str(req.url), "timeout": req.extensions.get("timeout", {}).get("read")})
        out = self.handler(req)
        if isinstance(out, Exception):
            raise out
        return out

    def install(self):
        http._client = httpx.Client(transport=httpx.MockTransport(self._h))

    def n(self, url_part=""):
        return sum(1 for c in self.calls if url_part in c["url"])


def _clean_db():
    with SessionLocal() as db:
        db.execute(delete(ProviderCircuit))
        for row in db.scalars(select(DailyCounter).where(DailyCounter.name.like("b:%"))):
            db.delete(row)
        db.commit()


@pytest.fixture
def gw(monkeypatch):
    s = get_settings()
    saved = {k: getattr(s, k) for k in ("budget_mode", "irctc1_assume_exhausted", "google_daily_usd", "railradar_daily_calls",
                                          "irctc1_daily_calls", "llm_daily_usd", "search_daily_usd", "daily_plan_cap", "budget_tz",
                                          "gateway_enabled", "gateway_breakers", "gateway_retry_budget", "gateway_bulkheads",
                                          "gateway_single_flight", "gateway_aimd", "gateway_health_routing", "plan_deadline_seconds",
                                          "admin_emails", "breaker_quota_open_minutes", "background_budget_share")}
    s.budget_mode, s.irctc1_assume_exhausted = "enforce", False
    s.google_daily_usd, s.railradar_daily_calls, s.irctc1_daily_calls = 100.0, 1000, 1000
    s.llm_daily_usd = s.search_daily_usd = 100.0
    s.daily_plan_cap = 1000
    for r in (breakers, budgets, ledger, retry_budget):
        r.reset()
    limits.reset()
    _clean_db()
    net = Net()
    net.install()
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda x: sleeps.append(x))  # no waiting in tests
    net.sleeps = sleeps
    clock = {"t": NOON}
    monkeypatch.setattr(C, "now", lambda: clock["t"])
    net.clock = clock
    yield net
    for k, v in saved.items():
        setattr(s, k, v)
    for r in (breakers, budgets, ledger, retry_budget):
        r.reset()
    limits.reset()
    _clean_db()
    http._client = None
    app.dependency_overrides.clear()


def resp(status, **kw):
    return httpx.Response(status, **kw)


# --- circuit breaker ---------------------------------------------------------------------------------------------------

def test_five_failures_open_the_circuit_and_an_open_circuit_makes_no_call(gw):
    gw.handler = lambda r: resp(500)  # a failure that is never retried
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            request("GET", RAIL, provider="RailRadar", retries=0)
    assert gw.n() == 5
    with pytest.raises(CircuitOpen) as e:
        request("GET", RAIL, provider="RailRadar", retries=0)
    assert gw.n() == 5 and isinstance(e.value, ProviderUnavailable)  # I3: every caller's fallback applies
    assert e.value.retry_at is not None


def test_half_open_lets_one_probe_through_then_closes_or_reopens_longer(gw):
    gw.handler = lambda r: resp(500)
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            request("GET", RAIL, provider="RailRadar", retries=0)
    gw.clock["t"] += 31  # the first back-off is 30 s
    with pytest.raises(ProviderUnavailable) as e:
        request("GET", RAIL, provider="RailRadar", retries=0)  # the probe: sent, fails
    assert not isinstance(e.value, CircuitOpen) and gw.n() == 6
    gw.clock["t"] += 31  # the next back-off is 60 s, so 31 s later it is still open
    with pytest.raises(CircuitOpen):
        request("GET", RAIL, provider="RailRadar", retries=0)
    gw.clock["t"] += 31
    gw.handler = lambda r: resp(200, json={"ok": 1})
    assert request("GET", RAIL, provider="RailRadar", retries=0).json() == {"ok": 1}  # probe succeeds
    assert request("GET", RAIL, provider="RailRadar", retries=0).status_code == 200  # closed again
    assert gw.n() == 8


def test_only_one_probe_is_let_through_while_half_open(gw):
    for _ in range(5):
        breakers.record("railradar", "GET /v1/trains/between/{id}/{id}", ok=False)
    gw.clock["t"] += 31
    first = breakers.before("railradar", "GET /v1/trains/between/{id}/{id}")
    assert first == "half-open"
    with pytest.raises(CircuitOpen):
        breakers.before("railradar", "GET /v1/trains/between/{id}/{id}")
    breakers.release("railradar", "GET /v1/trains/between/{id}/{id}", first)  # the probe was never sent
    assert breakers.before("railradar", "GET /v1/trains/between/{id}/{id}") == "half-open"


def test_breakers_are_per_endpoint(gw):
    gw.handler = lambda r: resp(500) if "searchText" in str(r.url) else resp(200, json={})
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            request("POST", SEARCH, provider="Google Places", retries=0, json={})
    with pytest.raises(CircuitOpen):
        request("POST", SEARCH, provider="Google Places", retries=0, json={})
    assert request("POST", ROUTES, provider="Google Routes", retries=0, json={}).status_code == 200


def test_client_errors_are_not_failures(gw):
    gw.handler = lambda r: resp(404, json={"error": "nope"})
    for _ in range(30):
        with pytest.raises(ProviderUnavailable):
            request("GET", RAIL, provider="RailRadar", retries=0)
    assert gw.n() == 30  # never opened: a 404 is about the request, not the provider
    assert request("GET", RAIL, provider="RailRadar", retries=0, raw=True).status_code == 404  # raw: the caller reads it


def test_a_failure_rate_of_half_over_ten_calls_opens_it(gw):
    outcomes = iter([200, 500] * 20)
    gw.handler = lambda r: resp(next(outcomes), json={})
    sent = 0
    with pytest.raises(CircuitOpen):
        for _ in range(40):
            try:
                request("GET", RAIL, provider="RailRadar", retries=0)
            except CircuitOpen:
                raise
            except ProviderUnavailable:
                pass
            sent += 1
    assert 10 <= sent <= 12 and gw.n() == sent  # opened at the tenth or eleventh call (failures reach half the window)


# --- quota circuit -----------------------------------------------------------------------------------------------------

def test_a_quota_answer_opens_the_provider_at_once_and_it_survives_a_restart(gw):
    gw.handler = lambda r: resp(429, text='{"message": "You have exceeded the MONTHLY quota"}')
    with pytest.raises(QuotaExhausted):
        request("GET", RAIL, provider="RailRadar")
    assert gw.n() == 1
    for _ in range(3):
        with pytest.raises(QuotaExhausted):
            request("GET", RAIL, provider="RailRadar")
    assert gw.n() == 1  # no network call while it is open
    with SessionLocal() as db:
        assert db.scalar(select(ProviderCircuit).where(ProviderCircuit.provider == "railradar")) is not None
    breakers.reset()  # a new process or another worker: memory is empty, the stored state is not
    with pytest.raises(QuotaExhausted):
        request("GET", RAIL, provider="RailRadar")
    assert gw.n() == 1


def test_the_quota_circuit_allows_one_probe_and_a_good_answer_closes_it(gw):
    gw.handler = lambda r: resp(429, text="quota exceeded")
    with pytest.raises(QuotaExhausted):
        request("GET", RAIL, provider="RailRadar")
    gw.clock["t"] += 361 * 60  # BREAKER_QUOTA_OPEN_MINUTES = 360
    results = []

    def go():
        try:
            results.append(request("GET", RAIL, provider="RailRadar", raw=True).status_code)
        except ProviderUnavailable as exc:
            results.append(type(exc).__name__)
    gw.handler = lambda r: (threading.Event().wait(0.25), resp(200, json={"ok": 1}))[1]  # the probe is still out when the rest arrive
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert gw.n() == 2 and results.count(200) >= 1  # the first call plus ONE probe; the quota circuit is closed after it
    assert request("GET", RAIL, provider="RailRadar").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(ProviderCircuit).where(ProviderCircuit.provider == "railradar")) is None


def test_a_remaining_quota_header_of_zero_opens_the_circuit(gw):
    gw.handler = lambda r: resp(200, json={}, headers={"X-RateLimit-Requests-Remaining": "0", "X-RateLimit-Requests-Limit": "1000"})
    assert request("GET", IRCTC, provider="IRCTC1").status_code == 200  # this answer is fine...
    with pytest.raises(QuotaExhausted):
        request("GET", IRCTC, provider="IRCTC1")  # ...but the next one would not be
    assert gw.n() == 1
    assert ledger.snapshot()["headers_seen"]["irctc1"]["x-ratelimit-requests-remaining"] == "0"


def test_ledger_reads_quota_headers_case_insensitively_and_ignores_junk():
    l = type(ledger)()
    assert l.see_headers("p", {"X-RateLimit-Remaining": "17", "X-RateLimit-Limit": "100", "Server": "x"}) == (17, 100)
    assert l.see_headers("p", {"ratelimit-remaining": "banana"}) == (None, None)
    assert l.see_headers("p", {"X-RapidAPI-Requests-Remaining": "3"}) == (3, None)
    assert set(l.headers["p"]) == {"x-ratelimit-remaining", "x-ratelimit-limit", "ratelimit-remaining", "x-rapidapi-requests-remaining"}


def test_irctc1_is_treated_as_exhausted_with_one_probe_a_day(gw):
    s = get_settings()
    s.irctc1_assume_exhausted = True
    gw.handler = lambda r: resp(429, text="exceeded the MONTHLY quota for Basic")
    for _ in range(4):
        with pytest.raises(QuotaExhausted):
            request("GET", IRCTC, provider="IRCTC1", raw=True)
    assert gw.n() == 0  # nothing is sent on the first day, however often it is asked
    with SessionLocal() as db:
        assert db.scalar(select(ProviderCircuit.reason).where(ProviderCircuit.provider == "irctc1")) == ASSUMED
    gw.clock["t"] += 24 * 3600 + 5
    outs = []
    threads = [threading.Thread(target=lambda: outs.append(_try(lambda: request("GET", IRCTC, provider="IRCTC1", raw=True)))) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert gw.n() == 1  # exactly ONE probe a day, which says "still exhausted"
    with pytest.raises(QuotaExhausted):
        request("GET", IRCTC, provider="IRCTC1", raw=True)
    assert gw.n() == 1
    gw.clock["t"] += 24 * 3600 + 5  # the next day's probe finds the quota reset
    gw.handler = lambda r: resp(200, json={"data": []})
    assert request("GET", IRCTC, provider="IRCTC1", raw=True).status_code == 200
    assert request("GET", IRCTC, provider="IRCTC1", raw=True).status_code == 200 and gw.n() == 3


def _try(fn):
    try:
        return fn().status_code
    except ProviderUnavailable as exc:
        return type(exc).__name__


def test_turning_the_irctc1_assumption_off_reopens_it(gw):
    s = get_settings()
    s.irctc1_assume_exhausted = True
    with pytest.raises(QuotaExhausted):
        request("GET", IRCTC, provider="IRCTC1", raw=True)
    s.irctc1_assume_exhausted = False
    breakers.reset()
    assert request("GET", IRCTC, provider="IRCTC1", raw=True).status_code == 200


def test_an_open_irctc1_circuit_is_not_shown_to_the_traveller(gw):
    from app.providers.rail_chain import RailChain

    class Src:
        def __init__(self, name, issue, out=()):
            self.name, self.last_issue, self.out = name, issue, list(out)

        def intercity(self, *a):
            return self.out
    get_settings().irctc1_assume_exhausted = True
    breakers.quota_until("irctc1", fresh=True)  # seeds the assumed-exhausted state
    chain = RailChain([Src("railradar", "RailRadar found no train"), Src("irctc1", "IRCTC1 quota used up")])
    assert chain.intercity(None, None, None, None) == []
    assert chain.last_issue == "RailRadar found no train"  # nothing about IRCTC1
    get_settings().irctc1_assume_exhausted = False
    breakers.reset()
    _clean_db()
    chain = RailChain([Src("railradar", None), Src("irctc1", "IRCTC1 quota used up")])
    chain.intercity(None, None, None, None)
    assert chain.last_issue == "IRCTC1 quota used up"  # when it is not open, its message stands


# --- retries -------------------------------------------------------------------------------------------------------------

def test_an_idempotent_call_is_retried_once_on_a_503(gw):
    answers = iter([resp(503), resp(200, json={"ok": 1})])
    gw.handler = lambda r: next(answers)
    assert request("GET", RAIL, provider="RailRadar").json() == {"ok": 1}
    assert gw.n() == 2 and len(gw.sleeps) == 1 and gw.sleeps[0] <= 0.3
    row = next(r for r in ledger.snapshot()["endpoints"] if r["provider"] == "railradar")
    assert row["retries"] == 1 and row["calls"] == 2


def test_a_non_idempotent_call_is_never_retried(gw):
    gw.handler = lambda r: resp(503)
    with pytest.raises(ProviderUnavailable):
        request("POST", LLM, provider="openrouter", retries=1, json={})  # an LLM answer is billed: the template is the fallback
    assert gw.n() == 1


@pytest.mark.parametrize("status, body", [(500, ""), (400, "bad"), (404, "gone"), (429, "Quota exceeded for this plan")])
def test_500_4xx_and_quota_answers_are_never_retried(gw, status, body):
    gw.handler = lambda r: resp(status, text=body)
    with pytest.raises(ProviderUnavailable):
        request("GET", RAIL, provider="RailRadar", retries=1)
    assert gw.n() == 1


def test_a_429_without_quota_wording_is_retried_and_retry_after_is_honoured_up_to_2s(gw):
    answers = iter([resp(429, text="slow down", headers={"Retry-After": "1"}), resp(200, json={})])
    gw.handler = lambda r: next(answers)
    request("GET", RAIL, provider="RailRadar")
    assert gw.sleeps == [1.0]
    assert backoff(0, 30) == 2.0 and backoff(0, 0.5) == 0.5


def test_backoff_is_jittered_and_bounded():
    vals = [backoff(a) for a in (0, 1, 2, 3, 6) for _ in range(200)]
    assert max(vals) <= 2.0 and min(vals) >= 0 and len({round(v, 3) for v in vals}) > 50
    assert max(backoff(0) for _ in range(200)) <= 0.3


def test_the_retry_budget_stops_a_failure_storm_from_doubling_the_spend(gw):
    get_settings().gateway_breakers = False  # isolate the retry budget from the breaker
    gw.handler = lambda r: resp(503)
    for _ in range(100):
        with pytest.raises(ProviderUnavailable):
            request("GET", RAIL, provider="RailRadar")
    # 100 calls + at most the initial burst of 10 retries + 0.1 retry per call (10): about 120, nowhere near 200
    assert 100 <= gw.n() <= 122


def test_an_empty_retry_budget_means_no_retry(gw):
    retry_budget._tokens["railradar"] = 0
    gw.handler = lambda r: resp(503)
    with pytest.raises(ProviderUnavailable):
        request("GET", RAIL, provider="RailRadar")
    assert gw.n() == 1


def test_the_old_retry_rule_comes_back_with_the_flag_off(gw):
    get_settings().gateway_retry_budget = False
    gw.handler = lambda r: resp(503)
    with pytest.raises(ProviderUnavailable):
        request("POST", LLM, provider="openrouter", retries=1, json={})
    assert gw.n() == 2  # the old behaviour: any method, one retry


# --- deadlines -----------------------------------------------------------------------------------------------------------

def test_a_call_never_waits_longer_than_the_plan_has_left(gw):
    with C.plan_scope(3.0):
        request("GET", RAIL, provider="RailRadar")
    assert gw.calls[0]["timeout"] <= 2.8  # min(8 s endpoint timeout, time left)
    request("GET", RAIL, provider="RailRadar")  # outside a plan: the endpoint's own timeout
    assert gw.calls[1]["timeout"] == 8


def test_endpoint_timeouts_come_from_the_measured_table(gw):
    for url, prov, want in ((SEARCH, "Google Places", 6), (ROUTES, "Google Routes", 6), (LLM, "openrouter", 15),
                            ("https://places.googleapis.com/v1/places:autocomplete", "Google Places", 3), (IRCTC, "IRCTC1", 6)):
        request("POST" if "googleapis" in url or "openrouter" in url else "GET", url, provider=prov, json={} if "googleapis" in url or "openrouter" in url else None, idempotent=True)
    assert [c["timeout"] for c in gw.calls] == [6, 6, 15, 3, 6]
    get_settings().gateway_timeouts = json.dumps({"railradar": 2.5})
    try:
        request("GET", RAIL, provider="RailRadar")
    finally:
        get_settings().gateway_timeouts = None
    assert gw.calls[-1]["timeout"] == 2.5


def test_with_under_a_second_left_no_call_is_made_and_the_stage_is_logged(gw, caplog):
    caplog.set_level(logging.WARNING, logger="tripai.gateway")
    with C.plan_scope(0.5) as scope:
        C.set_stage("routes")
        with pytest.raises(DeadlineExceeded):
            request("POST", ROUTES, provider="Google Routes", json={})
    assert gw.n() == 0
    assert scope.deadline_stage == "routes"
    assert scope.degraded == [{"group": "google", "endpoint": "POST /directions/v2:computeRoutes", "reason": "deadline",
                               "stage": "routes", "provider": "google-routes"}]
    assert "deadline reached during stage 'routes'" in caplog.text


def test_a_retry_is_not_started_when_there_is_no_time_for_it(gw, monkeypatch):
    from app.gateway import core
    monkeypatch.setattr(core, "backoff", lambda attempt, retry_after=None: 0.5)
    gw.handler = lambda r: resp(503)
    with C.plan_scope(1.4):
        with pytest.raises(ProviderUnavailable):
            request("GET", RAIL, provider="RailRadar")
    assert gw.n() == 1


def test_the_deadline_reaches_pool_threads():
    with C.plan_scope(30) as scope:
        C.set_stage("places")
        with ContextThreadPool(max_workers=2) as pool:
            seen = pool.submit(lambda: (C.remaining() is not None and C.remaining() > 25, C.stage(), C.scope() is scope)).result()
    assert seen == (True, "places", True)


def test_no_deadline_without_a_plan_scope_and_a_zero_setting_means_none():
    assert C.remaining() is None
    with C.plan_scope(0) as scope:
        assert scope.deadline is None and C.remaining() is None


# --- bulkheads and AIMD --------------------------------------------------------------------------------------------------

def test_a_bulkhead_caps_concurrency_per_provider(gw):
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    def handler(r):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(0)  # patched to a no-op: use a real wait instead
        threading.Event().wait(0.03)
        with lock:
            live["now"] -= 1
        return resp(200, json={})
    gw.handler = handler
    with ContextThreadPool(max_workers=16) as pool:
        list(pool.map(lambda _: request("GET", RAIL, provider="RailRadar"), range(24)))
    assert live["max"] <= 4  # RailRadar's limit
    assert gw.n() == 24


def test_a_call_that_would_wait_past_the_deadline_fails_fast(gw):
    limits.limiter_for("irctc1")  # limit 2
    lim = limits.limiter_for("irctc1")
    assert lim.acquire(1) and lim.acquire(1)  # both slots taken
    try:
        with C.plan_scope(1.6):  # 0.6 s left after the 1 s reserve
            t0 = time.perf_counter()
            with pytest.raises(BulkheadFull):
                request("GET", IRCTC, provider="IRCTC1")
            assert time.perf_counter() - t0 < 1.5
    finally:
        lim.release()
        lim.release()
    assert gw.n() == 0


def test_the_bulkhead_flag_turns_the_limit_off(gw):
    get_settings().gateway_bulkheads = False
    lim = limits.limiter_for("irctc1")
    assert lim.acquire(1) and lim.acquire(1)
    assert request("GET", IRCTC, provider="IRCTC1").status_code == 200  # no slot was needed
    lim.release()
    lim.release()


def test_aimd_halves_on_trouble_and_grows_on_success_only_when_on():
    off = limits.Limiter(8, aimd=False)
    for ok in (False, False, True):
        off.acquire(1)
        off.release(ok)
    assert off.limit == 8
    on = limits.Limiter(8, aimd=True)
    on.acquire(1)
    on.release(False)
    assert on.limit == 4
    for _ in range(4):
        on.acquire(1)
        on.release(True)
    assert on.limit == 5
    for _ in range(10):
        on.acquire(1)
        on.release(False)
    assert on.limit == 1  # never below the floor


# --- budgets -----------------------------------------------------------------------------------------------------------

def test_warn_at_80_percent_and_stop_at_100(gw, caplog):
    caplog.set_level(logging.WARNING, logger="tripai.gateway.budget")
    get_settings().google_daily_usd = 0.1  # a text search is $0.032
    for _ in range(3):
        request("POST", SEARCH, provider="Google Places", json={})
    assert "budget warning: google" in caplog.text  # $0.096 is past 80% of $0.10
    with pytest.raises(BudgetExhausted) as e:
        request("POST", SEARCH, provider="Google Places", json={})
    assert gw.n() == 3  # not sent
    assert e.value.retry_at.isoformat().endswith("T00:00:00+05:30") and isinstance(e.value, ProviderUnavailable)
    st = next(b for b in budgets.status() if b["group"] == "google")
    assert st["warned"] and st["percent"] == 96.0 and not st["stopped"]  # $0.096 of $0.10: the next $0.032 call doesn't fit


def test_warn_mode_lets_calls_through_and_off_mode_counts_nothing(gw, caplog):
    s = get_settings()
    s.google_daily_usd, s.budget_mode = 0.01, "warn"
    for _ in range(3):
        request("POST", SEARCH, provider="Google Places", json={})
    assert gw.n() == 3 and "used up" in caplog.text
    s.budget_mode = "off"
    budgets.reset()
    request("POST", SEARCH, provider="Google Places", json={})
    assert budgets.used("google") == 0


def test_background_work_gets_only_half_the_day(gw):
    s = get_settings()
    s.railradar_daily_calls = 10
    done = 0
    with C.background():
        for _ in range(8):
            try:
                request("GET", RAIL, provider="RailRadar")
                done += 1
            except BudgetExhausted:
                pass
    assert done == 5  # half
    for _ in range(5):
        request("GET", RAIL, provider="RailRadar")  # users still have the other half
    with pytest.raises(BudgetExhausted):
        request("GET", RAIL, provider="RailRadar")


def test_failed_calls_are_not_charged_and_timeouts_are(gw):
    get_settings().gateway_breakers = False
    gw.handler = lambda r: resp(503)
    with pytest.raises(ProviderUnavailable):
        request("POST", SEARCH, provider="Google Places", retries=0, json={})
    assert budgets.used("google") == 0  # a 5xx is not billed
    gw.handler = lambda r: httpx.ReadTimeout("slow", request=r)
    with pytest.raises(ProviderUnavailable):
        request("POST", SEARCH, provider="Google Places", retries=0, json={})
    assert budgets.used("google") == pytest.approx(0.032)  # a timeout may have been billed


def test_the_route_matrix_is_priced_per_element(gw):
    request("POST", "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", provider="Google Routes",
            json={"origins": [1, 2, 3], "destinations": [1, 2]})
    assert budgets.used("google") == pytest.approx(6 * 0.005)


def test_counts_are_flushed_exactly_and_other_workers_totals_are_read_back(gw):
    for _ in range(4):
        request("POST", SEARCH, provider="Google Places", json={})
    assert budgets.flush() == 1
    day = budgets.day_key()
    with SessionLocal() as db:
        assert db.scalar(select(DailyCounter.value).where(DailyCounter.day == day, DailyCounter.name == "b:google")) == 128_000
    budgets.reset()  # a restart: the day's total is read back, not forgotten
    assert budgets.used("google") == pytest.approx(0.128)
    with SessionLocal() as db:  # another worker spent $0.05 meanwhile
        row = db.scalar(select(DailyCounter).where(DailyCounter.day == day, DailyCounter.name == "b:google"))
        row.value += 50_000
        db.commit()
    request("POST", SEARCH, provider="Google Places", json={})
    budgets.flush()
    assert budgets.used("google") == pytest.approx(0.128 + 0.05 + 0.032)


def test_the_budget_day_resets_at_midnight_in_kolkata_and_the_zone_is_configurable(gw):
    s = get_settings()
    gw.clock["t"] = datetime(2026, 10, 6, 18, 29, tzinfo=UTC).timestamp()  # 23:59 IST
    assert budgets.day_key() == "2026-10-06"
    assert budgets.reset_at().isoformat() == "2026-10-07T00:00:00+05:30"
    gw.clock["t"] = datetime(2026, 10, 6, 18, 31, tzinfo=UTC).timestamp()  # 00:01 IST
    assert budgets.day_key() == "2026-10-07"
    s.budget_tz = "UTC"
    assert budgets.day_key() == "2026-10-06" and budgets.reset_at().isoformat() == "2026-10-07T00:00:00+00:00"


def test_the_ledger_records_who_spent(gw):
    uid = uuid.uuid4()
    with C.plan_scope(30, user_id=uid):
        request("POST", SEARCH, provider="Google Places", json={})
        request("POST", ROUTES, provider="Google Routes", json={})
    assert ledger.users[str(uid)] == pytest.approx(0.032 + 0.005)


def test_every_budget_is_an_env_var_and_the_plan_cap_sits_below_the_google_budget(monkeypatch):
    for k, v in {"GOOGLE_DAILY_USD": "3.5", "RAILRADAR_DAILY_CALLS": "7", "IRCTC1_DAILY_CALLS": "2", "LLM_DAILY_USD": "0.25",
                 "SEARCH_DAILY_USD": "0.5", "DAILY_PLAN_CAP": "9", "BUDGET_TZ": "UTC", "PLAN_DEADLINE_SECONDS": "30"}.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert (s.google_daily_usd, s.railradar_daily_calls, s.irctc1_daily_calls, s.llm_daily_usd, s.search_daily_usd) == (3.5, 7, 2, 0.25, 0.5)
    assert (s.daily_plan_cap, s.budget_tz, s.plan_deadline_seconds) == (9, "UTC", 30)
    d = Settings(_env_file=None)
    monkeypatch.undo()
    d = Settings(_env_file=None)
    assert (d.google_daily_usd, d.daily_plan_cap, d.budget_tz, d.plan_deadline_seconds) == (10.0, 25, "Asia/Kolkata", 60)
    assert d.daily_plan_cap * d.cold_plan_worst_usd < d.google_daily_usd  # people meet the polite cap before Google is cut off
    assert int(d.google_daily_usd // d.cold_plan_worst_usd) == 28 and d.daily_plan_cap < 28


def test_a_plan_cap_above_what_google_can_pay_for_is_flagged(caplog):
    s = get_settings()
    before = (s.daily_plan_cap, s.budget_mode, s.google_daily_usd)
    s.daily_plan_cap, s.budget_mode, s.google_daily_usd = 40, "enforce", 10.0
    try:
        caplog.set_level(logging.WARNING, logger="tripai.gateway.capacity")
        capacity.check_consistency()
    finally:
        s.daily_plan_cap, s.budget_mode, s.google_daily_usd = before
    assert "cut off before the plan cap applies" in caplog.text


# --- fall-through: a stopped rail source hands over, and nothing 5xx's ---------------------------------------------------

def test_a_rail_source_over_budget_falls_through_to_the_next(gw):
    from app.providers.rail_chain import RailChain
    from app.providers.railradar import RailRadarProvider
    from app.providers.types import PlaceData
    get_settings().railradar_daily_calls = 0

    class Next:
        name, last_issue = "irctc1", None

        def intercity(self, *a):
            return ["a train from the next source"]

    class NoPlaces:
        def resolve(self, *a):
            raise RuntimeError("offline")
    rr = RailRadarProvider(NoPlaces(), "k", store=False)
    jaipur = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)
    delhi = PlaceData("google", "d", "New Delhi", "city", 28.6139, 77.2090)
    chain = RailChain([rr, Next()])
    assert chain.intercity(jaipur, delhi, datetime(2026, 10, 17).date(), datetime(2026, 10, 17).date()) == ["a train from the next source"]
    assert gw.n() == 0  # RailRadar was never called


# --- the typed "unavailable" answer (I3, I4) -----------------------------------------------------------------------------

def _register_plan(client, **over):
    auth = register(client)
    trip = client.post("/api/trips", json=trip_payload(**over), headers=auth).json()
    return auth, trip["id"]


def _left(client, auth):
    return client.get("/api/auth/me", headers=auth).json()["subscription"]["free_plans_left"]


def _use(places_down=None, live=False, route_down=False):
    p = sim_providers("normal")
    if places_down is not None:
        def down(*a, **k):
            raise places_down
        for n in ("attractions", "restaurants", "resolve"):
            setattr(p.places, n, down)
    if route_down:
        p.maps.compute_route = lambda *a, **k: (_ for _ in ()).throw(ProviderUnavailable("routes down"))
    app.dependency_overrides[providers_dep] = lambda: p
    return p


def _assert_unavailable(body, reason="provider_busy"):
    assert body["state"] == "unavailable" and body["status"] == "unavailable" and body["reason"] == reason
    assert body["quota_used"] is False and "None of your free plans were used" in body["message"]
    assert "trip details are saved" in body["message"]
    assert datetime.fromisoformat(body["retry_at"]) > datetime.now(UTC)
    assert body["trip"]["id"] and body["trip"]["origin"]  # the trip's details come back, saved


def test_a_places_outage_is_a_normal_answer_not_a_503_and_the_free_plan_is_kept(client, gw):
    _use(places_down=ProviderUnavailable("Google Places is temporarily unavailable."))
    auth, tid = _register_plan(client)
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200, r.text
    _assert_unavailable(r.json())
    assert _left(client, auth) == 2
    trip = client.get(f"/api/trips/{tid}", headers=auth).json()
    assert trip["status"] == "draft" and trip["plan_count"] == 0 and trip["destination"] == "Jaipur"  # one tap to plan again
    app.dependency_overrides.clear()
    _use()  # Google is back: the same trip plans, and uses the plan now
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "ready" and _left(client, auth) == 1


def test_a_places_outage_on_the_stream_is_a_result_event_not_an_error(client, gw):
    _use(places_down=ProviderUnavailable("down"))
    auth, tid = _register_plan(client)
    r = client.post(f"/api/trips/{tid}/plan?stream=true", headers=auth)
    assert r.status_code == 200
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert not [e for e in events if e["type"] == "error"]
    result = events[-1]
    assert result["type"] == "result"
    _assert_unavailable(result)
    assert _left(client, auth) == 2


def test_a_google_budget_stop_before_planning_costs_nothing_and_says_when_it_reopens(client, gw, monkeypatch):
    p = _use()
    calls = []
    orig = p.places.attractions
    p.places.attractions = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    monkeypatch.setattr(capacity, "_live", lambda providers: True)  # treat the sim as the live Google stack
    get_settings().google_daily_usd = 0.01
    budgets.reserve("google", 0.01)  # today's budget is spent
    auth, tid = _register_plan(client)
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200
    body = r.json()
    _assert_unavailable(body)
    assert body["retry_at"].endswith("T00:00:00+05:30")  # the user's tomorrow: midnight in Asia/Kolkata
    assert not calls and _left(client, auth) == 2
    s = client.post(f"/api/trips/{tid}/plan?stream=true", headers=auth)
    assert json.loads(s.text.splitlines()[-1])["state"] == "unavailable"


def test_a_google_budget_stop_in_the_middle_of_a_plan_refunds_the_plan(client, gw):
    from app.gateway.errors import BudgetExhausted as Stop
    _use(places_down=Stop("Today's google budget is used up.", provider="google", retry_at=budgets.reset_at()))
    auth, tid = _register_plan(client)
    body = client.post(f"/api/trips/{tid}/plan", headers=auth).json()
    _assert_unavailable(body)
    assert body["retry_at"].endswith("T00:00:00+05:30") and _left(client, auth) == 2


def test_the_daily_plan_cap_gives_the_polite_message_before_google_is_cut_off(client, gw, monkeypatch):
    _use()
    monkeypatch.setattr(capacity, "_live", lambda providers: True)
    get_settings().daily_plan_cap = 2
    get_settings().google_daily_usd = 10
    results = []
    for _ in range(3):
        auth, tid = _register_plan(client)
        r = client.post(f"/api/trips/{tid}/plan", headers=auth)
        results.append((r.status_code, r.json().get("status"), _left(client, auth), r.json()))
    assert [(c, s) for c, s, *_ in results] == [(200, "ready"), (200, "ready"), (200, "unavailable")]
    _assert_unavailable(results[2][3], reason="daily_cap")
    assert results[2][3]["retry_at"].endswith("T00:00:00+05:30") and results[2][2] == 2  # the free plan is untouched
    assert budgets.used("google") < budgets.limit("google")  # Google was never cut off


def test_a_replan_during_an_outage_is_also_a_normal_answer(client, gw):
    _use()
    auth, tid = _register_plan(client)
    assert client.post(f"/api/trips/{tid}/plan", headers=auth).json()["status"] == "ready"
    _use(places_down=ProviderUnavailable("down"))
    r = client.post(f"/api/trips/{tid}/replan", json={"message": "add one more stop"}, headers=auth)
    assert r.status_code == 200
    assert r.json().get("state") in (None, "unavailable") and r.status_code != 503


def test_a_plan_with_a_degraded_part_says_so_in_its_plan_notes_and_logs_the_stage(client, gw, caplog):
    caplog.set_level(logging.WARNING, logger="tripai")

    def late(*a, **k):
        C.note_degraded("google", "POST /directions/v2:computeRoutes", "deadline", "google-routes")
        raise DeadlineExceeded("the plan ran out of time", provider="google-routes")
    p = _use()
    p.maps.compute_route = late
    auth, tid = _register_plan(client)
    body = client.post(f"/api/trips/{tid}/plan", headers=auth).json()
    assert body["status"] == "ready"
    notes = body["trip"]["itinerary"]["warnings"]
    assert any("took too long" in n and "travel times between stops" in n for n in notes), notes
    assert any("met its deadline during stage" in r.message for r in caplog.records)


def test_with_the_gateway_off_a_places_outage_is_still_a_normal_answer(client, gw):
    get_settings().gateway_enabled = False
    _use(places_down=ProviderUnavailable("down"))
    auth, tid = _register_plan(client)
    r = client.post(f"/api/trips/{tid}/plan", headers=auth)
    assert r.status_code == 200 and r.json()["state"] == "unavailable"  # I3 holds with or without the gateway


# --- no duplicate calls, flags --------------------------------------------------------------------------------------------

def test_a_paid_call_is_never_sent_twice_when_the_budget_or_breaker_says_no(gw):
    get_settings().google_daily_usd = 0.032
    gw.handler = lambda r: resp(503)
    with pytest.raises(ProviderUnavailable):
        request("POST", SEARCH, provider="Google Places", json={})  # a 5xx is refunded, so one retry is allowed
    assert gw.n() <= 2
    n = gw.n()
    budgets.reserve("google", 0.032)  # the whole day's budget is spent
    with pytest.raises(BudgetExhausted):
        request("POST", SEARCH, provider="Google Places", json={})
    assert gw.n() == n  # no budget, no call


def test_the_gateway_flag_off_is_the_plain_behaviour(gw):
    s = get_settings()
    s.gateway_enabled = False
    gw.handler = lambda r: resp(500)
    for _ in range(8):
        with pytest.raises(ProviderUnavailable):
            http.request("GET", RAIL, provider="RailRadar", retries=0)
    assert gw.n() == 8  # no breaker
    s.google_daily_usd = 0
    s.budget_mode = "enforce"
    gw.handler = lambda r: resp(200, json={})
    assert http.request("POST", SEARCH, provider="Google Places", json={}).status_code == 200  # no budget


def test_breaker_flag_off_never_opens(gw):
    get_settings().gateway_breakers = False
    gw.handler = lambda r: resp(500)
    for _ in range(12):
        with pytest.raises(ProviderUnavailable) as e:
            request("GET", RAIL, provider="RailRadar", retries=0)
        assert not isinstance(e.value, CircuitOpen)


def test_single_flight_counts_duplicates_off_and_shares_one_call_on(gw):
    def burst():
        gw.calls.clear()
        gw.handler = lambda r: (threading.Event().wait(0.15), resp(200, json={"ok": 1}))[1]
        with ContextThreadPool(max_workers=5) as pool:
            return [f.result().status_code for f in [pool.submit(request, "GET", RAIL, provider="RailRadar") for _ in range(5)]]
    ledger.reset()
    assert burst() == [200] * 5
    assert gw.n() == 5  # off: every call is made...
    row = next(r for r in ledger.snapshot()["endpoints"] if r["provider"] == "railradar")
    assert row["coalescible"] >= 1  # ...and the duplicates are counted: this is the evidence for turning it on
    get_settings().gateway_single_flight = True
    assert burst() == [200] * 5
    assert gw.n() <= 2  # on: one answer shared (a straggler may arrive after the leader finished)


def test_health_routing_is_off_by_default_and_orders_by_score_when_on(gw):
    class S:
        def __init__(self, name):
            self.name = name
    a, b = S("railradar"), S("irctc1")
    for _ in range(20):
        ledger.outcome("railradar", False, 4000)
        ledger.outcome("irctc1", True, 200)
    assert routing.health_score("irctc1") > routing.health_score("railradar")
    assert [p.name for p in routing.order([a, b])] == ["railradar", "irctc1"]  # unchanged: the flag is off
    get_settings().gateway_health_routing = True
    assert [p.name for p in routing.order([a, b])] == ["irctc1", "railradar"]
    assert routing.health_score("never-seen") == 1.0


# --- the admin gate ----------------------------------------------------------------------------------------------------------

def test_system_views_are_for_admins_only_and_everyone_else_gets_a_404(client, gw):
    auth = register(client)
    me = client.get("/api/auth/me", headers=auth).json()["user"]["email"]
    s = get_settings()
    for path in ("/api/system/rail-stats", "/api/system/budgets"):
        s.admin_emails = ""
        assert client.get(path, headers=auth).status_code == 404  # nobody is admin until configured
        assert client.get(path).status_code == 404  # signed out: also 404, not 401
        s.admin_emails = "someone.else@example.com"
        assert client.get(path, headers=auth).status_code == 404
        assert client.get(path, headers={"Authorization": "Bearer garbage"}).status_code == 404
        s.admin_emails = f"x@y.com, {me.upper()}"  # case-insensitive, comma list
        assert client.get(path, headers=auth).status_code == 200, path


def test_the_budgets_view_shows_use_limits_breakers_and_the_cap(client, gw):
    auth = register(client)
    get_settings().admin_emails = client.get("/api/auth/me", headers=auth).json()["user"]["email"]
    request("POST", SEARCH, provider="Google Places", json={})
    breakers.set_quota("irctc1")
    v = client.get("/api/system/budgets", headers=auth).json()
    g = next(b for b in v["budgets"] if b["group"] == "google")
    assert g["used"] == pytest.approx(0.032) and g["limit"] == 100 and v["timezone"] == "Asia/Kolkata"
    assert v["resets_at"].endswith("T00:00:00+05:30")
    assert any(b["provider"] == "irctc1" and b["state"] == "quota" for b in v["breakers"])
    assert v["plan_cap"]["google_pays_for"] == 285 or v["plan_cap"]["cap"] == 1000  # numbers come from the settings under test
    assert any(e["provider"] == "google-places" for e in v["ledger"]["endpoints"])
    assert "single_flight" in v["flags"] and v["flags"]["plan_deadline_seconds"] == get_settings().plan_deadline_seconds


# --- migration -----------------------------------------------------------------------------------------------------------------

def test_migration_0008_upgrades_and_downgrades_on_sqlite():
    from tests.test_rail_storage import _alembic
    path = os.path.join(tempfile.mkdtemp(prefix="tripai-mig3-"), "m.db")
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0007")
    _alembic(url, "upgrade", "head")
    db = sqlite3.connect(path)
    assert "provider_circuit" in {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    db.close()
    _alembic(url, "downgrade", "0007")
    db = sqlite3.connect(path)
    assert "provider_circuit" not in {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    db.close()
    _alembic(url, "upgrade", "head")
