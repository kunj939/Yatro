"""Indian Railways timetable parsing (IRCTC1 on RapidAPI), with the network mocked."""
import pytest
from datetime import date

from app.providers.indian_rail import IndianRailProvider
from app.providers.rail_chain import RailAwareTransport
from app.providers.types import PlaceData
from app.services.feasibility import transport_label

GGN = PlaceData("google", "g", "Gurugram", "city", 28.4595, 77.0266)
JP = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)
# A city deliberately absent from rail_common.KNOWN_STATIONS, so tests below still exercise the live
# station-search path (GGN/JP are known cities and are resolved without any network call at all).
FAKE = PlaceData("google", "f", "Faketown", "city", 25.0, 80.0)

TRAINS = {"status": True, "data": [
    {"train_number": "12015", "train_name": "AJMER SHATABDI", "from": "ZZA", "to": "ZZB", "from_std": "06:45", "to_sta": "11:00",
     "duration": "4:15", "class_type": ["CC", "EC"]},
    {"train_number": "22986", "train_name": "DEE UDZ RAJ HUMSAFAR", "from": "ZZA", "to": "ZZB", "from_std": "23:30", "to_sta": "04:40",
     "class_type": ["3A"]},
    {"train_number": "", "train_name": "", "from_std": ""},  # incomplete rows are skipped
]}


class NoPlaces:
    def resolve(self, *a):
        raise RuntimeError("offline")


def _provider(monkeypatch):
    p = IndianRailProvider(NoPlaces(), "test-key", "irctc1.p.rapidapi.com", store=False)

    def fake_get(path, params, ttl):
        if "searchStation" in path:
            return {"status": True, "data": [{"name": "FAKETOWN JN", "code": "FKT"}]}
        return TRAINS
    monkeypatch.setattr(p, "_get", fake_get)
    return p


def test_well_known_cities_resolve_without_any_network_call(monkeypatch):
    rail = _provider(monkeypatch)

    def boom(*a):
        raise AssertionError("a known city should never need a live station search")
    monkeypatch.setattr(rail, "_get", lambda path, params, ttl: TRAINS if "searchStation" not in path else boom())
    assert rail.station_codes(GGN) == [("GGN", "Gurgaon"), ("NDLS", "New Delhi"), ("NZM", "Hazrat Nizamuddin")]
    assert rail.station_codes(JP) == [("JP", "Jaipur Jn")]  # Jaipur has no configured nearby-hub fallback


def test_trains_come_from_the_timetable(monkeypatch):
    opts = _provider(monkeypatch).intercity(GGN, JP, date(2026, 10, 3), date(2026, 10, 3))
    out = [t for t in opts if t.direction == "outbound"]
    shatabdi = [t for t in out if t.meta["number"] == "12015"]
    assert {t.service_class for t in shatabdi} == {"CC"}  # no budget class on a Shatabdi: CC only
    t = shatabdi[0]
    assert t.name == "Ajmer Shatabdi" and t.depart_at.hour == 6 and t.duration_minutes == 255
    assert transport_label(t).startswith("Train: Ajmer Shatabdi 12015")
    assert t.from_name == "Gurgaon (GGN)" and t.verification_state == "estimated"  # fare is an estimate
    night = [t for t in out if t.meta["number"] == "22986"][0]
    assert night.arrive_at.day == 4 and night.service_class == "3A"  # overnight arrival rolls to the next day


def test_rail_aware_transport_only_used_inside_india(monkeypatch):
    rail = _provider(monkeypatch)

    class Base:
        name = "google-transport"

        def intercity_options(self, o, d, dd, rd, ws, we, modes, *a):
            return [("base", tuple(modes))]
    from app.providers.rail_chain import RailChain
    t = RailAwareTransport(Base(), RailChain([rail]))
    got = t.intercity_options(GGN, JP, date(2026, 10, 3), date(2026, 10, 3), None, None, ["train", "cab"])
    assert got[0] == ("base", ("cab",)) and any(getattr(x, "mode", "") == "train" for x in got)
    london = PlaceData("google", "l", "London", "city", 51.5, -0.12)
    assert t.intercity_options(london, JP, None, None, None, None, ["train"]) == [("base", ("train",))]


