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
        return {"day": day.isoformat(), "seen": "2026-10-16",
                "data": {"best_flights": [ONE_STOP if day == date(2026, 11, 7) else DIRECT_HOME], "other_flights": []}}

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


def test_one_search_per_route_and_weekday_answers_the_same_weekday_another_week(monkeypatch):
    """Searched for Saturday 7 Nov, a trip on Saturday 14 Nov reuses it: the times move a week, the price becomes an
    estimate ("for 7 Nov"), and the plan says to check the flight runs that day. One API call, not two."""
    from app.core.config import get_settings
    from app.providers import cache as C
    calls = []

    class R:
        def json(self):
            return {"best_flights": [ONE_STOP], "other_flights": []}

    def request(method, url, **kw):
        calls.append(kw["params"]["outbound_date"])
        return R()

    store = {}

    def cached(name, key, ttl, fetch):
        k = C.cache_key(name, *key)
        if k not in store:
            store[k] = fetch()
        return store[k]
    monkeypatch.setattr(GF, "cached", cached)
    monkeypatch.setattr(GF, "peek", lambda name, key: store.get(C.cache_key(name, *key)))
    monkeypatch.setattr(GF.http, "request", request)
    monkeypatch.setattr(get_settings(), "serpapi_api_key", "test-key")
    first = GF._search(["IXR"], ["LHR"], date(2026, 11, 7), "INR", "in")
    again = GF._search(["IXR"], ["LHR"], date(2026, 11, 14), "INR", "in")
    assert calls == ["2026-11-07"] and again == first
    t = GF.journey(ONE_STOP, "outbound", RANCHI, "INR", shift_days=7, searched="2026-11-07", seen="2026-10-16")
    assert t.depart_at == datetime(2026, 11, 14, 6, 10) and t.verification_state == "estimated"
    assert t.meta["schedule_from"] == "2026-11-07" and t.meta["fare_label"] == "Google Flights price for 7 Nov"
    GF._search(["IXR"], ["LHR"], date(2026, 11, 8), "INR", "in")  # a Sunday: a search of its own
    assert calls == ["2026-11-07", "2026-11-08"]


def test_errors_are_never_cached_and_the_saved_schedule_answers_when_serpapi_cannot(monkeypatch):
    """Like a saved rail timetable: every real search is also kept 60 days. Out of searches (or down), the plan uses
    the saved schedule for the same weekday, its price an estimate "(saved)"; an error answer is never cached."""
    from app.core.config import get_settings
    from app.providers import cache as C
    store, kept = {}, {}

    def cached(name, key, ttl, fetch):
        k = C.cache_key(name, *key)
        if k not in store:
            store[k] = fetch()  # a raising fetch leaves nothing behind
        return store[k]
    monkeypatch.setattr(GF, "cached", cached)
    monkeypatch.setattr(GF, "peek", lambda name, key: (store if name == "serpapi-flights" else kept).get(C.cache_key(name, *key)))
    monkeypatch.setattr(GF, "store", lambda name, key, value, ttl: kept.__setitem__(C.cache_key(name, *key), value))
    monkeypatch.setattr(get_settings(), "serpapi_api_key", "test-key")

    answers = iter([{"best_flights": [ONE_STOP]}, {"error": "Your account has run out of searches."}])

    class R:
        def json(self):
            return next(answers)
    monkeypatch.setattr(GF.http, "request", lambda *a, **k: R())

    from app.providers.airports import nearest_airports
    oc = [a[0] for a in nearest_airports(RANCHI.lat, RANCHI.lng, 2)]
    dc = [a[0] for a in nearest_airports(LONDON.lat, LONDON.lng, 2)]
    GF._search(oc, dc, date(2026, 11, 7), "INR", "in")  # a real answer: cached and saved
    store.clear()  # a week later the cache has gone, the saved copy hasn't
    later = GF._search(oc, dc, date(2026, 11, 7), "INR", "in")  # SerpApi: out of searches
    assert later["saved"] and later["day"] == "2026-11-07" and not store  # the error wasn't cached
    out = GF.flights(RANCHI, LONDON, date(2026, 11, 7), date(2026, 11, 12), "INR")  # no saved return: outbound only
    assert out and all(t.verification_state == "estimated" and t.meta["fare_label"].endswith("(saved)") for t in out)
