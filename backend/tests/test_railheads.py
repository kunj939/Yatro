"""A place with no railway station (Mussoorie) is reached by train to its railhead (Dehradun), then a road transfer.
And a misspelt destination is resolved to the place meant (Masoorie -> Mussoorie), not a similarly-spelt village."""
from datetime import date
from unittest.mock import MagicMock

from app.providers.indian_rail import IndianRailProvider
from app.providers.rail_common import find_stations, resolve_station_point
from app.providers.types import PlaceData

GURUGRAM = PlaceData("google", "g", "Gurugram", "city", 28.4595, 77.0266)
MUSSOORIE = PlaceData("google", "m", "Mussoorie", "city", 30.4598, 78.0644)


class Places:
    """Geocodes stations to their real positions."""
    POINTS = {"DDN": (30.3165, 78.0322), "NDLS": (28.6428, 77.2197), "GGN": (28.4643, 77.0289)}

    def resolve(self, text, place_id=None):
        for code, (lat, lng) in self.POINTS.items():
            if code in text:
                return PlaceData("google", code, text, "station", lat, lng)
        return None


TRAINS = {"status": True, "data": [
    {"train_number": "12017", "train_name": "DEHRADUN SHATABDI", "from_std": "06:45", "to_sta": "12:20",
     "duration": "5:35", "class_type": ["CC", "EC"]}]}


def test_places_without_a_station_get_their_railhead():
    places = MagicMock()
    places.resolve.return_value = None
    mussoorie = find_stations(places, MUSSOORIE, lambda q: [], "test")
    assert mussoorie[0] == ("DDN", "Dehradun") and ("HW", "Haridwar Jn") in mussoorie  # then the next big one
    nainital = PlaceData("google", "n", "Nainital", "city", 29.39, 79.45)
    assert find_stations(places, nainital, lambda q: [], "test")[0] == ("KGM", "Kathgodam")


def test_a_railhead_that_is_far_from_the_place_is_kept_but_a_wrong_match_still_is_not():
    places = Places()
    # Dehradun is ~35 km from Mussoorie: fine even under the default 40 km limit, and under the railhead limit
    assert resolve_station_point(places, "DDN", "Dehradun", MUSSOORIE, "t", max_km=120) is not None
    # a station 1,600 km away (the Goreswar mistake) is refused under either limit
    far = PlaceData("google", "x", "Elsewhere", "city", 26.2, 91.7)
    assert resolve_station_point(places, "DDN", "Dehradun", far, "t", max_km=120) is None


def test_a_train_to_the_railhead_is_offered_for_a_hill_station(monkeypatch):
    rail = IndianRailProvider(Places(), "k", "irctc1.p.rapidapi.com", store=False)
    calls = []

    def fake_get(path, params, ttl):
        calls.append((params.get("fromStationCode"), params.get("toStationCode")))
        return TRAINS if params.get("toStationCode") == "DDN" else {"status": True, "data": []}
    monkeypatch.setattr(rail, "_get", fake_get)
    out = rail.intercity(GURUGRAM, MUSSOORIE, date(2026, 10, 3), date(2026, 10, 4))
    outbound = [t for t in out if t.direction == "outbound"]
    assert outbound and all(t.to_name == "Dehradun (DDN)" for t in outbound)
    assert outbound[0].name == "Dehradun Shatabdi"
    # arriving at Dehradun, the traveller is ~35 km from Mussoorie: that is the plan's first (cab) leg, so the
    # arrival point must be the station itself, not Mussoorie
    assert abs(outbound[0].to_lat - 30.3165) < 0.01
    assert any(c[1] == "DDN" for c in calls)  # the search for trains went to the railhead