"""Phase 1 (ADR 0002): provenance as a type, the final serializer gate, the source chain, the AI guard,
I6 retention and the two Phase 0 fixes (no write lock held during a plan; first progress event first)."""
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.core import provenance as P
from app.core import telemetry as T
from app.db.session import SessionLocal
from app.models.catalog import ApiCache, Place
from app.models.rail import RailTimetable
from app.providers.rail_sources import rail_text_ok, validated_stations
from app.providers.types import TransportData
from app.services import llm_service as L
from app.services import retention
from app.services.train_fact_gate import redact_alternative, redact_item
from tests.conftest import register, trip_payload
from tests.test_named_trains import DEE_BACK, DEE_TRAINS, JAIPUR, NDLS_BACK, NDLS_TRAINS, NEW_DELHI, provider

NOW = datetime.now(UTC)


def _train(source=None, observed=None, name="Daurai - New Delhi Shatabdi Express", number="12016"):
    t = TransportData(direction="outbound", mode="train", name=name, depart_at=datetime(2026, 10, 10, 6, 10),
                      arrive_at=datetime(2026, 10, 10, 10, 40), duration_minutes=270, cost_per_person=Decimal(700),
                      meta={"number": number})
    if source:
        P.stamp(t, source, observed)
    return t


# --- the type --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("days, expected", [(0, "fresh"), (7, "fresh"), (8, "aging"), (21, "aging"), (22, "stale"),
                                            (45, "stale"), (46, "expired")])
def test_saved_timetable_freshness(days, expected):
    assert P.freshness(P.SourceKind.SAVED_TIMETABLE, NOW - timedelta(days=days, hours=1 if days else 0), NOW) == expected


@pytest.mark.parametrize("source", [P.SourceKind.LIVE_API, P.SourceKind.SAVED_TIMETABLE, P.SourceKind.OFFICIAL_DATASET])
def test_a_train_service_is_built_from_timetable_sources(source):
    s = P.TrainService.from_option(_train(source, NOW - timedelta(days=3)))
    assert s.number.value == "12016" and s.depart_at.source == source


@pytest.mark.parametrize("source", [P.SourceKind.STATION_GUIDANCE, P.SourceKind.AI_GUIDANCE, None])
def test_guidance_or_unknown_sources_can_never_make_a_train_service(source):
    with pytest.raises(P.ProvenanceError):
        P.TrainService.from_option(_train(source))


def test_a_saved_timetable_older_than_45_days_cannot_make_a_train_service():
    with pytest.raises(P.ProvenanceError):
        P.TrainService.from_option(_train(P.SourceKind.SAVED_TIMETABLE, NOW - timedelta(days=46)))


# --- the final gate (independent of the factory) -------------------------------------------------------------------

def _item(meta, title="Train: Daurai - New Delhi Shatabdi Express 12016 (CC)"):
    return {"kind": "transport", "start_at": "2026-10-10T06:10", "end_at": "2026-10-10T10:40", "time": "06:10",
            "end_time": "10:40", "title": title, "subtitle": "Jaipur Jn (JP) to New Delhi (NDLS), leaves 06:10",
            "explanation": "Take it at 06:10 to arrive by 10:40.", "reasons": [{"key": "time", "text": "fits 06:10 to 10:40"}],
            "meta": {"mode": "train", "number": "12016", "to_name": "New Delhi (NDLS)", **meta}}


@pytest.mark.parametrize("meta", [{"source_kind": "station_guidance"}, {"source_kind": "ai_guidance"}, {},
                                  {"source_kind": "saved_timetable", "observed_at": (NOW - timedelta(days=50)).isoformat()}])
def test_the_serializer_strips_train_facts_whatever_the_planner_wrote(meta):
    out = redact_item(_item(meta))
    text = " ".join(str(out.get(k)) for k in ("title", "subtitle", "explanation")) + str(out["reasons"])
    assert "Shatabdi" not in text and "12016" not in text
    assert out["time"] is None and out["end_time"] is None and out["start_at"] == "2026-10-10"
    assert not any(c in text for c in ("06:10", "10:40"))
    assert out["meta"]["number"] is None and out["meta"]["when"] == "Early morning"


