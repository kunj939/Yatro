"""I6 for saved trips, share pages and shared text (ADR 0005): Google content is never shown once it is older than 30 days;
it is re-fetched from the place_id when a trip is opened (within caps); what can't be re-fetched shows as "Details
unavailable — open in Google Maps"; our own data is never touched. No external call is made: places are simulated as
Google's, and the refresh is a stub."""
import json
import uuid as _uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select, update

from app.api.deps import providers_dep
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.counters import DailyCounter
from app.models.trip import Alternative, Itinerary, ItineraryItem
from app.providers.types import PlaceData
from app.services import google_content as GC
from app.services import retention
from tests.conftest import register, trip_payload
from tests.sim_providers import sim_providers

HOURS = {"weekly": {str(d): [["09:00", "17:00"]] for d in range(7)}, "source": "google"}


class Stub:
    """Sim providers whose places are Google's, with a refresh that can answer, fail, or be slow."""

    def __init__(self):
        self.p = sim_providers("normal")
        self.calls: list[str] = []
        self.fail: set[str] = set()
        for obj, names in ((self.p.places, ("attractions", "restaurants", "resolve", "attraction", "find_attraction")),
                           (self.p.hotels, ("hotels",))):
            for n in names:
                setattr(obj, n, self._google(getattr(obj, n)))
        self.p.places.refresh_place = self.refresh

    @staticmethod
    def _google(fn):
        def inner(*a, **k):
            r = fn(*a, **k)
            return [replace(x, provider="google") for x in r] if isinstance(r, list) else (replace(r, provider="google") if r else r)
        return inner

    def refresh(self, pid):
        self.calls.append(pid)
        if pid in self.fail:
            raise RuntimeError("Google is down")
        return PlaceData("google", pid, f"Fresh {pid[-4:]}", "attraction", 26.91, 75.81, address="1 New Road", rating=4.8,
                         rating_count=12345, opening_hours=HOURS)


@pytest.fixture
def stub():
    s = Stub()
    app.dependency_overrides[providers_dep] = lambda: s.p
    saved = (get_settings().google_refresh_daily_cap, get_settings().google_refresh_per_trip)
    yield s
    app.dependency_overrides.clear()
    get_settings().google_refresh_daily_cap, get_settings().google_refresh_per_trip = saved
    with SessionLocal() as db:  # the daily counter is shared by the whole test database
        for row in db.scalars(select(DailyCounter).where(DailyCounter.name == GC.COUNTER)):
            db.delete(row)
        db.commit()


def _planned(client):
    auth = register(client)
    trip = client.post("/api/trips", json=trip_payload(), headers=auth).json()
    r = client.post(f"/api/trips/{trip['id']}/plan", headers=auth)
    assert r.status_code == 200, r.text
    return auth, trip["id"]


def _items(client, auth, tid):
    t = client.get(f"/api/trips/{tid}", headers=auth).json()
    return [i for d in t["itinerary"]["days"] for i in d["items"]], t


def _age(tid, days):
    """Make the trip's Google content `days` old."""
    tid = _uuid.UUID(tid)
    with SessionLocal() as db:
        ids = db.scalars(select(Itinerary.id).where(Itinerary.trip_id == tid)).all()
        stamp = datetime.now(UTC) - timedelta(days=days)
        db.execute(update(ItineraryItem).where(ItineraryItem.itinerary_id.in_(ids), ItineraryItem.google_fetched_at.is_not(None))
                   .values(google_fetched_at=stamp))
        item_ids = db.scalars(select(ItineraryItem.id).where(ItineraryItem.itinerary_id.in_(ids))).all()
        db.execute(update(Alternative).where(Alternative.itinerary_item_id.in_(item_ids), Alternative.google_fetched_at.is_not(None))
                   .values(google_fetched_at=stamp))
        db.execute(update(Itinerary).where(Itinerary.id.in_(ids)).values(created_at=stamp))
        db.commit()


def _off(tid):
    get_settings().google_refresh_daily_cap = 0


def _places(items):
    return [i for i in items if i["kind"] in ("activity", "meal") and i["place_id"]]


# --- planning stamps Google content -----------------------------------------------------------------------------------

