"""The rail chain: tries each configured Indian Railways source in order, so one being unavailable (quota,
outage) doesn't stop trains being offered, as long as another configured source has them."""
from datetime import date

from app.providers.rail_chain import RailAwareTransport, RailChain, build
from app.providers.types import PlaceData, TransportData

GGN = PlaceData("google", "g", "Gurugram", "city", 28.4595, 77.0266)
JP = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)


def _train(number: str) -> TransportData:
    return TransportData(direction="outbound", mode="train", name="Test Express", depart_at=None, arrive_at=None,
                         duration_minutes=60, cost_per_person=0, meta={"number": number})


class Source:
    """A minimal stand-in for IndianRailProvider / RailRadarProvider."""

    def __init__(self, name: str, result=None, issue=None, boom=False):
        self.name, self._result, self.last_issue, self._boom = name, result or [], issue, boom
        self.calls = 0

    def intercity(self, *a):
        self.calls += 1
        if self._boom:
            raise RuntimeError("this source is broken")
        return self._result


def test_first_source_with_trains_wins_and_the_rest_are_not_needed():
    a = Source("a", result=[_train("100")])
    b = Source("b", result=[_train("200")])
    chain = RailChain([a, b])
    out = chain.intercity(GGN, JP, date(2026, 10, 3), date(2026, 10, 3))
    assert [t.meta["number"] for t in out] == ["100"] and a.calls == 1 and b.calls == 0


def test_falls_through_to_the_next_source_when_the_first_finds_nothing():
    a = Source("a", result=[], issue="a's quota is used up")
    b = Source("b", result=[_train("200")])
    chain = RailChain([a, b])
    out = chain.intercity(GGN, JP, date(2026, 10, 3), date(2026, 10, 3))
    assert [t.meta["number"] for t in out] == ["200"] and a.calls == 1 and b.calls == 1


def test_an_unexpected_error_in_one_source_does_not_stop_the_others():
    a = Source("a", boom=True)
    b = Source("b", result=[_train("200")])
    chain = RailChain([a, b])
    out = chain.intercity(GGN, JP, date(2026, 10, 3), date(2026, 10, 3))
    assert [t.meta["number"] for t in out] == ["200"]


def test_when_every_source_is_empty_the_reasons_are_combined():
    a = Source("a", result=[], issue="no train runs between GGN and JP on these dates")
    b = Source("b", result=[], issue="RailRadar quota used up")
    chain = RailChain([a, b])
    assert chain.intercity(GGN, JP, date(2026, 10, 3), date(2026, 10, 3)) == []
    assert "GGN and JP" in chain.last_issue and "quota used up" in chain.last_issue


def test_rail_aware_transport_reports_the_chains_issue_through_last_rail_issue():
    class Base:
        name = "google-transport"

        def intercity_options(self, *a):
            return []
    chain = RailChain([Source("a", result=[], issue="a specific, useful reason")])
    t = RailAwareTransport(Base(), chain)
    t.intercity_options(GGN, JP, date(2026, 10, 3), date(2026, 10, 3), None, None, ["train"])
    assert t.last_rail_issue == "a specific, useful reason"


def test_build_still_wraps_the_base_transport_when_no_rail_key_is_configured(monkeypatch):
    # With no timetable source, Indian train journeys fall back to the station-network estimate, so the wrapper
    # is still needed; it just has no sources to try.
    from app.core.config import Settings
    monkeypatch.setattr("app.providers.rail_chain.get_settings",
                        lambda: Settings(irctc_rapidapi_key=None, railradar_api_key=None))

    class Base:
        name = "google-transport"
    base = Base()
    out = build(base, places=None)
    assert isinstance(out, RailAwareTransport) and out.base is base and out.chain.providers == []


def test_build_orders_railradar_before_irctc1_and_shares_station_search(monkeypatch):
    from app.core.config import Settings
    monkeypatch.setattr("app.providers.rail_chain.get_settings",
                        lambda: Settings(irctc_rapidapi_key="irctc-key", railradar_api_key="rr-key"))

    class Base:
        name = "google-transport"
    out = build(Base(), places="places-stub")
    assert isinstance(out, RailAwareTransport)
    assert [p.name for p in out.chain.providers] == ["railradar", "irctc1"]
    railradar, irctc1 = out.chain.providers
    assert railradar.station_search == irctc1._station_rows  # RailRadar borrows IRCTC1's station search


def test_build_uses_only_the_configured_source(monkeypatch):
    from app.core.config import Settings
    monkeypatch.setattr("app.providers.rail_chain.get_settings",
                        lambda: Settings(irctc_rapidapi_key=None, railradar_api_key="rr-key"))

    class Base:
        name = "google-transport"
    out = build(Base(), places="places-stub")
    assert [p.name for p in out.chain.providers] == ["railradar"]
    assert out.chain.providers[0].station_search is None  # no IRCTC1 configured to borrow from

# --- When no timetable is available at all -------------------------------------------------------------------
RISHIKESH = PlaceData("google", "r", "Rishikesh", "city", 30.0869, 78.2676)


class _NoTrainsBase:
    name = "google-transport"

    def intercity_options(self, *a):
        return []


def _no_timetable_options(chain):
    from datetime import datetime
    t = RailAwareTransport(_NoTrainsBase(), chain)
    return t.intercity_options(GGN, RISHIKESH, date(2026, 10, 10), date(2026, 10, 11),
                               datetime(2026, 10, 10, 6, 0), datetime(2026, 10, 11, 22, 0), ["train", "cab"])


def test_no_timetable_still_offers_a_train_routed_through_the_real_stations():
    # Gurugram -> Rishikesh: ride to a big Delhi station, train to Haridwar (the better-served railhead for
    # Rishikesh), never a "train" from Gurugram's own coordinates and never a named train.
    for chain in (RailChain([]), RailChain([Source("down", result=[], issue="quota used up")])):
        out = _no_timetable_options(chain)
        assert out and all(t.mode == "train" and t.flexible and t.name == "" for t in out)
        first = next(t for t in out if t.direction == "outbound")
        assert first.from_name.endswith(("(NDLS)", "(NZM)")) and first.to_name == "Haridwar Jn (HW)"
        assert first.access_distance_m > 20_000 and first.meta["estimated_rail"]


def test_metro_replaces_the_cab_to_the_station_when_the_traveller_did_not_choose_cabs():
    from types import SimpleNamespace
    from app.services import feasibility as FE
    from app.services import planning_support as PS
    out = _no_timetable_options(RailChain([]))
    cab_out = next(t for t in out if t.direction == "outbound")
    _, cab_cost = FE.group_cost(cab_out, 2)
    leave_home = cab_out.depart_at.timestamp() - cab_out.access_minutes * 60
    PS.choose_station_access(out, SimpleNamespace(transport_modes=["train", "metro"], styles=["balanced"],
                                                  currency="INR"), GGN)
    assert cab_out.meta["access_mode"] == "transit"
    _, metro_cost = FE.group_cost(cab_out, 2)
    assert metro_cost < cab_cost  # two metro tickets, not a cab
    assert cab_out.depart_at.timestamp() - cab_out.access_minutes * 60 == leave_home  # still leaves home on time
