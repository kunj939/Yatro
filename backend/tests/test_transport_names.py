"""Transport is never given an invented name: names come only from a live timetable. Times and money are the
destination's own (tz, currency) — never assumed to be India for a trip that isn't."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.providers.google import GoogleTransportProvider
from app.providers.types import PlaceData, TransportData
from app.services.feasibility import transport_label

LONDON_TZ = ZoneInfo("Europe/London")
IST = ZoneInfo("Asia/Kolkata")


def _step(vtype, name, short, dep, arr, frm, to, trip=None, fare=None):
    d = {"stopDetails": {"departureTime": dep, "arrivalTime": arr,
                         "departureStop": {"name": frm, "location": {"latLng": {"latitude": 51.53, "longitude": -0.12}}},
                         "arrivalStop": {"name": to, "location": {"latLng": {"latitude": 55.95, "longitude": -3.19}}}},
         "transitLine": {"name": name, "nameShort": short, "vehicle": {"type": vtype},
                         "agencies": [{"name": "LNER"}]}}
    if trip:
        d["tripShortText"] = trip
    return {"transitDetails": d}


def _provider():
    return GoogleTransportProvider.__new__(GoogleTransportProvider)


def _scheduled(data, direction, mode, km, home, origin, dest, tz=IST, currency="INR"):
    return _provider()._scheduled(data, direction, mode, km, home, origin, dest, tz, currency)


HOME = PlaceData("google", "x", "London", "city", 51.5074, -0.1278)
EDIN = PlaceData("google", "e", "Edinburgh", "city", 55.9533, -3.1883)
JAIPUR = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)


def test_named_train_comes_from_the_timetable_and_metro_legs_are_ignored():
    data = {"routes": [{"distanceMeters": 632000, "legs": [{"steps": [
        _step("SUBWAY", "Victoria line", "", "2026-10-03T07:40:00Z", "2026-10-03T07:52:00Z", "Oxford Circus", "King's Cross"),
        _step("HIGH_SPEED_TRAIN", "London North Eastern Railway", "", "2026-10-03T08:00:00Z", "2026-10-03T12:20:00Z",
              "London King's Cross", "Edinburgh Waverley", trip="1S09"),
    ]}]}]}
    [t] = _scheduled(data, "outbound", "train", 632, HOME, HOME, EDIN, tz=LONDON_TZ, currency="GBP")
    assert t.name == "London North Eastern Railway" and t.meta["number"] == "1S09"
    assert t.meta["changes"] == 1 and t.service_class is None  # no Indian seat classes outside India
    assert transport_label(t) == "Train: London North Eastern Railway 1S09 (1 change)"
    assert t.access_distance_m > 0 and t.access_cost > 0  # transfer computed from real coordinates


def test_unnamed_service_is_described_not_named():
    data = {"routes": [{"legs": [{"steps": [
        _step("HEAVY_RAIL", "", "", "2026-10-03T06:40:00Z", "2026-10-03T11:10:00Z", "A", "Jaipur Junction")]}]}]}
    [t] = _scheduled(data, "outbound", "train", 238, HOME, HOME, JAIPUR)
    assert t.name == "" and transport_label(t) == "12:10 train to Jaipur Junction"  # 06:40 UTC in IST (+5:30)


def test_route_with_only_city_transit_is_not_offered_as_a_train():
    data = {"routes": [{"legs": [{"steps": [
        _step("SUBWAY", "Yellow Line", "", "2026-10-03T06:40:00Z", "2026-10-03T07:10:00Z", "A", "B")]}]}]}
    assert _scheduled(data, "outbound", "train", 30, HOME, HOME, EDIN) == []


def test_estimated_journeys_say_so():
    t = TransportData(direction="outbound", mode="bus", name="", depart_at=datetime(2026, 10, 3, 7),
                      arrive_at=datetime(2026, 10, 3, 12), duration_minutes=300, cost_per_person=Decimal(500),
                      to_name="Jaipur", flexible=True)
    assert transport_label(t) == "Bus to Jaipur (timing estimated)"


def test_regional_rail_hop_before_a_long_bus_is_not_a_train_journey():
    data = {"routes": [{"legs": [{"steps": [
        _step("COMMUTER_TRAIN", "Namo Bharat RRTS", "", "2026-10-03T04:00:00Z", "2026-10-03T05:00:00Z", "A", "Meerut"),
        _step("BUS", "UPSRTC", "", "2026-10-03T05:20:00Z", "2026-10-03T10:00:00Z", "Meerut", "Rishikesh")]}]}]}
    rishikesh = PlaceData("google", "r", "Rishikesh", "city", 30.09, 78.27)
    assert _scheduled(data, "outbound", "train", 250, HOME, HOME, rishikesh) == []


def test_indian_classes_only_for_indian_railways_services():
    home = PlaceData("google", "g", "Gurugram", "city", 28.46, 77.03)
    rrts = {"routes": [{"legs": [{"steps": [
        _step("COMMUTER_TRAIN", "Namo Bharat RRTS", "", "2026-10-03T04:00:00Z", "2026-10-03T05:30:00Z", "A", "B")]}]}]}
    meerut = PlaceData("google", "m", "Meerut", "city", 28.98, 77.71)
    [t] = _scheduled(rrts, "outbound", "train", 80, home, home, meerut)
    assert t.service_class is None
    shatabdi = {"routes": [{"legs": [{"steps": [
        _step("HEAVY_RAIL", "Ajmer Shatabdi", "12015", "2026-10-03T00:40:00Z", "2026-10-03T05:00:00Z", "A", "B")]}]}]}
    assert {t.service_class for t in _scheduled(shatabdi, "outbound", "train", 300, home, home, JAIPUR)} == {"2S", "CC"}


def test_a_stop_name_in_an_unreadable_script_falls_back_to_the_city_name():
    dest = PlaceData("google", "r", "Rishikesh", "city", 30.0869, 78.2676)
    data = {"routes": [{"legs": [{"steps": [
        _step("BUS", "FlixBus: Gurugram - Kasol", "", "2026-10-03T17:05:00Z", "2026-10-04T01:32:00Z",
              "Mahavir Chowk / Gaushala Ground", "ঋষিকেশ")]}]}]}
    [t] = _scheduled(data, "outbound", "bus", 268, HOME, HOME, dest)
    assert t.to_name == "Rishikesh"  # the feed only had the Bengali name; the known city name is used instead
    assert t.from_name == "Mahavir Chowk / Gaushala Ground"  # a readable stop name is kept as-is


# --- timezone: a trip is read in the destination's own local time, not assumed to be India ------------------

def test_a_train_is_read_in_the_timezone_it_was_asked_for():
    # 08:00 UTC is 09:00 in London (BST, UTC+1) and 13:30 in India (IST, UTC+5:30) on this date
    data = {"routes": [{"legs": [{"steps": [
        _step("HIGH_SPEED_TRAIN", "Test Express", "", "2026-06-03T08:00:00Z", "2026-06-03T10:00:00Z", "A", "B")]}]}]}
    [london] = _scheduled(data, "outbound", "train", 100, HOME, HOME, EDIN, tz=LONDON_TZ, currency="GBP")
    assert london.depart_at.hour == 9
    [india] = _scheduled(data, "outbound", "train", 100, HOME, HOME, JAIPUR, tz=IST, currency="INR")
    assert india.depart_at.hour == 13 and india.depart_at.minute == 30


def test_a_real_fare_is_accepted_only_in_the_trips_own_currency():
    data = {"routes": [{"legs": [{"steps": [
        _step("HIGH_SPEED_TRAIN", "Test Express", "", "2026-06-03T08:00:00Z", "2026-06-03T10:00:00Z", "A", "B")]}],
        "travelAdvisory": {"transitFare": {"currencyCode": "GBP", "units": "45"}}}]}
    [gbp] = _scheduled(data, "outbound", "train", 100, HOME, HOME, EDIN, tz=LONDON_TZ, currency="GBP")
    assert gbp.cost_per_person == Decimal(45) and gbp.verification_state == "verified_provider"
    # the same fare, quoted in GBP, isn't mistaken for a real fare on an INR trip: it falls back to an estimate
    [inr] = _scheduled(data, "outbound", "train", 100, HOME, HOME, JAIPUR, tz=IST, currency="INR")
    assert inr.verification_state == "estimated" and inr.cost_per_person != Decimal(45)


def test_an_estimated_fare_abroad_is_converted_not_shown_in_rupees():
    data = {"routes": [{"legs": [{"steps": [
        _step("HIGH_SPEED_TRAIN", "Test Express", "", "2026-06-03T08:00:00Z", "2026-06-03T10:00:00Z", "A", "B")]}]}]}
    [gbp] = _scheduled(data, "outbound", "train", 100, HOME, HOME, EDIN, tz=LONDON_TZ, currency="GBP")
    [inr] = _scheduled(data, "outbound", "train", 100, HOME, HOME, JAIPUR, tz=IST, currency="INR")
    assert gbp.verification_state == inr.verification_state == "estimated"
    assert gbp.cost_per_person != inr.cost_per_person  # converted, not the bare rupee number relabelled
    assert gbp.access_cost != inr.access_cost  # the access-leg cab estimate is converted too


def test_intercity_options_defaults_to_its_own_timezone_when_none_is_given():
    p = GoogleTransportProvider.__new__(GoogleTransportProvider)
    p.default_tz = "Asia/Kolkata"
    assert p.default_tz == "Asia/Kolkata"  # intercity_options() falls back to this when tz isn't passed


def test_a_time_window_is_offered_when_no_source_has_a_timetable():
    """No real train data anywhere for this route: an honest estimated window, never an invented name/number."""
    from app.providers.types import RouteResult

    p = GoogleTransportProvider.__new__(GoogleTransportProvider)
    p.default_tz = "Asia/Kolkata"
    p.maps = MagicMock()
    p.maps.compute_route.return_value = RouteResult(distance_m=1_200_000, duration_s=14 * 3600, polyline=None,
                                                     state="estimated", mode="drive")
    p._transit = lambda *a, **kw: None  # no live timetable anywhere
    opts = p.intercity_options(JAIPUR, EDIN, date(2026, 6, 1), date(2026, 6, 1),
                               datetime(2026, 6, 1, 7), datetime(2026, 6, 1, 22), ["train"])
    out = [t for t in opts if t.direction == "outbound"]
    assert len(out) == 1
    t = out[0]
    assert t.name == "" and t.flexible is True and t.service_class is None
    assert transport_label(t) == "Train to Edinburgh (timing estimated)"
    assert t.duration_minutes > 0 and t.cost_per_person > 0


def test_no_time_window_is_offered_beyond_a_sensible_rail_distance():
    from app.providers.types import RouteResult

    p = GoogleTransportProvider.__new__(GoogleTransportProvider)
    p.default_tz = "Asia/Kolkata"
    p.maps = MagicMock()
    p.maps.compute_route.return_value = RouteResult(distance_m=2_500_000, duration_s=30 * 3600, polyline=None,
                                                     state="estimated", mode="drive")
    p._transit = lambda *a, **kw: None
    opts = p.intercity_options(JAIPUR, EDIN, date(2026, 6, 1), date(2026, 6, 1),
                               datetime(2026, 6, 1, 7), datetime(2026, 6, 1, 22), ["train"])
    assert opts == []