"""RailRadar timetable parsing, with the network mocked. RailRadar gives real train names, numbers, timing
and distance; it doesn't give fare or seat class, so those are estimated here (never the train itself)."""
from datetime import date

from app.providers.railradar import RailRadarProvider
from app.providers.types import PlaceData

UJN = PlaceData("google", "u", "Ujjain", "city", 23.1793, 75.7849)
INDB = PlaceData("google", "i", "Indore", "city", 22.7196, 75.8577)

BETWEEN = {"success": True, "data": {
    "from": {"code": "UJN", "name": "Ujjain Junction"}, "to": {"code": "INDB", "name": "Indore Junction"},
    "count": 2, "trains": [
        {"train": {"number": "69211", "name": "Ujjain - Indore Passenger", "type": "Passenger",
                   "runDays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
         "from": {"departure": "05:30", "day": 0, "sequence": 1},
         "to": {"arrival": "07:55", "day": 1, "sequence": 11},
         "distance": 62.2, "duration": 115, "totalHaltsBetween": 6},
        {"train": {"number": "12920", "name": "Malwa Express", "type": "Superfast Express", "runDays": ["daily"]},
         "from": {"departure": "22:15", "day": 0, "sequence": 1},
         "to": {"arrival": "23:40", "day": 0, "sequence": 4},
         "distance": 78.0, "duration": 85, "totalHaltsBetween": 2},
        {"train": {"number": "", "name": "", "type": ""}, "from": {}, "to": {}},  # incomplete: skipped
    ]}}


class NoPlaces:
    def resolve(self, *a):
        raise RuntimeError("offline")


def _provider(monkeypatch, station_search=None):
    p = RailRadarProvider(NoPlaces(), "rr_live_test", store=False, station_search=station_search)
    monkeypatch.setattr(p, "_get", lambda path, params, ttl: BETWEEN)
    return p


def test_real_trains_with_estimated_fare_and_class(monkeypatch):
    def stations(query):
        return [{"name": "UJJAIN JN", "code": "UJN"}] if "jjain" in query.lower() else [
            {"name": "INDORE JN BG", "code": "INDB"}]
    opts = _provider(monkeypatch, station_search=stations).intercity(UJN, INDB, date(2026, 6, 22), date(2026, 6, 22))
    out = [t for t in opts if t.direction == "outbound"]
    passenger = [t for t in out if t.meta["number"] == "69211"][0]
    assert passenger.name == "Ujjain - Indore Passenger" and passenger.service_class == "2S"  # passenger: 2S only
    assert passenger.depart_at.hour == 5 and passenger.depart_at.minute == 30
    assert passenger.duration_minutes == 115  # RailRadar's own duration is used, not a day/arrival reconstruction
    assert passenger.distance_m == 62200  # RailRadar's own per-leg distance, not a road estimate
    assert passenger.verification_state == "estimated"
    assert "fare and class estimated" in passenger.source

    superfast = {t.service_class for t in out if t.meta["number"] == "12920"}
    assert superfast == {"2S", "CC"}  # ordinary superfast: one budget class, one AC class


def test_named_ac_only_trains_get_sensible_classes():
    from datetime import timedelta

    from app.providers.rail_common import guess_classes
    assert guess_classes("Vande Bharat Express", timedelta(hours=6)) == ["CC"]
    assert guess_classes("Rajdhani Express", timedelta(hours=14)) == ["3A", "2A"]
    assert guess_classes("Garib Rath Express", timedelta(hours=10)) == ["3E"]
    assert guess_classes("Passenger", timedelta(hours=2)) == ["2S"]


def test_no_station_search_means_no_stations_for_unknown_cities(monkeypatch):
    rail = _provider(monkeypatch, station_search=None)
    fictional = PlaceData("google", "x", "Nowheresville", "city", 24.0, 79.0)
    assert rail.intercity(fictional, INDB, date(2026, 6, 22), date(2026, 6, 22)) == []
    assert rail.last_issue == "no railway station was found for Nowheresville"


def test_known_cities_need_no_station_search_callable(monkeypatch):
    # Jaipur is in rail_common.KNOWN_STATIONS, so RailRadar can resolve it even with no IRCTC1 fallback
    jaipur = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)
    rail = _provider(monkeypatch, station_search=None)
    assert rail.station_codes(jaipur) == [("JP", "Jaipur Jn")]


def test_incomplete_rows_are_skipped_not_fabricated(monkeypatch):
    rail = _provider(monkeypatch, station_search=lambda q: [{"name": "UJJAIN JN", "code": "UJN"}])
    out = rail._options(BETWEEN["data"]["trains"], "outbound", date(2026, 6, 22), UJN, (23.18, 75.78),
                        (22.72, 75.86), "Ujjain Jn (UJN)", "Indore Jn (INDB)")
    numbers = {t.meta["number"] for t in out}
    assert numbers == {"69211", "12920"}  # the two real trains; the blank row produced nothing


def test_quota_exhaustion_is_reported_distinctly(monkeypatch):
    from app.core.errors import ProviderUnavailable
    rail = RailRadarProvider(NoPlaces(), "rr_live_test", store=False,
                             station_search=lambda q: [{"name": "UJJAIN JN", "code": "UJN"}])

    def boom(path, params, ttl):
        rail.quota_used_up = True
        raise ProviderUnavailable("quota")
    monkeypatch.setattr(rail, "_get", boom)
    assert rail.intercity(UJN, INDB, date(2026, 6, 22), date(2026, 6, 22)) == []
    assert "quota" in rail.last_issue and "used up" in rail.last_issue


def test_a_nearby_hub_station_is_tried_when_the_origin_city_has_no_direct_train(monkeypatch):
    """Gurugram has no direct train to many places; New Delhi, a short cab ride away, usually does."""
    gurugram = PlaceData("google", "g", "Gurugram", "city", 28.4595, 77.0266)
    agra = PlaceData("google", "a", "Agra", "city", 27.1767, 78.0081)
    rail = RailRadarProvider(NoPlaces(), "rr_live_test", store=False)
    calls = []

    def fake_get(path, params, ttl):
        calls.append(path)
        if path == "/trains/between/GGN/AGC":
            return {"success": True, "data": {"trains": []}}
        return BETWEEN
    monkeypatch.setattr(rail, "_get", fake_get)
    out = rail.intercity(gurugram, agra, date(2026, 10, 3), date(2026, 10, 3))
    outbound = [t for t in out if t.direction == "outbound"]
    assert outbound and outbound[0].from_name.startswith("New Delhi")  # the nearest hub that has trains comes first
    assert calls[0] == "/trains/between/GGN/AGC" and calls[1] == "/trains/between/NDLS/AGC"
    assert "/trains/between/NZM/AGC" in calls  # New Delhi alone offered few trains, so the next terminal was asked too