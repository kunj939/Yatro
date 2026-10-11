"""Journeys with no direct service: a change of train at a junction, and a flight from the airport that actually
serves a place, with the road ride at each end. (A road ride to a railhead, then the train, is covered in
test_indian_rail.py / test_railradar.py.)"""
from datetime import date, datetime, timedelta

from app.providers.google import AIRPORT_MINUTES, AIRPORT_MINUTES_ABROAD, airport_flights
from app.providers.rail_common import CHANGE_MIN, junctions
from app.providers.rail_stations import STATIONS, station_position
from app.providers.railradar import RailRadarProvider
from app.providers.types import PlaceData
from app.services import feasibility as FE
from app.services.engine_types import PlanInput

JAIPUR = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)
HARIDWAR = PlaceData("google", "h", "Haridwar", "city", 29.9457, 78.1642)
GURUGRAM = PlaceData("google", "g", "Gurugram", "city", 28.4595, 77.0266)
MUSSOORIE = PlaceData("google", "m", "Mussoorie", "city", 30.4598, 78.0644)
ISTANBUL = PlaceData("google", "i", "Istanbul", "city", 41.0082, 28.9784)
AGRA = PlaceData("google", "a", "Agra", "city", 27.1767, 78.0081)
MUMBAI = PlaceData("google", "b", "Mumbai", "city", 19.0760, 72.8777)


def row(number, name, frm, to, dep, minutes, km=250):
    return {"train": {"number": number, "name": name, "type": "Express"}, "from": {"code": frm, "departure": dep},
            "to": {"code": to}, "distance": km, "duration": minutes}


def provider(per_pair: dict, calls: list):
    rr = RailRadarProvider(None, "key", store=False)

    def fake_get(path, params, ttl):
        _, _, _, frm, to = path.split("/")
        calls.append((frm, to))
        return {"success": True, "data": {"trains": per_pair.get((frm, to), [])}}
    rr._get = fake_get
    return rr


def test_junctions_lie_on_the_way_and_use_the_main_terminal():
    picked = junctions(station_position("JP"), station_position("HW"), {"JP", "HW"})
    assert picked and picked[0][0] == "NDLS"  # Delhi, at its main station (more trains than Old Delhi)
    assert all(STATIONS[c][3] == 3 for c, _ in picked)
    assert junctions(station_position("NDLS"), station_position("GZB"), set()) == []  # too short for a change


def test_no_direct_train_gives_a_journey_with_one_change():
    hub = junctions(station_position("JP"), station_position("HW"), {"JP", "HW"})[0][0]
    calls: list = []
    rr = provider({
        ("JP", hub): [row("12985", "Double Decker Express", "JP", hub, "06:00", 270),  # reaches 10:30
                      row("12016", "Shatabdi Express", "JP", hub, "17:35", 295)],
        (hub, "HW"): [row("12017", "Dehradun Shatabdi Express", hub, "HW", "06:45", 260),  # too early to connect
                      row("12055", "Jan Shatabdi Express", hub, "HW", "11:40", 255),  # 70 min after the first arrives
                      row("14041", "Mussoorie Express", hub, "HW", "22:25", 400)],
        ("HW", hub): [row("12018", "Dehradun Shatabdi Express", "HW", hub, "13:00", 250)],  # reaches 17:10
        (hub, "JP"): [row("12986", "Double Decker Express", hub, "JP", "18:10", 275)],
    }, calls)
    out = rr.intercity(JAIPUR, HARIDWAR, date(2026, 10, 10), date(2026, 10, 11))
    there = [t for t in out if t.direction == "outbound"]
    back = [t for t in out if t.direction == "return"]
    assert there and back and all(len(t.meta["legs"]) == 2 and t.meta["changes"] == 1 for t in out)
    first = min(there, key=lambda t: t.depart_at)
    assert [leg["number"] for leg in first.meta["legs"]] == ["12985", "12055"]
    assert first.depart_at == datetime(2026, 10, 10, 6, 0) and first.meta["change_minutes"] == 70
    assert first.from_name.startswith("Jaipur") and first.to_name.startswith("Haridwar")
    assert all(t.meta["change_minutes"] >= CHANGE_MIN for t in out)
    assert first.cost_per_person > 0 and first.access_minutes > 0  # both fares, and the ride to Jaipur station
    label = FE.transport_label(first)
    assert "Double Decker Express 12985, then Jan Shatabdi Express 12055" in label and "change at" in label
    assert FE.service_matches(first, "12055") and FE.service_matches(first, "double decker")
    assert ("JP", "HW") in calls  # the direct route was asked about first


