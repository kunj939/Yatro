"""A sunset point is only ever visited around sunset, never at 10 am."""
from datetime import datetime, timedelta

import pytest

from app.api.routes.demo import demo_date
from tests.conftest import register, trip_payload


@pytest.mark.parametrize("budget,styles", [(6000, ["romantic", "sightseeing"]), (8000, ["romantic"]),
                                           (6000, ["balanced"])])
def test_sunset_points_are_scheduled_near_sunset(client, budget, styles):
    d = demo_date() - timedelta(days=1)
    body = trip_payload(start_date=d.isoformat(), end_date=(d + timedelta(days=1)).isoformat(), start_time="07:00",
                        end_time="22:00", budget=budget, traveller_type="couple", stay="oyo", trip_styles=styles)
    headers = register(client)
    trip = client.post("/api/trips", json=body, headers=headers).json()
    out = client.post(f"/api/trips/{trip['id']}/plan", json={}, headers=headers).json()
    assert out["status"] == "ready"
    for day in out["trip"]["itinerary"]["days"]:
        for item in day["items"]:
            if "sunset" in item["title"].lower() and item["kind"] in ("activity", "sunset"):
                start = datetime.fromisoformat(item["start_at"]).time()
                assert start.hour >= 15, f"{item['title']} scheduled at {start}"
            for alt in item["alternatives"]:
                if "sunset" in alt["title"].lower() and item["kind"] == "activity":
                    assert datetime.fromisoformat(item["start_at"]).hour >= 15, alt["title"]