def test_the_planner_stamps_google_content_and_only_that(client, stub):
    auth, tid = _planned(client)
    with SessionLocal() as db:
        rows = db.scalars(select(ItineraryItem).join(Itinerary).where(Itinerary.trip_id == _uuid.UUID(tid))).unique().all()
        by_kind = {k: [r.google_fetched_at is not None for r in rows if r.kind == k] for k in {r.kind for r in rows}}
    assert all(by_kind["activity"]) and not any(by_kind["transport"])
    assert any(r for r in by_kind if r == "meal")


def test_demo_data_is_never_gated_however_old(client):
    auth, tid = _planned(client)  # real (demo) providers: nothing is Google's
    _age(tid, 400)
    items, t = _items(client, auth, tid)
    assert all("google_expired" not in i["meta"] for i in items) and t["itinerary"]["summary"]


# --- the 30-day line ----------------------------------------------------------------------------------------------------

def test_content_is_shown_at_29_days_and_gated_at_31(client, stub):
    auth, tid = _planned(client)
    fresh, _ = _items(client, auth, tid)
    stop = _places(fresh)[0]
    _age(tid, 29)
    _off(tid)
    at29, _ = _items(client, auth, tid)
    assert next(i for i in at29 if i["id"] == stop["id"])["title"] == stop["title"]
    _age(tid, 31)
    at31, t = _items(client, auth, tid)
    gated = next(i for i in at31 if i["id"] == stop["id"])
    assert gated["title"] == GC.UNAVAILABLE and gated["meta"]["google_expired"] is True
    u = urlparse(gated["meta"]["maps_url"])
    q = parse_qs(u.query)
    assert u.netloc == "www.google.com" and q["query"] == [t["destination"]] and q["query_place_id"][0].startswith("sim:")


def test_a_gated_stop_keeps_our_data_and_loses_google_content(client, stub):
    auth, tid = _planned(client)
    before, _ = _items(client, auth, tid)
    stop = _places(before)[0]
    _age(tid, 31)
    _off(tid)
    after, _ = _items(client, auth, tid)
    g = next(i for i in after if i["id"] == stop["id"])
    for ours in ("start_at", "end_at", "time", "cost", "cost_state", "place_id", "position", "day_index", "kind", "duration_minutes"):
        assert g[ours] == stop[ours], ours
    assert g["polyline"] is None and g["explanation"] is None and g["opening_time"] is None and g["reasons"] == []
    assert not {"lat", "lng", "address", "rating", "rating_count"} & set(g["meta"])
    assert stop["title"] not in json.dumps(g) and (stop["meta"].get("address") or "\0") not in json.dumps(g)


def test_stays_and_free_time_keep_our_words_but_not_the_place_name(client, stub):
    auth, tid = _planned(client, ) if False else _planned(client)
    with SessionLocal() as db:
        it = db.scalar(select(Itinerary).where(Itinerary.trip_id == _uuid.UUID(tid)))
        db.add_all([
            ItineraryItem(itinerary_id=it.id, position=90, kind="stay", start_at=datetime(2026, 10, 17, 18), end_at=datetime(2026, 10, 17, 18),
                          title="Check in: Hotel Grand Secret", subtitle="2 nights, 1 room", google_fetched_at=datetime.now(UTC) - timedelta(days=40), meta={"lat": 1, "lng": 2}),
            ItineraryItem(itinerary_id=it.id, position=91, kind="free", start_at=datetime(2026, 10, 17, 19), end_at=datetime(2026, 10, 17, 20),
                          title="Free time near Secret Fort", subtitle="Unscheduled", google_fetched_at=datetime.now(UTC) - timedelta(days=40), meta={})])
        db.commit()
    _off(tid)
    items, _ = _items(client, auth, tid)
    stay = next(i for i in items if i["position"] == 90)
    free = next(i for i in items if i["position"] == 91)
    assert stay["title"] == "Check in: your stay" and stay["subtitle"] == "2 nights, 1 room" and "lat" not in stay["meta"]
    assert free["title"] == "Free time" and free["subtitle"] == "Unscheduled"
    assert "Secret" not in json.dumps([stay, free])


def test_the_narration_that_names_places_goes_with_them(client, stub):
    auth, tid = _planned(client)
    _, t = _items(client, auth, tid)
    assert t["itinerary"]["summary"] and not t["itinerary"]["summary_expired"]
    _age(tid, 31)
    _off(tid)
    _, t = _items(client, auth, tid)
    assert t["itinerary"]["summary"] is None and t["itinerary"]["tips"] == [] and t["itinerary"]["summary_expired"] is True


