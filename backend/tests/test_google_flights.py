"""Real flights from Google Flights via SerpApi (ADR 0020): airline, flight numbers, each airport's local times, the
layover, Google's price; flight check-in before departure and bags/immigration after landing; the estimate stays
when there's no key or no answer."""
from datetime import date, datetime
from decimal import Decimal

from app.providers import google_flights as GF
from app.providers.types import PlaceData
from app.services import feasibility as FE

RANCHI = PlaceData("google", "r", "Ranchi", "city", 23.3441, 85.3096)
LONDON = PlaceData("google", "l", "London", "city", 51.5072, -0.1276)


def _seg(frm, frm_name, dep, to, to_name, arr, minutes, airline, number):
    return {"departure_airport": {"name": frm_name, "id": frm, "time": dep},
            "arrival_airport": {"name": to_name, "id": to, "time": arr}, "duration": minutes,
            "airline": airline, "flight_number": number, "travel_class": "Economy", "airplane": "Airbus A320"}


ONE_STOP = {"flights": [_seg("IXR", "Birsa Munda Airport", "2026-11-07 06:10", "DEL", "Indira Gandhi International Airport",
                             "2026-11-07 08:20", 130, "IndiGo", "6E 2134"),
                        _seg("DEL", "Indira Gandhi International Airport", "2026-11-07 11:30", "LHR", "Heathrow Airport",
                             "2026-11-07 16:25", 625, "Air India", "AI 111")],
            "layovers": [{"duration": 190, "name": "Indira Gandhi International Airport", "id": "DEL"}],
            "total_duration": 945, "price": 48250, "type": "One way"}
DIRECT_HOME = {"flights": [_seg("LHR", "Heathrow Airport", "2026-11-12 21:00", "DEL", "Indira Gandhi International Airport",
                                "2026-11-13 10:40", 550, "Air India", "AI 112")],
               "total_duration": 550, "price": 39900}


def test_a_one_stop_flight_is_named_timed_and_priced_like_the_ticket():
    t = GF.journey(ONE_STOP, "outbound", RANCHI, "INR")
    assert t.mode == "flight" and t.name == "IndiGo + Air India" and t.verification_state == "verified_provider"
    assert t.depart_at == datetime(2026, 11, 7, 6, 10) and t.arrive_at == datetime(2026, 11, 7, 16, 25)  # local clocks
    assert t.duration_minutes == 945 and t.cost_per_person == Decimal(48250)
    assert t.from_name == "Ranchi Airport (IXR)" and t.to_name == "London Heathrow Airport (LHR)"
    m = t.meta
    assert m["changes"] == 1 and m["change_minutes"] == 190 and "DEL" in m["change_at"]
    assert [(leg["name"], leg["number"], leg["depart_local"], leg["arrive_local"]) for leg in m["legs"]] == [
        ("IndiGo", "6E 2134", "06:10", "08:20"), ("Air India", "AI 111", "11:30", "16:25")]
    assert m["international"] and m["check_in_minutes"] == 180 and m["exit_minutes"] == 60
    assert t.access_minutes > 10  # the cab to Ranchi airport comes first
    label = FE.transport_label(t)
    assert "IndiGo 6E 2134" in label and "Air India AI 111" in label and "change at" in label


def test_a_direct_flight_carries_its_number_and_an_overnight_arrival():
    t = GF.journey(DIRECT_HOME, "return", RANCHI, "INR")
    assert t.name == "Air India" and t.meta["number"] == "AI 112" and t.meta["changes"] == 0
    assert FE.transport_label(t).startswith("Flight: Air India AI 112")
    assert t.meta["overnight"] and t.meta["arrive_local"] == "10:40"


def test_flight_check_in_and_landing_time_count_and_trains_keep_their_minutes():
    t = GF.journey(ONE_STOP, "outbound", RANCHI, "INR")
    assert FE.boarding_minutes(t) == 180 and FE.exit_minutes(t) == 60
    assert FE._leave_home(t) == datetime(2026, 11, 7, 6, 10) - __import__("datetime").timedelta(minutes=t.access_minutes + 180)
    train = GF.journey(ONE_STOP, "outbound", RANCHI, "INR")
    train.mode, train.meta = "train", {}
    assert FE.boarding_minutes(train) == FE.BOARDING_BUFFER and FE.exit_minutes(train) == FE.EXIT_BUFFER


def test_results_that_cannot_be_trusted_whole_are_left_out():
    assert GF.journey({**ONE_STOP, "price": None}, "outbound", RANCHI, "INR") is None
    three_stops = {**ONE_STOP, "flights": ONE_STOP["flights"] * 2}
    assert GF.journey(three_stops, "outbound", RANCHI, "INR") is None


def test_both_directions_from_google_flights_with_a_key_and_the_estimate_without(monkeypatch):
    from app.core.config import get_settings
    asked = []

    def search(frm, to, day, currency, market):
        asked.append((frm, to, day, market))
        return {"best_flights": [ONE_STOP if day == date(2026, 11, 7) else DIRECT_HOME], "other_flights": []}

    monkeypatch.setattr(GF, "_search", search)
    monkeypatch.setattr(get_settings(), "serpapi_api_key", None)
    assert GF.flights(RANCHI, LONDON, date(2026, 11, 7), date(2026, 11, 12), "INR") is None  # no key: the estimate stays
    monkeypatch.setattr(get_settings(), "serpapi_api_key", "test-key")
    out = GF.flights(RANCHI, LONDON, date(2026, 11, 7), date(2026, 11, 12), "INR")
    assert [t.direction for t in out] == ["outbound", "return"]
    assert asked[0][0] == ["IXR"] or asked[0][0][0] == "IXR"  # Ranchi's airport first
    assert set(asked[0][1]) >= {"LHR"} and asked[0][3] == "in"

    def down(*a):
        raise RuntimeError("SerpApi 503")
    monkeypatch.setattr(GF, "_search", down)
    assert GF.flights(RANCHI, LONDON, date(2026, 11, 7), date(2026, 11, 12), "INR") is None