def test_no_cab_is_added_when_only_train_was_chosen(monkeypatch):
    rail = _provider(monkeypatch)
    calls = []

    class Base:
        name = "google-transport"

        def intercity_options(self, *a):
            calls.append(a)
            return []
    from app.providers.rail_chain import RailChain
    t = RailAwareTransport(Base(), RailChain([rail]))
    got = t.intercity_options(GGN, JP, date(2026, 10, 3), date(2026, 10, 3), None, None, ["train"])
    assert not calls and got and all(x.mode == "train" for x in got)


def test_reason_is_kept_when_no_station_is_found(monkeypatch):
    rail = _provider(monkeypatch)
    monkeypatch.setattr(rail, "_get", lambda path, params, ttl: {"status": True, "data": []})
    assert rail.intercity(FAKE, JP, date(2026, 10, 3), date(2026, 10, 3)) == []
    assert rail.last_issue == "no railway station was found for Faketown"


def test_station_names_are_matched_to_the_right_city():
    from app.providers.rail_common import name_score
    assert name_score("GORESWAR", "Gurugram") == 0 and name_score("GORESWAR", "Gurgaon") == 0
    assert name_score("GURGAON", "Gurgaon") == 3
    assert name_score("JAIPUR JN", "Jaipur") > name_score("GANDHINAGAR JAIPUR", "Jaipur") > 0
    assert name_score("NEW DELHI", "New Delhi") == 3


def test_fuzzy_search_results_for_other_places_are_ignored(monkeypatch):
    rail = _provider(monkeypatch)
    fuzzy = {"status": True, "data": [{"name": "GORESWAR", "code": "GVR"}, {"name": "FAKETOWN", "code": "FKT"}]}
    monkeypatch.setattr(rail, "_get", lambda path, params, ttl: fuzzy)
    assert rail.station_codes(FAKE)[0][0] == "FKT"


def test_one_station_search_per_city_when_the_match_is_clear(monkeypatch):
    rail = _provider(monkeypatch)
    queries = []

    def fake_get(path, params, ttl):
        queries.append(params.get("query"))
        return {"status": True, "data": [{"name": "FAKETOWN", "code": "FKT"}]}
    monkeypatch.setattr(rail, "_get", fake_get)
    assert rail.station_codes(FAKE)[0][0] == "FKT"
    assert queries == ["Faketown"]  # a clear match on the first search: no second call


def test_quota_message_is_specific(monkeypatch):
    from app.core.errors import ProviderUnavailable
    rail = _provider(monkeypatch)

    def boom(path, params, ttl):
        rail.quota_used_up = True
        raise ProviderUnavailable("quota")
    monkeypatch.setattr(rail, "_get", boom)
    assert rail.intercity(GGN, JP, date(2026, 10, 3), date(2026, 10, 3)) == []
    assert "quota" in rail.last_issue and "used up" in rail.last_issue


@pytest.fixture
def irctc1_storage_allowed():
    """IRCTC1 may not store anything by default (I7, ADR 0003); these tests check the mechanism when it is allowed."""
    from app.core.config import get_settings
    s = get_settings()
    before = s.rail_storage_sources
    s.rail_storage_sources = "railradar,irctc1"
    yield
    s.rail_storage_sources = before


def test_irctc1_stores_nothing_until_its_terms_allow_it(monkeypatch):
    rail = IndianRailProvider(NoPlaces(), "k", "irctc1.p.rapidapi.com", store=True)
    assert rail.cache.enabled is False
    calls = []
    monkeypatch.setattr(rail, "_get", lambda path, params, ttl: calls.append(path) or TRAINS)
    rail.trains("ZZC", "ZZD", date(2026, 10, 3))
    rail.trains("ZZC", "ZZD", date(2026, 10, 10))
    assert calls.count("/api/v3/trainBetweenStations") == 2  # asked live both times: nothing was kept


