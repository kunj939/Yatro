"""Two-day trips: every day gets its meals, and a budget that can't cover them is reported as infeasible."""
from datetime import timedelta

from app.api.routes.demo import demo_date
from tests.conftest import register, trip_payload


def _two_day(budget: int) -> dict:
    d = demo_date() - timedelta(days=1)  # Friday -> Saturday
    return trip_payload(start_date=d.isoformat(), end_date=(d + timedelta(days=1)).isoformat(), start_time="07:00",
                        end_time="22:00", budget=budget, traveller_type="friends", stay="oyo")


def _plan(client, headers, budget):
    trip = client.post("/api/trips", json=_two_day(budget), headers=headers).json()
    return client.post(f"/api/trips/{trip['id']}/plan", json={}, headers=headers).json()


def test_every_day_has_its_meals_and_sights(client):
    out = _plan(client, register(client), 6000)
    assert out["status"] == "ready", out.get("feasibility")
    it = out["trip"]["itinerary"]
    assert it["budget"]["status"] != "over"
    assert not any("No " in w and "stop matched" in w for w in it["warnings"]), it["warnings"]
    for day in it["days"]:
        kinds = [i["kind"] for i in day["items"]]
        assert kinds.count("meal") >= 2, (day["label"], kinds)
        assert kinds.count("activity") + kinds.count("sunset") >= 2, (day["label"], kinds)
        free = sum(i["duration_minutes"] for i in day["items"] if i["kind"] == "free")
        assert free <= 120, (day["label"], free)


def test_budget_that_cannot_feed_everyone_is_infeasible(client):
    out = _plan(client, register(client), 4000)  # ₹4,500 still is too: cheaper cabs (ADR 0018) made ₹5,000 just fit
    assert out["status"] == "infeasible"
    f = out["feasibility"] if "feasibility" in out and out["feasibility"] else out["trip"]["feasibility"]
    assert f["message"] == "The requested trip is not feasible within ₹4,000."
    assert any(a["key"] == "higher_budget" for a in f["adjustments"])
    assert all(a.get("saves", 1) > 0 for a in f["adjustments"] if a["key"] == "cheaper_hotel")
