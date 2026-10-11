"""Two-tier rail storage (ADR 0003): validation before saving, freshness and expiry, replacement with a change log,
Tier 1 route knowledge, the storage allow-list (I6/I7), traveller reports, the refresh worker and migration 0005.

Every test uses its own made-up station codes (or its own source name), so they share the test database safely."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.core.provenance import SourceKind
from app.db.session import SessionLocal
from app.models.rail import RailCityRoute, RailPairResult, RailTimetable, RailTrainReport
from app.models.trip import Itinerary, ItineraryItem
from app.providers import rail_storage as RS
from app.providers.rail_common import NotAnswered, RailCache, remember_pair, search_station_pairs, tag_rows
from app.providers.railradar import RailRadarProvider
from app.providers.types import PlaceData
from tests.conftest import register, trip_payload

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)  # outside the Dec-Feb seasonal cap
WINTER = datetime(2027, 1, 15, 9, 0, tzinfo=UTC)


def row(number, dep, arr, frm, to, name="Test Express", duration=120, distance=150.0):
    """A RailRadar-shaped timetable row."""
    out = {"train": {"number": number, "name": name, "type": "Express"},
           "from": {"code": frm, "departure": dep}, "to": {"code": to, "arrival": arr}}
    if duration is not None:
        out["duration"] = duration
    if distance is not None:
        out["distance"] = distance
    return out


def good(frm, to, n=3):
    return [row(f"1{i:04d}", f"{8 + i:02d}:00", f"{10 + i:02d}:00", frm, to) for i in range(n)]


def _timetable(source, frm, to, weekday):
    with SessionLocal() as db:
        return db.scalar(select(RailTimetable).where(RailTimetable.source == source, RailTimetable.from_code == frm,
                                                     RailTimetable.to_code == to, RailTimetable.weekday == weekday))


@pytest.fixture
def allow(request):
    """Allow-list extra (made-up) sources for one test."""
    s = get_settings()
    before = s.rail_storage_sources

    def _allow(*names):
        s.rail_storage_sources = ",".join(["railradar", *names])
    yield _allow
    s.rail_storage_sources = before


# --- Validation ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad, reason", [
    (row("19999", "08:00", "10:00", "VA1", "VB1", name=""), "no_name"),
    (row("123", "08:00", "10:00", "VA1", "VB1"), "bad_number"),
    (row("TR999", "08:00", "10:00", "VA1", "VB1"), "bad_number"),
    (row("19999", "8 am", "10:00", "VA1", "VB1"), "bad_departure"),
    (row("19999", "08:00", None, "VA1", "VB1", duration=None), "bad_arrival"),
    (row("19999", "08:00", "10:00", "VA1", "XXX"), "wrong_station"),
    (row("19999", "08:00", "10:00", "VA1", "VB1", duration=-5), "bad_duration"),
    (row("19999", "08:00", "09:00", "VA1", "VB1", duration=60, distance=900.0), "implausible_speed"),
    (row("19999", "08:00", "20:00", "VA1", "VB1", duration=720, distance=30.0), "implausible_speed"),
    ("not a row", "not_a_row"),
])
def test_each_validation_rule_drops_the_bad_row_only(bad, reason):
    kept, report = RS.validate_rows([*good("VA1", "VB1"), bad], "VA1", "VB1")
    assert len(kept) == 3 and bad not in kept
    assert report["status"] == "ok" and report["dropped"] == {reason: 1}


def test_a_train_listed_twice_with_different_times_is_dropped_entirely():
    rows = [*good("VA1", "VB1"), row("17777", "06:00", "08:00", "VA1", "VB1"), row("17777", "07:00", "09:00", "VA1", "VB1")]
    kept, report = RS.validate_rows(rows, "VA1", "VB1")
    assert [r["train"]["number"] for r in kept] == ["10000", "10001", "10002"]
    assert report["dropped"] == {"conflicting_duplicate": 2}


def test_an_exact_duplicate_is_kept_once():
    rows = [*good("VA1", "VB1"), good("VA1", "VB1")[0]]
    kept, report = RS.validate_rows(rows, "VA1", "VB1")
    assert len(kept) == 3 and report["dropped"] == {}


def test_overnight_arrival_counts_as_the_next_day():
    r = row("12345", "22:00", "06:00", "VA1", "VB1", duration=None, distance=480.0)  # 8 h, 60 km/h
    assert RS.validate_rows([r], "VA1", "VB1")[0] == [r]


def test_when_most_rows_fail_nothing_is_kept():
    rows = [*good("VA1", "VB1", 1), row("1", "08:00", "10:00", "VA1", "VB1"), row("2", "08:00", "10:00", "VA1", "VB1")]
    kept, report = RS.validate_rows(rows, "VA1", "VB1")
    assert kept == [] and report["status"] == "rejected"
    assert RS.save_timetable("railradar", "VA2", "VB2", 0, rows, NOW)["status"] == "rejected"
    assert _timetable("railradar", "VA2", "VB2", 0) is None


def test_flat_irctc1_shaped_rows_validate_too():
    flat = {"train_name": "Flat Express", "train_number": "12001", "from_std": "06:00", "to_sta": "09:00",
            "from": "VA1", "to": "VB1", "duration": "3:00"}
    assert RS.validate_rows([flat], "VA1", "VB1")[0] == [flat]


# --- Freshness and expiry --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("days, expected", [(0, "fresh"), (7, "fresh"), (8, "aging"), (21, "aging"), (22, "stale"),
                                            (45, "stale"), (46, "expired")])
def test_freshness_bands(days, expected):
    assert RS.freshness_of(NOW - timedelta(days=days), NOW) == expected


def test_seasonal_cap_shortens_the_limit_to_30_days_in_winter():
    assert RS.freshness_of(WINTER - timedelta(days=30), WINTER) == "stale"
    assert RS.freshness_of(WINTER - timedelta(days=31), WINTER) == "expired"
    s = get_settings()
    s.rail_seasonal_cap = False
    try:
        assert RS.freshness_of(WINTER - timedelta(days=40), WINTER) == "stale"
    finally:
        s.rail_seasonal_cap = True


def test_an_expired_timetable_is_never_used():
    RS.save_timetable("railradar", "EX1", "EX2", 2, good("EX1", "EX2"), NOW - timedelta(days=46))
    assert RS.load_timetable("railradar", "EX1", "EX2", 2, NOW) is None
    assert RailCache("railradar").timetable_any_age("EX1", "EX2", 2) is None


def test_a_saved_copy_expires_at_45_days_or_30_in_winter():
    RS.save_timetable("railradar", "EX3", "EX4", 2, good("EX3", "EX4"), NOW)
    assert RS._aware(_timetable("railradar", "EX3", "EX4", 2).expires_at) == NOW + timedelta(days=45)
    RS.save_timetable("railradar", "EX5", "EX6", 2, good("EX5", "EX6"), WINTER)
    assert RS._aware(_timetable("railradar", "EX5", "EX6", 2).expires_at) == WINTER + timedelta(days=30)


@pytest.mark.parametrize("age, fresh, priority", [(3, "fresh", 0), (10, "aging", 1), (30, "stale", 2)])
def test_aging_and_stale_copies_are_served_and_queued_for_refresh(age, fresh, priority):
    frm, to = f"FR{age}", f"TO{age}"
    RS.save_timetable("railradar", frm, to, 1, good(frm, to), NOW - timedelta(days=age))
    rows, _, got = RS.load_timetable("railradar", frm, to, 1, NOW)
    assert len(rows) == 3 and got == fresh
    saved = _timetable("railradar", frm, to, 1)
    assert saved.needs_refresh is (priority > 0) and saved.refresh_priority == priority


def test_rows_saved_before_validation_existed_are_validated_when_read():
    with SessionLocal() as db:
        db.add(RailTimetable(source="railradar", from_code="LG1", to_code="LG2", weekday=4, fetched_at=NOW,
                             rows=[*good("LG1", "LG2", 2), row("9", "08:00", "10:00", "LG1", "LG2")],
                             validation={"status": "legacy"}))
        db.commit()
    rows, _, _ = RS.load_timetable("railradar", "LG1", "LG2", 4, NOW)
    assert [r["train"]["number"] for r in rows] == ["10000", "10001"]


def test_a_newer_timetable_replaces_the_old_one_and_logs_the_differences():
    RS.save_timetable("railradar", "RP1", "RP2", 3, [row("11111", "10:00", "12:00", "RP1", "RP2"),
                                                     row("22222", "12:00", "14:00", "RP1", "RP2")], NOW - timedelta(days=9))
    report = RS.save_timetable("railradar", "RP1", "RP2", 3, [row("11111", "10:30", "12:30", "RP1", "RP2"),
                                                              row("33333", "14:00", "16:00", "RP1", "RP2")], NOW)
    assert report["changes"] == {"added": ["33333"], "removed": ["22222"], "retimed": ["11111"]}
    saved = _timetable("railradar", "RP1", "RP2", 3)
    assert {r["train"]["number"] for r in saved.rows} == {"11111", "33333"}
    assert RS._aware(saved.fetched_at) == NOW and saved.needs_refresh is False


# --- The allow-list (I6, I7) -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["google", "irctc1"])
def test_google_and_irctc1_never_write_either_tier(source):
    assert RS.save_timetable(source, "NW1", "NW2", 0, good("NW1", "NW2"), NOW) == {"status": "not_allowed"}
    RS.record_pair_result(source, "NW1", "NW2", 0, "empty", now=NOW)
    RS.record_working_pairs(source, "nw-a", "nw-b", [("NW1", "NW2")], NOW)
    assert RailCache(source).enabled is False
    with SessionLocal() as db:
        assert db.scalar(select(RailTimetable).where(RailTimetable.source == source, RailTimetable.from_code == "NW1")) is None
        assert db.scalar(select(RailPairResult).where(RailPairResult.source == source, RailPairResult.from_code == "NW1")) is None
        assert db.scalar(select(RailCityRoute).where(RailCityRoute.source == source, RailCityRoute.origin_key == "nw-a")) is None


# --- Tier 1: route knowledge -----------------------------------------------------------------------------------------

def test_known_empty_lasts_60_days_and_is_per_weekday():
    RS.record_pair_result("railradar", "KE1", "KE2", 6, "empty", now=NOW)
    assert RS.known_empty("railradar", "KE1", "KE2", 6, NOW + timedelta(days=59))
    assert not RS.known_empty("railradar", "KE1", "KE2", 6, NOW + timedelta(days=61))
    assert not RS.known_empty("railradar", "KE1", "KE2", 5, NOW)  # another weekday is another timetable
    RS.record_pair_result("railradar", "KE1", "KE2", 6, "trains", ["12345"], now=NOW)
    assert not RS.known_empty("railradar", "KE1", "KE2", 6, NOW)


class NoPlaces:
    def resolve(self, *a):
        raise RuntimeError("offline")


TONE = PlaceData("google", "t1", "Tieroneville", "city", 23.10, 75.70)
TTWO = PlaceData("google", "t2", "Tiertwo Town", "city", 22.70, 75.90)  # ~50 km: no junction search


def _searcher(monkeypatch, answers):
    p = RailRadarProvider(NoPlaces(), "k", store=True)
    calls = []

    def trains(frm, to, day):
        calls.append((frm, to))
        got = answers.get((frm, to), [])
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(p, "trains", trains)
    return p, calls


def test_working_pairs_go_first_and_known_empty_pairs_are_skipped(monkeypatch):
    day = date(2026, 10, 7)
    o_codes, d_codes = [("TA1", "Tier A1"), ("TA2", "Tier A2")], [("TB1", "Tier B1"), ("TB2", "Tier B2")]
    answers = {("TA1", "TB1"): [], ("TA2", "TB1"): NotAnswered(),
               ("TA1", "TB2"): tag_rows([row("15151", "09:00", "11:00", "TA1", "TB2")], SourceKind.LIVE_API)}
    p, calls = _searcher(monkeypatch, answers)

    def search():
        return search_station_pairs(p, TONE, TTWO, day, day - timedelta(days=1), o_codes, d_codes, "test", 20)
    first, _ = search()
    assert calls == [("TA1", "TB1"), ("TA2", "TB1"), ("TA1", "TB2")]
    assert {t.meta["number"] for t in first} == {"15151"} and all(t.meta["rail_source"] == "railradar" for t in first)
    assert RS.working_pairs("railradar", "tieroneville", "tiertwo town") == [("TA1", "TB2")]
    assert RS.known_empty("railradar", "TA1", "TB1", day.weekday())
    with SessionLocal() as db:  # an error answer is never a negative
        assert db.scalar(select(RailPairResult).where(RailPairResult.from_code == "TA2")) is None

    calls.clear()
    again, _ = search()
    assert calls == [("TA1", "TB2")]  # straight to the pair that worked: one call instead of three
    assert {t.meta["number"] for t in again} == {"15151"}


def test_failures_and_unreadable_answers_never_become_negatives(monkeypatch):
    day = date(2026, 10, 8)
    o_codes, d_codes = [("NF1", "No Fail 1")], [("NF2", "No Fail 2")]
    p, calls = _searcher(monkeypatch, {("NF1", "NF2"): ProviderUnavailable("RailRadar quota used up")})
    with pytest.raises(ProviderUnavailable):
        search_station_pairs(p, TONE, TTWO, day, day - timedelta(days=1), o_codes, d_codes, "test", 20)
    remember_pair(p, "NF3", "NF4", day, NotAnswered(), [])  # the source answered with an error
    remember_pair(p, "NF5", "NF6", day, [{"train": {}}], [])  # rows came back but none could be read
    with SessionLocal() as db:
        assert db.scalars(select(RailPairResult).where(RailPairResult.from_code.in_(["NF1", "NF3", "NF5"]))).all() == []
    assert not RS.known_empty("railradar", "NF1", "NF2", day.weekday())


def test_tier1_is_off_when_the_provider_does_not_store(monkeypatch):
    day = date(2026, 10, 9)
    p = RailRadarProvider(NoPlaces(), "k", store=False)
    monkeypatch.setattr(p, "trains", lambda frm, to, d: [])
    out, _ = search_station_pairs(p, TONE, TTWO, day, day, [("OF1", "Off 1")], [("OF2", "Off 2")], "test", 20)
    assert out == []
    assert not RS.known_empty("railradar", "OF1", "OF2", day.weekday())


# --- Traveller reports -----------------------------------------------------------------------------------------------

def _trip_with_train(client, auth, meta, kind="transport"):
    trip = client.post("/api/trips", json=trip_payload(), headers=auth).json()
    start = datetime(2026, 10, 10, 6, 15)  # a Saturday
    with SessionLocal() as db:
        it = Itinerary(trip_id=uuid.UUID(trip["id"]), title="Test plan")
        db.add(it)
        db.flush()
        item = ItineraryItem(itinerary_id=it.id, position=0, kind=kind, start_at=start, end_at=start + timedelta(hours=2),
                             title="Test Express", meta=meta)
        db.add(item)
        db.commit()
        return trip["id"], str(item.id)


def test_a_report_marks_the_route_for_refresh_first(client):
    auth = register(client)
    RS.save_timetable("railradar", "RQ1", "RQ2", 5, good("RQ1", "RQ2"), NOW)
    trip_id, item_id = _trip_with_train(client, auth, {
        "mode": "train", "source_kind": "live_api", "rail_source": "railradar", "number": "10001",
        "from_name": "Reportpur (RQ1)", "to_name": "Reportnagar (RQ2)"})
    r = client.post(f"/api/trips/{trip_id}/report-train", json={"item_id": item_id, "problem": "didnt_run"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["reported"] == 1 and r.json()["queued_for_refresh"] == 1
    saved = _timetable("railradar", "RQ1", "RQ2", 5)
    assert saved.needs_refresh is True and saved.refresh_priority == RS.REFRESH_PRIORITY["reported"] and saved.reported_at
    with SessionLocal() as db:
        rep = db.scalar(select(RailTrainReport).where(RailTrainReport.from_code == "RQ1"))
        assert rep.problem == "didnt_run" and rep.train_number == "10001" and rep.weekday == 5


def test_only_timetabled_trains_on_your_own_trip_can_be_reported(client):
    auth = register(client)
    guess = {"mode": "train", "source_kind": "station_guidance", "from_name": "A (RQ3)", "to_name": "B (RQ4)"}
    trip_id, item_id = _trip_with_train(client, auth, guess)
    body = {"item_id": item_id, "problem": "wrong_time"}
    assert client.post(f"/api/trips/{trip_id}/report-train", json=body, headers=auth).status_code == 422
    assert client.post(f"/api/trips/{trip_id}/report-train", json=body, headers=register(client)).status_code == 404
    assert client.post(f"/api/trips/{trip_id}/report-train", json={**body, "item_id": str(uuid.uuid4())},
                       headers=auth).status_code == 404


# --- The refresh worker ----------------------------------------------------------------------------------------------

class FakeRefresh:
    def __init__(self, name, answer=None):
        self.name, self.answer, self.calls = name, answer, []

    def fetch_live(self, frm, to, day):
        self.calls.append((frm, to))
        return good(frm, to) if self.answer is None else self.answer


def _queued(source, frm, to, priority, age_days):
    with SessionLocal() as db:
        db.add(RailTimetable(source=source, from_code=frm, to_code=to, weekday=2, rows=good(frm, to),
                             fetched_at=datetime.now(UTC) - timedelta(days=age_days), validation={"status": "ok"},
                             needs_refresh=True, refresh_priority=priority))
        db.commit()


def test_refresh_goes_reported_then_stale_then_aging_within_the_daily_cap(allow):
    from app.services.rail_refresh import refresh_due
    src = f"rf{uuid.uuid4().hex[:6]}"
    allow(src)
    _queued(src, "AG1", "AG2", 1, 12)
    _queued(src, "ST1", "ST2", 2, 25)
    _queued(src, "RP1", "RP2", 3, 2)
    _queued(src, "ST3", "ST4", 2, 30)  # older stale goes before newer stale
    p = FakeRefresh(src)
    assert refresh_due(p, limit=3) == 3
    assert p.calls == [("RP1", "RP2"), ("ST3", "ST4"), ("ST1", "ST2")]
    assert refresh_due(p, limit=3) == 0  # today's cap is spent
    assert refresh_due(p, limit=4) == 1 and p.calls[-1] == ("AG1", "AG2")
    refreshed = _timetable(src, "RP1", "RP2", 2)
    assert refreshed.needs_refresh is False and refreshed.refresh_priority == 0
    assert RS.freshness_of(RS._aware(refreshed.fetched_at)) == "fresh"


def test_a_refresh_that_finds_no_trains_stops_showing_the_old_copy(allow):
    from app.services.rail_refresh import refresh_due
    src = f"rf{uuid.uuid4().hex[:6]}"
    allow(src)
    _queued(src, "GN1", "GN2", 3, 5)
    assert refresh_due(FakeRefresh(src, answer=[]), limit=5) == 1
    assert RS.load_timetable(src, "GN1", "GN2", 2) is None
    assert RS.known_empty(src, "GN1", "GN2", 2)


def test_a_failed_refresh_counts_against_the_cap_and_keeps_the_route_queued(allow):
    from app.services.rail_refresh import refresh_due
    src = f"rf{uuid.uuid4().hex[:6]}"
    allow(src)
    _queued(src, "FL1", "FL2", 2, 25)

    class Down(FakeRefresh):
        def fetch_live(self, frm, to, day):
            raise ProviderUnavailable("down")
    assert refresh_due(Down(src), limit=1) == 1
    assert refresh_due(Down(src), limit=1) == 0
    assert _timetable(src, "FL1", "FL2", 2).needs_refresh is True


def test_refresh_is_off_at_cap_zero():
    from app.services.rail_refresh import refresh_due
    assert refresh_due(FakeRefresh("railradar"), limit=0) == 0


# --- Migration 0005 --------------------------------------------------------------------------------------------------

def _alembic(url, *args):
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DATABASE_URL": url}
    r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


def test_migration_0005_upgrades_and_downgrades_on_sqlite():
    path = os.path.join(tempfile.mkdtemp(prefix="tripai-mig-"), "m.db")
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0004")
    db = sqlite3.connect(path)
    now = "2026-10-01 00:00:00.000000"
    for source in ("irctc1", "railradar"):
        db.execute("insert into rail_timetables (id, from_code, to_code, weekday, source, rows, fetched_at, created_at, "
                   "updated_at) values (?, 'MG1', 'MG2', 0, ?, '[]', ?, ?, ?)", (uuid.uuid4().hex, source, now, now, now))
    db.commit()
    db.close()

    _alembic(url, "upgrade", "head")
    db = sqlite3.connect(path)
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"rail_city_routes", "rail_pair_results", "rail_train_reports", "rail_daily_stats"} <= tables
    left = db.execute("select source, expires_at, validation from rail_timetables").fetchall()
    assert len(left) == 1 and left[0][0] == "railradar"  # IRCTC1's stored copies are deleted (I7)
    assert left[0][1].startswith("2026-11-15") and "legacy" in left[0][2]
    db.close()

    _alembic(url, "downgrade", "0004")
    db = sqlite3.connect(path)
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert "rail_city_routes" not in tables and "rail_daily_stats" not in tables
    assert "expires_at" not in {r[1] for r in db.execute("pragma table_info(rail_timetables)")}
    db.close()
    _alembic(url, "upgrade", "head")


# --- train numbers: 4-6 digits are kept, anything that isn't 5 digits is logged (user decision 2026-10-06) -----------------

def test_four_to_six_digit_train_numbers_are_kept_and_odd_lengths_are_logged(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="tripai.rail_storage")
    rows = [row("12345", "08:00", "10:00", "VA1", "VB1"), row("1234", "09:00", "11:00", "VA1", "VB1"),
            row("150131", "10:00", "12:00", "VA1", "VB1"), row("1234567", "11:00", "13:00", "VA1", "VB1"),
            row("123", "12:00", "14:00", "VA1", "VB1")]
    kept, report = RS.validate_rows(rows, "VA1", "VB1")
    assert [r["train"]["number"] for r in kept] == ["12345", "1234", "150131"]  # valid data is not rejected over its length
    assert report["dropped"] == {"bad_number": 2}  # 7 digits and 3 digits are not train numbers
    logged = caplog.text
    assert "train 1234 has a 4-digit number" in logged and "train 150131 has a 6-digit number" in logged
    assert "train 12345 has" not in logged  # five digits is the normal case: silent


def test_the_ai_output_guard_rejects_any_four_to_six_digit_number_the_data_does_not_have():
    from app.services import llm_service as L
    from app.providers.rail_sources import rail_text_ok
    allowed = L.allowed_facts({"items": [{"title": "Train 150131"}]})
    for n in ("1234", "12345", "123456"):
        assert L.violations_in(f"Take the {n} from the station.", allowed) == [n]
    assert L.violations_in("Take the 150131 from the station.", allowed) == []  # a number the verified data has
    assert not rail_text_ok("Board the 150131 at dawn") and not rail_text_ok("The 1234 runs daily") and rail_text_ok("Board early")