def test_saved_timetable_is_reused_for_the_same_weekday(monkeypatch, irctc1_storage_allowed):
    from datetime import timedelta
    rail = IndianRailProvider(NoPlaces(), "k", "irctc1.p.rapidapi.com", store=True)
    calls = []

    def fake_get(path, params, ttl):
        calls.append(path)
        return TRAINS
    monkeypatch.setattr(rail, "_get", fake_get)
    sat = date(2026, 10, 3)
    first = rail.trains("ZZA", "ZZB", sat)
    again = rail.trains("ZZA", "ZZB", sat + timedelta(days=7))  # next Saturday: from our own database
    other_day = rail.trains("ZZA", "ZZB", sat + timedelta(days=1))  # Sunday: different trains run, so fetch
    # the saved copy keeps only the 2 valid trains: the incomplete row was dropped by validation (ADR 0003)
    assert len(first) == 3 and len(again) == 2 and len(other_day) == 3
    assert calls.count("/api/v3/trainBetweenStations") == 2


def test_saved_station_lookup_is_reused(monkeypatch, irctc1_storage_allowed):
    rail = IndianRailProvider(NoPlaces(), "k", "irctc1.p.rapidapi.com", store=True)
    calls = []

    def fake_get(path, params, ttl):
        calls.append(params["query"])
        return {"status": True, "data": [{"name": "TESTPUR JN", "code": "ZQX"}]}
    monkeypatch.setattr(rail, "_get", fake_get)
    place = PlaceData("google", "t", "Testpur", "city", 25.0, 80.0)
    assert rail.station_codes(place)[0][0] == "ZQX"
    assert rail.station_codes(place)[0][0] == "ZQX"
    assert calls == ["Testpur"]


AGRA = PlaceData("google", "a", "Agra", "city", 27.1767, 78.0081)


def test_a_nearby_hub_station_is_tried_when_the_origin_city_has_no_direct_train(monkeypatch):
    """Gurugram has no direct train to many places; New Delhi, a short cab ride away, usually does."""
    rail = IndianRailProvider(NoPlaces(), "k", "irctc1.p.rapidapi.com", store=False)
    calls = []

    def fake_get(path, params, ttl):
        calls.append((params.get("fromStationCode"), params.get("toStationCode")))
        if params.get("fromStationCode") == "GGN":
            return {"status": True, "data": []}  # no direct Gurgaon -> Agra train
        return TRAINS  # New Delhi -> Agra has real trains
    monkeypatch.setattr(rail, "_get", fake_get)
    out = rail.intercity(GGN, AGRA, date(2026, 10, 3), date(2026, 10, 3))
    outbound = [t for t in out if t.direction == "outbound"]
    assert outbound and outbound[0].from_name == "New Delhi (NDLS)"
    assert calls[0] == ("GGN", "AGC") and calls[1] == ("NDLS", "AGC")  # tried in order, own station first
    assert any(c[0] == "NZM" for c in calls)  # New Delhi alone offered few trains, so the next terminal was asked too


def test_when_no_station_at_all_has_a_route_the_reason_names_the_last_one_tried(monkeypatch):
    rail = IndianRailProvider(NoPlaces(), "k", "irctc1.p.rapidapi.com", store=False)
    monkeypatch.setattr(rail, "_get", lambda path, params, ttl:
                        {"status": True, "data": []} if "searchStation" not in path
                        else {"status": True, "data": [{"name": "AGRA CANTT", "code": "AGC"}]})
    out = rail.intercity(GGN, AGRA, date(2026, 10, 3), date(2026, 10, 3))
    assert out == [] and "NZM" in rail.last_issue  # the last of the 3 tried stations (GGN, NDLS, NZM)