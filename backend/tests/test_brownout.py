"""Brownout levels (ADR 0008): each does exactly its one thing, is labelled where it shows, and never touches a fact."""
from __future__ import annotations

import re
import threading
from datetime import timedelta

import pytest

from app.api.deps import providers_dep
from app.api.routes.demo import demo_date
from app.core import brownout
from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.main import app
from app.services import llm_service
from app.services import planner as planner_mod
from tests.conftest import register, trip_payload
from tests.sim_providers import sim_providers

L1, L2, L3, L4 = (brownout.LABELS[i] for i in (1, 2, 3, 4))


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.delenv("BROWNOUT_LEVEL", raising=False)
    monkeypatch.setattr(get_settings(), "brownout_level", 0)
    yield
    app.dependency_overrides.clear()


def _plan(client, level=None, monkeypatch=None, **over):
    if level is not None:
        monkeypatch.setenv("BROWNOUT_LEVEL", str(level))
    p = sim_providers("normal")
    app.dependency_overrides[providers_dep] = lambda: p
    auth = register(client)
    body = trip_payload(**over) if over else trip_payload(stay="no_stay", transport_modes=["train", "cab"])
    tid = client.post("/api/trips", json=body, headers=auth).json()["id"]
    out = client.post(f"/api/trips/{tid}/plan", headers=auth).json()
    assert out["status"] == "ready", out
    return out["trip"]["itinerary"], p


# --- the strings the user sees ---------------------------------------------------------------------------------------------

def test_the_labels_are_the_agreed_plain_words():
    assert L1 == "Trip description simplified to keep things fast"
    assert L2 == "Alternatives aren't available right now"
    assert L3 == "Fewer options were compared to keep things fast"
    assert L4 == "Train times are from saved timetables only right now"


def test_a_label_can_never_carry_a_fact():
    for text in brownout.LABELS.values():  # no money, no clock time, no number a train could have (I1)
        assert not re.search(r"\d|₹|rs\.?", text, re.I)


# --- reading the level --------------------------------------------------------------------------------------------------------

def test_the_level_is_read_live_from_the_environment_and_clamped(monkeypatch):
    assert brownout.level() == 0
    monkeypatch.setenv("BROWNOUT_LEVEL", "2")
    assert brownout.level() == 2 and brownout.active(2) and not brownout.active(3)
    monkeypatch.setenv("BROWNOUT_LEVEL", "9")
    assert brownout.level() == 4
    monkeypatch.setenv("BROWNOUT_LEVEL", "-3")
    assert brownout.level() == 0
    monkeypatch.setenv("BROWNOUT_LEVEL", "nonsense")
    assert brownout.level() == 0  # a typo is not a brownout
    monkeypatch.setenv("BROWNOUT_LEVEL", "")
    monkeypatch.setattr(get_settings(), "brownout_level", 3)
    assert brownout.level() == 3  # falls back to the setting


def test_one_plan_sees_one_level_even_if_the_operator_changes_it_midway(monkeypatch):
    monkeypatch.setenv("BROWNOUT_LEVEL", "1")
    with brownout.plan_scope() as lv:
        monkeypatch.setenv("BROWNOUT_LEVEL", "4")
        assert lv == 1 and brownout.level() == 1 and brownout.labels() == {"description": L1}
    assert brownout.level() == 4  # the next plan sees the new level


def test_the_automatic_suggestion_is_a_pure_function_of_queue_numbers():
    assert brownout.suggest(None, 0, 40, 100) == 0
    assert brownout.suggest(50, 0, 40, 100) == 1
    assert brownout.suggest(10, 40, 40, 100) == 2 and brownout.suggest(90, 40, 40, 100) == 3
    assert brownout.suggest(10, 100, 40, 100) == 4


def test_the_automatic_level_is_never_applied(monkeypatch):
    s = get_settings()
    assert s.brownout_auto is False
    monkeypatch.setenv("BROWNOUT_LEVEL", "0")
    from app.services.plan_runner import runner
    assert runner.stats()["brownout_level"] == 0  # whatever it suggests, the level in force is the operator's


# --- plans at each level ---------------------------------------------------------------------------------------------------------

def test_level_0_is_exactly_today(client):
    it, _ = _plan(client)
    assert it["brownout"] is None
    assert not [w for w in it["warnings"] if w in brownout.LABELS.values()]
    assert any(i["alternatives"] for d in it["days"] for i in d["items"])


def test_l1_uses_the_template_text_and_says_so_beside_the_description(client, monkeypatch):
    def no_ai(*a, **k):
        raise AssertionError("the AI was called at level 1")
    monkeypatch.setattr(llm_service, "narrate", no_ai)
    it, _ = _plan(client, 1, monkeypatch)
    assert it["brownout"] == {"level": 1, "labels": {"description": L1}}
    assert it["llm_status"] == "template" and it["summary"]
    assert L1 in it["warnings"]  # and in Plan notes