def test_expired_alternatives_are_gated_too(client, stub):
    auth, tid = _planned(client)
    items, _ = _items(client, auth, tid)
    with_alts = next(i for i in items if i["alternatives"] and i["kind"] in ("activity", "meal"))
    _age(tid, 31)
    _off(tid)
    r = client.get(f"/api/trips/{tid}/alternatives", params={"item_id": with_alts["id"]}, headers=auth).json()["items"][0]
    assert r["title"] == GC.UNAVAILABLE and with_alts["title"] not in json.dumps(r)  # the item's own title is gated here too
    alts = [a for a in r["alternatives"] if a["kind"] == "place"]
    assert alts and all(a["title"] == GC.UNAVAILABLE and a["reason"] is None and "lat" not in a["meta"] for a in alts)
    assert all(a["meta"].get("key") for a in alts)  # still swappable: the key is ours


# --- refresh when a trip is opened ----------------------------------------------------------------------------------------

def test_opening_an_old_trip_refreshes_it_from_the_place_id(client, stub):
    auth, tid = _planned(client)
    before, _ = _items(client, auth, tid)
    stop = _places(before)[0]
    _age(tid, 31)
    after, t = _items(client, auth, tid)
    fresh = next(i for i in after if i["id"] == stop["id"])
    assert fresh["title"].startswith("Fresh ") and fresh["meta"]["rating"] == 4.8 and fresh["meta"]["address"] == "1 New Road"
    assert fresh["opening_time"] == "09:00" and "google_expired" not in fresh["meta"]
    assert any("4.8" in r["text"] for r in fresh["reasons"])
    assert (fresh["start_at"], fresh["cost"], fresh["place_id"]) == (stop["start_at"], stop["cost"], stop["place_id"])
    assert stub.calls and len(stub.calls) == len(set(stub.calls))  # one call per distinct place
    with SessionLocal() as db:
        row = db.get(ItineraryItem, _uuid.UUID(fresh["id"]))
        assert (datetime.now(UTC) - GC._aware(row.google_fetched_at)) < timedelta(minutes=1)
    n = len(stub.calls)
    _items(client, auth, tid)
    assert len(stub.calls) == n  # fresh again: no second charge


def test_a_failed_refresh_leaves_the_stop_unavailable_and_the_trip_working(client, stub):
    auth, tid = _planned(client)
    stops = _places(_items(client, auth, tid)[0])
    _age(tid, 31)
    stub.fail = {f"sim:{s['meta'].get('key', '')}" for s in stops}
    with SessionLocal() as db:
        stub.fail = {r.provider_place_id for r in db.scalars(select(ItineraryItem).where(ItineraryItem.id == _uuid.UUID(stops[0]["id"]))).unique()
                     for r in [r.place]}
    items, _ = _items(client, auth, tid)  # still 200
    first = next(i for i in items if i["id"] == stops[0]["id"])
    assert first["title"] == GC.UNAVAILABLE and first["meta"]["google_expired"] is True
    assert any(i["title"].startswith("Fresh ") for i in items if i["id"] != first["id"])  # the others refreshed


def test_the_per_trip_and_daily_caps_bound_the_refresh(client, stub):
    auth, tid = _planned(client)
    _age(tid, 31)
    get_settings().google_refresh_per_trip = 2
    items, _ = _items(client, auth, tid)
    assert len(stub.calls) == 2
    assert sum(1 for i in items if i["meta"].get("google_expired") and i["place_id"]) > 0  # the rest: unavailable, not shown stale
    auth2, tid2 = _planned(client)
    stub.calls.clear()
    get_settings().google_refresh_per_trip = 20
    with SessionLocal() as db:
        done = GC.refreshed_today(db)
    get_settings().google_refresh_daily_cap = done + 1
    _age(tid2, 31)
    _items(client, auth2, tid2)
    assert len(stub.calls) == 1  # one place left in today's budget
    _items(client, auth2, tid2)
    assert len(stub.calls) == 1  # none left: the counter is recorded before the calls, so it can't be exceeded


