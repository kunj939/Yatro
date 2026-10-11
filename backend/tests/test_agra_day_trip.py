"""Regression for a real trip (Jaipur -> Agra day trip, 07:00-22:00): the refusal offered only "Add a day", which then
gave a night in Agra with no hotel. An earlier train would have made the day trip work."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.providers.types import PlaceData, TransportData
from app.services import feasibility as FE
from app.services import planning_support as PS
from app.services.engine_types import PlanInput

AGRA = PlaceData("t", "a", "Agra", "city", 27.18, 78.01)


def _t(direction, dep, arr, name, access=30):
    return TransportData(direction=direction, mode="train", name=name, service_class="CC", depart_at=dep, arrive_at=arr,
                         duration_minutes=int((arr - dep).total_seconds() // 60), cost_per_person=Decimal(400),
                         access_minutes=access, meta={})


def _inp(**over):
    base = dict(origin_text="Jaipur", destination_text="Agra", origin_place_id=None, destination_place_id=None,
                start=datetime(2026, 10, 10, 7), end=datetime(2026, 10, 10, 23, 17), budget=Decimal(5000), travellers=2,
                traveller_type="friends", transport_modes=["train", "bus"], food="veg", styles=["balanced"],
                stay="no_stay")
    base.update(over)
    return PlanInput(**base)


D = datetime(2026, 10, 10)
EARLY = _t("outbound", D.replace(hour=6, minute=10), D.replace(hour=10, minute=40), "Ajmer Agra Express")  # leave 05:25
LATE = _t("outbound", D.replace(hour=13, minute=0), D.replace(hour=16, minute=20), "Afternoon Express")
HOME = _t("return", D.replace(hour=17, minute=20), D.replace(hour=22, minute=40), "Udaipur City Express")


def test_an_earlier_start_is_offered_first_and_needs_no_night_away():
    info = FE.time_infeasible(_inp(), [EARLY, LATE, HOME], ["train", "bus"], AGRA)
    first = info["adjustments"][0]
    assert first["key"] == "start_earlier" and first["apply"] == {"start_time": "05:25"}
    assert "Ajmer Agra Express" in first["description"]


def test_adding_a_night_to_a_no_stay_trip_also_books_a_bed():
    info = FE.time_infeasible(_inp(), [LATE, HOME], ["train", "bus"], AGRA)
    day = next(a for a in info["adjustments"] if a["label"] == "Add a day")
    assert day["apply"]["stay"] == "budget"


def test_a_trip_spanning_a_night_never_plans_without_a_bed():
    prefs = SimpleNamespace(trip_styles=["balanced"], interests=[], notes=None, traveller_type="friends",
                            transport_modes=["train"], food="veg", stay="no_stay", pace="normal", excluded_place_ids=[],
                            pinned_place_ids=[], pinned_transport={})
    trip = SimpleNamespace(preferences=prefs, origin_name="Jaipur", destination_name="Agra", origin_place_id=None,
                           destination_place_id=None, start_date=date(2026, 10, 10), start_time=time(7),
                           end_date=date(2026, 10, 11), end_time=time(23, 17), budget=5000, travellers=2, members=[],
                           timezone="Asia/Kolkata", currency="INR")
    assert PS.plan_input(trip).stay == "budget"
    trip.end_date, trip.end_time = date(2026, 10, 11), time(1, 30)  # home after midnight: still a day trip
    assert PS.plan_input(trip).stay == "no_stay"
    trip.end_date = date(2026, 10, 10) + timedelta(days=0)
    trip.end_time = time(22)
    assert PS.plan_input(trip).stay == "no_stay"
