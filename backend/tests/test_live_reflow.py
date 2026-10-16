"""Live trip mode (ADR 0021): "running late" re-flows the rest of a day offline."""
from __future__ import annotations

import uuid
from datetime import datetime

from app.models.trip import ItineraryItem
from app.services.live_reflow import reflow
from tests.test_scenarios import _planned_agra, plus_user  # noqa: F401

D = "2026-11-08"


def item(pos, kind, start, end, title=None, travel=0, opens=None, closes=None, **meta):
    return ItineraryItem(id=uuid.uuid4(), position=pos, day_index=0, kind=kind, title=title or f"{kind} {pos}",
                         start_at=datetime.fromisoformat(f"{D}T{start}"), end_at=datetime.fromisoformat(f"{D}T{end}"),
                         travel_minutes_from_prev=travel, opening_time=opens, closing_time=closes, meta=meta)


def times(stops):
    return [(s.item.title, s.start.strftime("%H:%M"), s.end.strftime("%H:%M")) for s in stops if not s.dropped]


def test_shift_moves_the_rest_and_a_gap_absorbs_it():
    day = [item(0, "activity", "09:00", "10:00", "Fort"), item(1, "activity", "10:20", "11:20", "Palace", travel=20),
           item(2, "activity", "13:00", "14:00", "Garden", travel=15)]
    res, stops = reflow(day, 30, day[1].id, pinned=set())
    assert res.status == "shifted" and res.dropped == []
    assert times(stops) == [("Fort", "09:00", "10:00"), ("Palace", "10:50", "11:50"), ("Garden", "13:00", "14:00")]
    assert "Running 30 min late" in res.note


def test_default_start_is_the_first_stop_not_started():
    day = [item(0, "activity", "09:00", "10:00", "Fort"), item(1, "activity", "10:20", "11:20", "Palace", travel=20)]
    res, stops = reflow(day, 45, None, pinned=set(), now=datetime.fromisoformat(f"{D}T09:30"))
    assert times(stops)[0] == ("Fort", "09:00", "10:00") and times(stops)[1] == ("Palace", "11:05", "12:05")


def test_a_stop_that_would_be_closed_is_dropped_and_its_cost_named():
    day = [item(0, "activity", "15:00", "16:00", "Museum", closes="17:00", rating=4.6, rating_count=900),
           item(1, "activity", "16:15", "17:15", "Bazaar", travel=15, closes="23:00")]
    res, stops = reflow(day, 90, day[0].id, pinned=set())
    assert res.dropped == ["Museum"]
    assert times(stops) == [("Bazaar", "16:45", "17:45")]


def test_a_visit_is_shortened_to_fit_before_closing():
    day = [item(0, "activity", "15:00", "16:40", "Museum", closes="17:00")]
    res, stops = reflow(day, 30, day[0].id, pinned=set())
    assert res.shortened == ["Museum"] and times(stops) == [("Museum", "15:30", "17:00")]


def test_tickets_never_move_and_low_stops_go_first_to_catch_one():
    day = [item(0, "activity", "14:00", "15:00", "Famous fort", rating=4.8, rating_count=50000),
           item(1, "activity", "15:15", "16:15", "Small park", travel=15, rating=3.9, rating_count=40),
           item(2, "meal", "16:30", "17:15", "Early dinner", travel=15),
           item(3, "transport", "18:00", "22:00", "Shatabdi 12016", travel=20)]
    res, stops = reflow(day, 45, day[0].id, pinned=set())
    assert res.status == "shifted" and res.dropped == ["Small park"]
    assert ("Shatabdi 12016", "18:00", "22:00") in times(stops)
    meal = next(s for s in stops if s.item.title == "Early dinner")
    assert meal.end.strftime("%H:%M") <= "17:25"  # dinner, then 20 min to the station, 15 min to board


