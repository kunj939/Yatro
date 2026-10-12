"""End to end: a train that pulls in at 00:48 is followed by a hotel check-in that night, not one the next evening
(a Jaipur -> Udaipur plan checked in at 20:04 on the day after a 00:48 arrival, booked for one night)."""
from dataclasses import replace
from datetime import datetime, timedelta

from app.api.deps import providers_dep
from app.main import app
from tests.conftest import register, trip_payload
from tests.sim_providers import sim_providers


def test_a_night_arrival_checks_in_that_night_for_the_right_number_of_nights(client):
    p = sim_providers("normal")
    real = p.transport.intercity_options

    def overnight(origin, dest, depart_day, return_day, *a, **k):
        out = []
        for t in real(origin, dest, depart_day, return_day, *a, **k):
            if t.direction == "outbound" and t.mode == "train":
                dep = datetime.combine(depart_day, datetime.min.time()) + timedelta(hours=16, minutes=5)
                out.append(replace(t, depart_at=dep, arrive_at=dep + timedelta(minutes=523), duration_minutes=523))
            elif t.direction == "return":
                out.append(t)
        return out

    p.transport.intercity_options = overnight
    app.dependency_overrides[providers_dep] = lambda: p
    try:
        auth = register(client)
        body = trip_payload(stay="tier2", transport_modes=["train"], budget=40000, start_time="07:00", end_time="22:00")
        start = datetime.fromisoformat(body["start_date"])
        body["end_date"] = (start + timedelta(days=2)).date().isoformat()
        trip = client.post("/api/trips", json=body, headers=auth).json()
        out = client.post(f"/api/trips/{trip['id']}/plan", json={}, headers=auth).json()
    finally:
        app.dependency_overrides.clear()
    assert out["status"] == "ready", out.get("feasibility") or out.get("message")
    items = [i for d in out["trip"]["itinerary"]["days"] for i in d["items"]]
    checkins = [i for i in items if i["kind"] == "stay" and i["title"].startswith("Check in")]
    assert len(checkins) == 1, [i["title"] for i in items]
    assert checkins[0]["time"] < "06:00" and checkins[0]["subtitle"].startswith("2 nights")  # that night, both nights
    train = next(i for i in items if i["kind"] == "transport" and i["time"] == "16:05")
    assert items.index(checkins[0]) == items.index(train) + 1  # straight after the train, with the cab ride
    assert any(i["title"].startswith("Start from") for i in items)  # the first morning starts at the hotel
    assert any(i["title"].startswith("Back to") for i in items)  # the first evening returns there