def test_timetabled_trains_and_other_modes_pass_untouched():
    live = _item({"source_kind": "live_api", "observed_at": NOW.isoformat()})
    assert redact_item(live) == live
    cab = {**_item({}), "meta": {"mode": "cab"}}
    assert redact_item(cab) == cab


def test_alternatives_are_gated_too():
    alt = {"kind": "transport", "title": "Train: Shatabdi 12016", "reason": "departs 06:10, arrives 10:40; same fare.",
           "meta": {"mode": "train", "depart_at": "2026-10-10T06:10:00", "number": "12016", "source_kind": "station_guidance"}}
    out = redact_alternative(alt)
    assert "12016" not in str(out) and "06:10" not in str(out) and "Shatabdi" not in out["title"]


# --- AI output guards ----------------------------------------------------------------------------------------------

def test_the_narration_guard_rejects_invented_train_numbers_and_names():
    allowed = L.allowed_facts({"items": [{"title": "Train to Agra Cantt", "time": "08:10"}]})
    assert L.violations_in("Hop on the 12049 Gatimaan Express.", allowed) == ["12049", "Gatimaan", "Express"]
    ok = L.allowed_facts({"items": [{"title": "Train: Gatimaan Express 12049", "time": "08:10"}]})
    assert L.violations_in("Hop on the Gatimaan Express 12049 at 08:10.", ok) == []


@pytest.mark.parametrize("text, ok", [("Head for Haridwar Jn, about 25 km away", True), ("Take train 12017", False),
                                      ("The 06:45 departure", False), ("Fares around ₹500", False),
                                      ("The Shatabdi is best", False), ("Leaves at 7 am", False)])
def test_ai_rail_text_guard(text, ok):
    assert rail_text_ok(text) is ok


def test_ai_suggested_stations_must_be_in_the_gazetteer():
    assert validated_stations(["hw", "XXXX", "NDLS", "HW", None]) == [("HW", "Haridwar Jn"), ("NDLS", "New Delhi")]


# --- sources are stamped where options are made --------------------------------------------------------------------

def test_railradar_options_say_live_or_saved():
    rr = provider({("JP", "NDLS"): NDLS_TRAINS, ("NDLS", "JP"): NDLS_BACK, ("JP", "DEE"): DEE_TRAINS, ("DEE", "JP"): DEE_BACK}, [])
    out = rr.intercity(JAIPUR, NEW_DELHI, date(2026, 10, 10), date(2026, 10, 10))
    assert {t.meta["source_kind"] for t in out} == {"live_api"}
    rr.cache.enabled = True  # now with a saved copy of one route, 10 days old
    with SessionLocal() as db:
        db.add(RailTimetable(source="railradar", from_code="JP", to_code="NDLS", weekday=date(2026, 10, 17).weekday(),
                             rows=NDLS_TRAINS, fetched_at=NOW - timedelta(days=10)))
        db.commit()
    saved = [t for t in rr.intercity(JAIPUR, NEW_DELHI, date(2026, 10, 17), date(2026, 10, 17))
             if t.direction == "outbound" and "(NDLS)" in t.to_name]
    assert saved and {t.meta["source_kind"] for t in saved} == {"saved_timetable"}
    assert {t.meta["freshness"] for t in saved} == {"aging"}


# --- I6 retention --------------------------------------------------------------------------------------------------

