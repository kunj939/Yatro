"""Any place is served by the big stations near it: a train to/from those, then the rest by road."""
from datetime import date
from unittest.mock import MagicMock

from app.providers.indian_rail import IndianRailProvider
from app.providers.rail_common import find_stations, station_pairs, transfer_notes
from app.providers.rail_stations import STATIONS, nearest_stations, station_position
from app.providers.types import PlaceData


def place(name, lat, lng):
    return PlaceData("google", name, name, "city", lat, lng)


GURUGRAM = place("Gurugram", 28.4595, 77.0266)
MUSSOORIE = place("Mussoorie", 30.4598, 78.0644)


def test_a_big_hub_is_worth_a_longer_ride_than_a_small_station():
    near = [code for code, _, _ in nearest_stations(GURUGRAM.lat, GURUGRAM.lng, limit=4)]
    # Faridabad is a small station: the big Delhi stations come first even though they are a little further
    assert near.index("NDLS") < near.index("FDB") if "FDB" in near else True
    assert near[0] in {"NZM", "NDLS", "DLI", "GGN"}


def test_an_unlisted_place_still_gets_stations_from_the_gazetteer():
    kausani = place("Kausani", 29.8430, 79.6020)  # a hill town nobody hand-listed
    got = find_stations(MagicMock(resolve=MagicMock(return_value=None)), kausani, lambda q: [], "t")
    assert got and got[0][0] == "KGM"  # Kathgodam is the railhead
    remote = place("Middle of the sea", 5.0, 70.0)
    assert find_stations(MagicMock(resolve=MagicMock(return_value=None)), remote, lambda q: [], "t") == []


def test_every_station_has_a_position_inside_india_and_a_valid_tier():
    for code, (name, lat, lng, tier) in STATIONS.items():
        assert 6 <= lat <= 36 and 68 <= lng <= 98, code
        assert tier in (1, 2, 3), code
    assert station_position("DDN") == (30.3165, 78.0322) and station_position("NOPE") is None


def test_pairs_are_tried_own_stations_first_then_the_start_then_the_destination():
    o = [("A0", "a0"), ("A1", "a1"), ("A2", "a2")]
    d = [("B0", "b0"), ("B1", "b1"), ("B2", "b2")]
    got = [(x[0], y[0]) for x, y in station_pairs(o, d)]
    assert got == [("A0", "B0"), ("A1", "B0"), ("A0", "B1"), ("A2", "B0"), ("A1", "B1")]  # capped at 5 calls
    assert station_pairs([], d) == []


def test_transfer_notes_say_where_the_road_part_is():
    notes = transfer_notes(GURUGRAM, MUSSOORIE, (28.6428, 77.2197), (30.3165, 78.0322), "New Delhi (NDLS)",
                           "Dehradun (DDN)")
    assert len(notes) == 2
    assert "New Delhi (NDLS)" in notes[0] and "Gurugram" in notes[0]
    assert "Dehradun (DDN)" in notes[1] and "Mussoorie" in notes[1] and "taxi or bus" in notes[1]
    # a train that starts and ends in the place itself needs no note
    assert transfer_notes(GURUGRAM, GURUGRAM, (28.4643, 77.0289), (28.4643, 77.0289), "x", "y") == []


class Places:
    def resolve(self, text, place_id=None):
        return None


def test_gurugram_to_mussoorie_is_new_delhi_to_dehradun_then_a_road_transfer(monkeypatch):
    """No direct train Gurugram -> Mussoorie exists (neither has the other's station): the plan uses New Delhi
    and Dehradun, and says the ends are done by road."""
    rail = IndianRailProvider(Places(), "k", "irctc1.p.rapidapi.com", store=False)
    calls = []
    trains = {"status": True, "data": [
        {"train_number": "12017", "train_name": "DEHRADUN SHATABDI", "from_std": "06:45", "to_sta": "12:20",
         "duration": "5:35", "class_type": ["CC", "EC"]}]}

    def fake_get(path, params, ttl):
        if "searchStation" in path:
            return {"status": True, "data": []}
        pair = (params.get("fromStationCode"), params.get("toStationCode"))
        calls.append(pair)
        return trains if pair == ("NDLS", "DDN") else {"status": True, "data": []}
    monkeypatch.setattr(rail, "_get", fake_get)
    out = rail.intercity(GURUGRAM, MUSSOORIE, date(2026, 10, 3), date(2026, 10, 4))
    outbound = [t for t in out if t.direction == "outbound"]
    assert outbound and all(t.from_name == "New Delhi (NDLS)" and t.to_name == "Dehradun (DDN)" for t in outbound)
    assert calls[:2] == [("GGN", "DDN"), ("NDLS", "DDN")]  # own station first, then the bigger one near the start
    notes = outbound[0].meta["transfer_notes"]
    assert any("ride to that station" in n for n in notes) and any("taxi or bus" in n for n in notes)
    # the plan starts from the real station, so the road leg to Mussoorie is part of the day
    assert abs(outbound[0].to_lat - 30.3165) < 0.01


def test_when_nothing_has_trains_every_station_pair_tried_is_named(monkeypatch):
    rail = IndianRailProvider(Places(), "k", "irctc1.p.rapidapi.com", store=False)
    monkeypatch.setattr(rail, "_get", lambda path, params, ttl: {"status": True, "data": []})
    assert rail.intercity(GURUGRAM, MUSSOORIE, date(2026, 10, 3), date(2026, 10, 3)) == []
    assert "Gurgaon (GGN) to Dehradun (DDN)" in rail.last_issue and "New Delhi (NDLS) to Dehradun (DDN)" in rail.last_issue