def test_a_cap_of_zero_switches_the_refresh_off(client, stub):
    auth, tid = _planned(client)
    _age(tid, 31)
    get_settings().google_refresh_daily_cap = 0
    items, _ = _items(client, auth, tid)
    assert not stub.calls and any(i["title"] == GC.UNAVAILABLE for i in items)


def test_a_slow_refresh_cannot_hold_the_page_up(client, stub):
    import time
    auth, tid = _planned(client)
    _age(tid, 31)
    get_settings().google_refresh_budget_seconds = 0.2
    stub.p.places.refresh_place = lambda pid: (time.sleep(2), None)[1]
    t0 = time.perf_counter()
    items, _ = _items(client, auth, tid)
    get_settings().google_refresh_budget_seconds = 3.0
    assert time.perf_counter() - t0 < 1.5 and any(i["title"] == GC.UNAVAILABLE for i in items)


# --- at rest ------------------------------------------------------------------------------------------------------------

def test_the_scrub_clears_google_fields_but_not_ours(client, stub):
    auth, tid = _planned(client)
    before, _ = _items(client, auth, tid)
    stop = _places(before)[0]
    _age(tid, 31)
    with SessionLocal() as db:
        assert retention.run_retention()["saved_trip_rows_scrubbed"] > 0
        row = db.get(ItineraryItem, _uuid.UUID(stop["id"]))
        db.refresh(row)
        assert row.title == "" and row.polyline is None and row.explanation is None and not (row.meta or {}).get("lat")
        assert (row.place_id, float(row.cost), row.start_at) == (stop["place_id"] and row.place_id, stop["cost"], row.start_at)
        assert str(row.place_id) == stop["place_id"] and row.place.provider_place_id.startswith("sim:")  # the place_id is kept
    assert retention.run_retention()["saved_trip_rows_scrubbed"] == 0  # idempotent
    get_settings().google_refresh_daily_cap = 200
    after, _ = _items(client, auth, tid)  # opening it fills the stop back in
    assert next(i for i in after if i["id"] == stop["id"])["title"].startswith("Fresh ")


def test_fresh_content_is_never_scrubbed(client, stub):
    auth, tid = _planned(client)
    before, _ = _items(client, auth, tid)
    _age(tid, 29)
    retention.run_retention()
    after, _ = _items(client, auth, tid)
    assert [i["title"] for i in after] == [i["title"] for i in before]


# --- share pages and text -------------------------------------------------------------------------------------------------