def test_google_place_content_is_cleared_after_30_days_but_the_id_is_kept():
    with SessionLocal() as db:
        old = Place(provider="google", provider_place_id="ChIJold", name="Old Fort", category="attraction",
                    lat=27.1, lng=78.0, rating=4.5, fetched_at=NOW - timedelta(days=31))
        new = Place(provider="google", provider_place_id="ChIJnew", name="New Cafe", category="restaurant",
                    lat=27.2, lng=78.1, fetched_at=NOW - timedelta(days=2))
        demo = Place(provider="demo", provider_place_id="demo:x", name="Sample", category="attraction",
                     lat=1.0, lng=1.0, fetched_at=NOW - timedelta(days=90))
        db.add_all([old, new, demo,
                    ApiCache(provider="google-routes", cache_key="expired-x", response={}, expires_at=NOW - timedelta(hours=1)),
                    ApiCache(provider="google-routes", cache_key="fresh-x", response={}, expires_at=NOW + timedelta(hours=1))])
        db.commit()
    out = retention.run_retention()
    assert out["google_places_scrubbed"] >= 1 and out["cache_rows_deleted"] >= 1
    with SessionLocal() as db:
        o = db.query(Place).filter_by(provider_place_id="ChIJold").one()
        assert (o.name, o.lat, o.lng, o.rating) == ("", 0.0, 0.0, None)
        assert db.query(Place).filter_by(provider_place_id="ChIJnew").one().name == "New Cafe"
        assert db.query(Place).filter_by(provider_place_id="demo:x").one().name == "Sample"
        assert db.query(ApiCache).filter_by(cache_key="expired-x").first() is None
        assert db.query(ApiCache).filter_by(cache_key="fresh-x").first() is not None


# --- end to end: the plan's own output, and the two Phase 0 fixes ----------------------------------------------------

def _use(rail_estimate=False, slow_resolve=0.0, cache_writes=False):
    from app.api.deps import providers_dep
    from app.main import app
    from app.providers import cache as C
    from tests.sim_providers import sim_providers
    p = sim_providers("normal", rail_estimate=rail_estimate)
    if slow_resolve:
        orig = p.places.resolve
        p.places.resolve = lambda *a, **k: (time.sleep(slow_resolve), orig(*a, **k))[1]
    if cache_writes:  # every route lookup writes the response cache from its own session, as Google's provider does
        orig_route = p.maps.compute_route
        p.maps.compute_route = lambda a, b, mode="drive", depart_at=None: C.cached(
            "lock-test", [a, b, mode, time.time()], timedelta(minutes=5), lambda: orig_route(a, b, mode, depart_at))
    app.dependency_overrides[providers_dep] = lambda: p
    return app


@pytest.fixture
def overrides():
    yield
    from app.main import app
    app.dependency_overrides.clear()


def _plan(client, **over):
    auth = register(client)
    trip = client.post("/api/trips", json=trip_payload(**over), headers=auth).json()
    r = client.post(f"/api/trips/{trip['id']}/plan", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()["trip"] if "trip" in r.json() else r.json()


def test_a_train_without_a_timetable_reaches_the_traveller_without_name_number_or_times(client, overrides):
    _use(rail_estimate=True)
    body = _plan(client, transport_modes=["train"])
    trains = [i for d in body["itinerary"]["days"] for i in d["items"] if i["kind"] == "transport" and i["meta"].get("mode") == "train"]
    assert trains, "the plan has a train"
    for i in trains:
        assert i["time"] is None and i["meta"]["source_kind"] == "station_guidance" and i["meta"]["when"]
        assert "timing estimated" in i["title"]
        import re
        assert not re.search(r"\b\d{1,2}:\d{2}\b", " ".join(str(i[k] or "") for k in ("title", "subtitle", "explanation")))


def test_timetabled_trains_keep_their_times(client, overrides):
    _use()
    body = _plan(client, transport_modes=["train"])
    trains = [i for d in body["itinerary"]["days"] for i in d["items"] if i["kind"] == "transport" and i["meta"].get("mode") == "train"]
    assert trains and all(i["time"] and i["meta"]["source_kind"] == "live_api" for i in trains)


def test_the_first_progress_event_comes_before_any_provider_call(client, overrides):
    _use(slow_resolve=0.6)
    _plan(client)
    assert T.RECENT[-1]["first_event_ms"] < 300


def test_the_planner_holds_no_write_lock_while_waiting_on_providers(client, overrides):
    # Before the fix each of these cache writes waited out SQLite's 30 s busy timeout.
    _use(cache_writes=True)
    t0 = time.perf_counter()
    _plan(client)
    assert time.perf_counter() - t0 < 20
