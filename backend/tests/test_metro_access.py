"""Regression for a real plan: Gurugram -> Agra with a 06:55 train from New Delhi. The ride to the station was
planned as a 1 h 46 min metro leaving at 04:54 — far too long (the Yellow Line takes about an hour) and before the
metro runs (first trains about 05:15)."""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from app.providers.rail_stations import metro_area, metro_runs_at
from app.providers.types import PlaceData, TransportData
from app.services import fare_model as F
from app.services import planning_support as PS
from app.utils.geo import estimate_road

GGN = PlaceData("t", "g", "Gurugram", "city", 28.4595, 77.0266)
NDLS = (28.6428, 77.2197)
INP = SimpleNamespace(transport_modes=["train", "metro", "bus"], styles=["budget"], currency="INR")


def _train(direction: str, dep: datetime, arr: datetime) -> TransportData:
    road_m, secs = estimate_road(GGN.lat, GGN.lng, *NDLS)
    return TransportData(direction=direction, mode="train", name="Taj Express", depart_at=dep, arrive_at=arr,
                         duration_minutes=int((arr - dep).total_seconds() // 60), cost_per_person=Decimal(282),
                         from_lat=NDLS[0], from_lng=NDLS[1], to_lat=NDLS[0], to_lng=NDLS[1],
                         access_minutes=secs // 60 + 5, access_cost=Decimal(600), access_distance_m=road_m)


def test_metro_takes_about_an_hour_not_nearly_two():
    road_m, _ = estimate_road(GGN.lat, GGN.lng, *NDLS)
    assert 55 <= F.metro_minutes(road_m) <= 85  # Gurugram -> New Delhi, door to door


def test_metro_is_used_when_it_is_running_and_not_before_the_first_train():
    day = datetime(2026, 10, 10)
    ok = _train("outbound", day.replace(hour=7, minute=30), day.replace(hour=10))
    dawn = _train("outbound", day.replace(hour=6, minute=0), day.replace(hour=8, minute=30))
    PS.choose_station_access([ok, dawn], INP, GGN)
    assert ok.meta.get("access_mode") == "transit"  # leaves home about 06:00, metro running
    assert dawn.meta.get("access_mode") != "transit"  # would mean boarding before 05:00: keep the cab
    assert dawn.access_cost == Decimal(600)


def test_no_metro_home_after_the_last_train():
    day = datetime(2026, 10, 10)
    early = _train("return", day.replace(hour=17), day.replace(hour=19, minute=30))
    late = _train("return", day.replace(hour=19), day.replace(hour=23, minute=20))
    PS.choose_station_access([early, late], INP, GGN)
    assert early.meta.get("access_mode") == "transit"
    assert late.meta.get("access_mode") != "transit"  # reaches the station after the last metro


def test_service_hours():
    area = metro_area(GGN.lat, GGN.lng)
    assert area == "Delhi NCR"
    assert not metro_runs_at(area, datetime(2026, 10, 10, 4, 54))
    assert metro_runs_at(area, datetime(2026, 10, 10, 5, 20))
    assert metro_runs_at(area, datetime(2026, 10, 10, 22, 59))
    assert not metro_runs_at(area, datetime(2026, 10, 11, 0, 25))