def _share(client, auth, tid, **body):
    r = client.post(f"/api/trips/{tid}/share", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json(), r.json()["url"].rsplit("/", 1)[1]


def test_share_page_and_text_refresh_before_they_render(client, stub):
    auth, tid = _planned(client)
    _age(tid, 31)
    j, token = _share(client, auth, tid)
    assert stub.calls  # creating the link refreshed the trip first
    view = client.get(f"/api/share/{token}").json()
    names = [i["title"] for d in view["itinerary"]["days"] for i in d["items"]]
    assert any(n.startswith("Fresh ") for n in names) and GC.UNAVAILABLE not in names
    assert any(n in j["text"] for n in names if n.startswith("Fresh "))
    assert view["google"]["attribution"] == "Place details from Google Maps" and "Place details from Google Maps" in j["text"]


def test_share_page_and_text_never_show_expired_content(client, stub):
    auth, tid = _planned(client)
    before, _ = _items(client, auth, tid)
    names = [i["title"] for i in _places(before)]
    j, token = _share(client, auth, tid)
    _age(tid, 31)
    get_settings().google_refresh_daily_cap = 0  # the refresh can't run (budget exhausted)
    view = client.get(f"/api/share/{token}")
    blob = json.dumps(view.json())
    assert not any(n in blob for n in names)
    items = [i for d in view.json()["itinerary"]["days"] for i in d["items"]]
    gated = [i for i in items if i["meta"].get("google_expired")]
    assert gated and all(i["meta"]["maps_url"].startswith("https://www.google.com/maps/search/") for i in gated if i["kind"] in ("activity", "meal"))
    again = client.post(f"/api/trips/{tid}/share-text", json={"token": token}, headers=auth).json()["text"]
    assert not any(n in again for n in names) and GC.UNAVAILABLE in again
    assert view.json()["itinerary"]["summary"] is None
    valid = view.json()["google"]["valid_until"]
    assert valid and datetime.fromisoformat(valid) < datetime.now(UTC)  # said so: the page knows its content is out of date


def test_valid_until_is_30_days_after_the_oldest_content(client, stub):
    auth, tid = _planned(client)
    _age(tid, 10)
    _, token = _share(client, auth, tid)
    until = datetime.fromisoformat(client.get(f"/api/share/{token}").json()["google"]["valid_until"])
    assert abs((until - (datetime.now(UTC) + timedelta(days=20))).total_seconds()) < 120


def test_a_demo_trip_has_no_attribution_line(client):
    auth, tid = _planned(client)
    j, token = _share(client, auth, tid)
    assert client.get(f"/api/share/{token}").json()["google"]["attribution"] is None and "Google" not in j["text"]


# --- schema ---------------------------------------------------------------------------------------------------------------

def test_migrations_0006_and_0007_upgrade_backfill_and_downgrade():
    import os
    import sqlite3
    import tempfile
    import uuid

    from tests.test_rail_storage import _alembic
    path = os.path.join(tempfile.mkdtemp(prefix="tripai-mig2-"), "m.db")
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0005")
    db = sqlite3.connect(path)
    now = "2026-09-01 00:00:00.000000"
    gid, did = uuid.uuid4().hex, uuid.uuid4().hex
    for pid, prov in ((gid, "google"), (did, "demo")):
        db.execute("insert into places (id, provider, provider_place_id, name, category, lat, lng, types, tags, meta, created_at, updated_at) "
                   "values (?, ?, ?, 'P', 'attraction', 1, 2, '[]', '[]', '{}', ?, ?)", (pid, prov, pid, now, now))
    uid, tid, iid = (uuid.uuid4().hex for _ in range(3))
    db.execute("insert into users (id, email, name, password_hash, is_active, free_plans_used, created_at, updated_at) values (?, 'm@x.com', 'M', 'x', 1, 0, ?, ?)", (uid, now, now))
    db.execute("insert into trips (id, owner_id, title, status, origin_name, destination_name, start_date, end_date, start_time, end_time, "
               "timezone, budget, currency, travellers, data_mode, quota_consumed, plan_count, replan_count, created_at, updated_at) "
               "values (?, ?, 'T', 'ready', 'A', 'B', '2026-09-05', '2026-09-05', '06:00', '22:00', 'Asia/Kolkata', 5000, 'INR', 2, 'live', 0, 1, 0, ?, ?)", (tid, uid, now, now))
    db.execute("insert into itinerary (id, trip_id, version, is_current, title, tips, total_cost, remaining, budget_breakdown, warnings, pipeline_trace, candidate_pool, llm_status, created_at) "
               "values (?, ?, 1, 1, 'I', '[]', 0, 0, '{}', '[]', '[]', '{}', 'template', ?)", (iid, tid, now))
    rows = {}
    for kind, pid in (("activity", gid), ("activity", did), ("transport", None)):
        rid = uuid.uuid4().hex
        rows[(kind, pid)] = rid
        db.execute("insert into itinerary_items (id, itinerary_id, position, day_index, kind, start_at, end_at, title, place_id, created_at, "
                   "travel_cost, cost, sources, reasons, meta) values (?, ?, 0, 0, ?, ?, ?, 'x', ?, ?, 0, 0, '[]', '[]', '{}')", (rid, iid, kind, now, now, pid, now))
    db.commit()
    db.close()

    _alembic(url, "upgrade", "head")
    db = sqlite3.connect(path)
    stamped = {k: db.execute("select google_fetched_at from itinerary_items where id=?", (v,)).fetchone()[0] for k, v in rows.items()}
    assert stamped[("activity", gid)] == now and stamped[("activity", did)] is None and stamped[("transport", None)] is None
    assert {"daily_counters", "share_links"} <= {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert "upi_id" in {r[1] for r in db.execute("pragma table_info(users)")}
    db.close()

    _alembic(url, "downgrade", "0005")
    db = sqlite3.connect(path)
    assert "share_links" not in {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert "google_fetched_at" not in {r[1] for r in db.execute("pragma table_info(itinerary_items)")}
    db.close()
    _alembic(url, "upgrade", "head")