def test_dinner_goes_before_the_train_does():
    day = [item(0, "meal", "16:30", "17:15", "Dinner"), item(1, "transport", "17:45", "21:00", "Shatabdi 12016", travel=20)]
    res, stops = reflow(day, 30, day[0].id, pinned=set())  # free at 17:00: no time to eat, just enough for the train
    assert res.dropped == ["Dinner"] and ("Shatabdi 12016", "17:45", "21:00") in times(stops)


def test_a_ticket_that_cant_be_caught_changes_nothing():
    day = [item(0, "stay", "17:00", "17:10", "Hotel check-out"), item(1, "transport", "17:45", "21:00", "Shatabdi 12016", travel=20)]
    res, stops = reflow(day, 30, day[0].id, pinned=set())
    assert res.status == "missed" and res.missed == {"title": "Shatabdi 12016", "time": "17:45"}
    assert all(s.start == s.item.start_at for s in stops)


def test_meals_and_must_sees_outlast_ordinary_sights_when_the_day_overflows():
    day = [item(0, "activity", "18:00", "19:00", "Pinned temple", key="pin-1"),
           item(1, "activity", "19:10", "20:10", "Viewpoint", travel=10, rating=4.0, rating_count=100),
           item(2, "meal", "20:20", "21:20", "Dinner", travel=10)]
    res, stops = reflow(day, 60, day[0].id, pinned={"pin-1"}, day_end=datetime.fromisoformat(f"{D}T22:00").time())
    assert res.dropped == ["Viewpoint"]
    assert [t for t, *_ in times(stops)] == ["Pinned temple", "Dinner"]


def test_a_sunset_is_dropped_rather_than_watched_in_the_dark():
    day = [item(0, "activity", "16:00", "17:00", "Fort"), item(1, "sunset", "17:40", "18:30", "Sunset point", travel=20)]
    res, stops = reflow(day, 60, day[0].id, pinned=set())
    assert res.dropped == ["Sunset point"] and times(stops) == [("Fort", "17:00", "18:00")]


def test_nothing_to_move_after_the_last_stop():
    day = [item(0, "activity", "09:00", "10:00", "Fort")]
    res, _ = reflow(day, 30, None, pinned=set(), now=datetime.fromisoformat(f"{D}T20:00"))
    assert res.status == "nothing"


# ------------------------------------------------------------------------------------------------------ API

def test_running_late_api_saves_a_version_uses_no_replan_and_undoes(client, plus_user):  # noqa: F811
    tid, trip = _planned_agra(client, plus_user, d="Jaipur", days=2, stay="tier2", budget=30000)
    day = trip["itinerary"]["days"][1]
    first = next(i for i in day["items"] if i["kind"] in ("activity", "meal"))
    before = trip["itinerary"]["version"], trip["replan_count"]
    r = client.post(f"/api/trips/{tid}/live/late", headers=plus_user,
                    json={"minutes": 60, "day_index": day["index"], "from_item_id": first["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"]["status"] == "shifted", out["result"]
    t = out["trip"]
    assert t["replan_count"] == before[1]
    if out["result"]["status"] == "shifted":
        assert t["itinerary"]["version"] == before[0] + 1
        assert t["itinerary"]["change_note"].startswith("Running 1 h late")
        moved = next(i for d in t["itinerary"]["days"] for i in d["items"] if i["title"] == first["title"])
        assert moved["start_at"] > first["start_at"]
        u = client.post(f"/api/trips/{tid}/live/undo", headers=plus_user)
        assert u.status_code == 200 and u.json()["trip"]["itinerary"]["version"] == before[0]
    assert client.post(f"/api/trips/{tid}/live/undo", headers=plus_user).status_code == 409


def test_running_late_is_only_for_the_owner(client, plus_user):  # noqa: F811
    from tests.conftest import register
    tid, trip = _planned_agra(client, plus_user)
    other = register(client)
    r = client.post(f"/api/trips/{tid}/live/late", headers=other, json={"minutes": 30, "day_index": 0})
    assert r.status_code == 404