def test_a_direct_train_means_no_change_is_looked_for():
    calls: list = []
    rr = provider({("JP", "HW"): [row("19031", "Yoga Express", "JP", "HW", "08:00", 540)],
                   ("HW", "JP"): [row("19032", "Yoga Express", "HW", "JP", "08:00", 540)]}, calls)
    out = rr.intercity(JAIPUR, HARIDWAR, date(2026, 10, 10), date(2026, 10, 11))
    assert out and not any(t.meta.get("legs") for t in out)
    hub = junctions(station_position("JP"), station_position("HW"), {"JP", "HW"})[0][0]
    assert ("JP", hub) not in calls


def test_a_connection_plans_like_any_train():
    hub = junctions(station_position("JP"), station_position("HW"), {"JP", "HW"})[0][0]
    rr = provider({("JP", hub): [row("12985", "Double Decker Express", "JP", hub, "06:00", 270)],
                   (hub, "HW"): [row("12055", "Jan Shatabdi Express", hub, "HW", "11:40", 255)],
                   ("HW", hub): [row("12018", "Dehradun Shatabdi Express", "HW", hub, "13:00", 250)],
                   (hub, "JP"): [row("12986", "Double Decker Express", hub, "JP", "18:10", 275)]}, [])
    out = rr.intercity(JAIPUR, HARIDWAR, date(2026, 10, 10), date(2026, 10, 11))
    inp = PlanInput(origin_text="Jaipur", destination_text="Haridwar", origin_place_id=None, destination_place_id=None,
                    start=datetime(2026, 10, 10, 5), end=datetime(2026, 10, 11, 23), budget=20000, travellers=2,
                    traveller_type="family", transport_modes=["train"], food="both", styles=["balanced"], stay="budget")
    best = FE.rank_combos(FE.build_combos(out, inp, JAIPUR, HARIDWAR), inp)[0]
    assert best.outbound.meta["legs"] and best.ret.meta["legs"]


# --- Flights from the airport that serves the place ----------------------------------------------------------------

def test_a_flight_runs_between_the_real_airports_with_the_road_at_each_end():
    out = airport_flights(MUMBAI, MUSSOORIE, datetime(2026, 10, 10, 6), datetime(2026, 10, 12, 21), "INR")
    go = next(t for t in out if t.direction == "outbound")
    assert "(BOM)" in go.from_name and "(DED)" in go.to_name  # Mumbai airport, then Dehradun's Jolly Grant
    assert (go.to_lat, go.to_lng) != (MUSSOORIE.lat, MUSSOORIE.lng)  # lands at the airport; the plan drives up from there
    assert go.access_minutes > 0 and go.access_cost > 0  # the cab to Mumbai airport
    assert go.meta["airport_minutes"] == AIRPORT_MINUTES
    back = next(t for t in out if t.direction == "return")
    assert back.arrive_at + timedelta(minutes=back.access_minutes) == datetime(2026, 10, 12, 21)  # home by the end


def test_no_flight_when_one_airport_serves_both_or_they_are_close():
    assert airport_flights(GURUGRAM, AGRA, datetime(2026, 10, 10, 6), datetime(2026, 10, 10, 21), "INR") == []
    assert airport_flights(GURUGRAM, MUSSOORIE, datetime(2026, 10, 10, 6), datetime(2026, 10, 10, 21), "INR") == []


def test_an_international_flight_allows_more_time_at_the_airports():
    out = airport_flights(GURUGRAM, ISTANBUL, datetime(2026, 10, 10, 6), datetime(2026, 10, 17, 21), "TRY")
    go = next(t for t in out if t.direction == "outbound")
    assert "(IST)" in go.to_name and go.meta["airport_minutes"] == AIRPORT_MINUTES_ABROAD


def test_an_unknown_airport_falls_back_to_a_city_estimate():
    nowhere = PlaceData("google", "x", "Middle of the Pacific", "city", 0.0, -150.0)
    assert airport_flights(GURUGRAM, nowhere, datetime(2026, 10, 10, 6), datetime(2026, 10, 10, 21), "INR") is None