def test_l2_skips_alternatives_and_says_so_in_that_section(client, monkeypatch):
    it, _ = _plan(client, 2, monkeypatch)
    assert it["brownout"]["labels"]["alternatives"] == L2 and it["brownout"]["level"] == 2
    assert not any(i["alternatives"] for d in it["days"] for i in d["items"])
    assert {L1, L2} <= set(it["warnings"])  # levels are applied in order: 2 includes 1


def test_l3_compares_fewer_options(client, monkeypatch):
    seen: list[int] = []
    real = planner_mod.PS.upsert_places

    def spy(db, cands):
        seen.append(len(cands))
        return real(db, cands)
    monkeypatch.setattr(planner_mod.PS, "upsert_places", spy)
    _plan(client)
    full = seen[-1]
    it, _ = _plan(client, 3, monkeypatch)
    assert seen[-1] < full  # fewer sights and restaurants went into the comparison
    assert it["brownout"]["labels"]["options"] == L3 and L3 in it["warnings"]
    assert it["days"] and any(i["kind"] == "activity" for d in it["days"] for i in d["items"])  # still a real plan


def test_l3_keeps_pinned_places_and_the_best_ones():
    class C:
        def __init__(self, pinned): self.pinned = pinned  # noqa: E701
    cands = [C(False), C(True), C(False), C(False), C(False), C(True), C(False)]
    out = planner_mod._smaller(cands, 2)
    assert [c.pinned for c in out].count(True) == 2  # a place the traveller pinned is never dropped
    assert out == [c for c in cands if c in out]  # order (best first) is kept
    assert len(out) == 2 + 3  # half of the five others, rounded up


def test_l4_stops_live_rail_lookups_and_labels_the_rail_section(client, monkeypatch):
    it, _ = _plan(client, 4, monkeypatch)
    assert it["brownout"]["labels"]["rail"] == L4 and it["brownout"]["level"] == 4
    trains = [i for d in it["days"] for i in d["items"] if i["kind"] == "transport" and i["meta"].get("mode") == "train"]
    assert trains  # the sample plan has a train, so this is not an empty check
    for t in trains:
        assert t["meta"]["brownout"] == L4  # the rail section itself says it
    assert L4 in it["warnings"]


def test_l4_refuses_the_live_call_before_any_network_use(monkeypatch):
    import httpx

    from app.providers.railradar import RailRadarProvider
    sent: list = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"success": True, "data": {"trains": []}})
    from app.providers import http
    monkeypatch.setattr(http, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("BROWNOUT_LEVEL", "4")
    rr = RailRadarProvider(None, "k", store=False)
    with pytest.raises(ProviderUnavailable):
        rr._get("/trains/between/AAA/BBB", {"date": "2026-10-20"}, timedelta(hours=1), use_cache=False)
    assert sent == []  # nothing went out
    monkeypatch.setenv("BROWNOUT_LEVEL", "0")
    assert rr._get("/trains/between/AAA/BBB", {"date": "2026-10-20"}, timedelta(hours=1), use_cache=False)
    assert len(sent) == 1


def test_no_level_changes_a_train_fact_a_fare_or_a_name(client, monkeypatch):
    def transport(it):
        return [(i["title"], i["time"], i["end_time"], i["cost"], i["cost_state"], i["meta"].get("number"))
                for d in it["days"] for i in d["items"] if i["kind"] == "transport"]
    base, _ = _plan(client)
    assert transport(base)
    for lv in (1, 2, 3, 4):
        it, _ = _plan(client, lv, monkeypatch)
        assert transport(it) == transport(base), f"level {lv} changed a name, time, number or fare"  # same facts, less effort
    assert base["brownout"] is None  # and the unlabelled plan is untouched by the levels above


def test_the_level_applies_to_a_plan_started_after_the_change_not_before(client, monkeypatch):
    p = sim_providers("normal")
    gate, entered = threading.Event(), threading.Event()
    orig = p.places.resolve

    def slow(*a, **k):
        entered.set()
        gate.wait(30)
        return orig(*a, **k)
    p.places.resolve = slow
    app.dependency_overrides[providers_dep] = lambda: p
    auth = register(client)
    tid = client.post("/api/trips", json=trip_payload(), headers=auth).json()["id"]
    out: dict = {}

    def run():
        from fastapi.testclient import TestClient
        out["r"] = TestClient(app).post(f"/api/trips/{tid}/plan", headers=auth).json()
    t = threading.Thread(target=run)
    t.start()
    assert entered.wait(20)
    monkeypatch.setenv("BROWNOUT_LEVEL", "2")  # changed while the plan is running
    gate.set()
    t.join(60)
    assert out["r"]["trip"]["itinerary"]["brownout"] is None  # it started at level 0 and finished at level 0


def test_demo_date_helper_still_importable():
    assert demo_date()
