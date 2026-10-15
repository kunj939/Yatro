"""Local public transport with real lines and stops (ADR 0019): on a plan that gets around by bus/metro, each hop is
asked of Google Routes in TRANSIT mode, and the plan names what to take, where to get on and off, and the fare."""
from datetime import date, datetime
from decimal import Decimal

from app.core.errors import ProviderUnavailable
from app.providers.google import local_transit
from app.providers.types import RouteResult
from app.services.engine_types import Candidate, DayFrame, Stop
from app.services.route_optimizer import Travel, apply_real_routes

ROUTE = {"distanceMeters": 9100, "duration": "1980s", "travelAdvisory": {"transitFare": {"currencyCode": "INR", "units": "30"}},
         "legs": [{"steps": [
             {"travelMode": "WALK"},
             {"travelMode": "TRANSIT", "transitDetails": {
                 "stopDetails": {"departureStop": {"name": "Rajiv Chowk"}, "arrivalStop": {"name": "Hauz Khas"}},
                 "transitLine": {"nameShort": "Yellow Line", "vehicle": {"type": "SUBWAY"}}, "stopCount": 7,
                 "headsign": "Millennium City Centre Gurugram"}},
             {"travelMode": "TRANSIT", "transitDetails": {
                 "stopDetails": {"departureStop": {"name": "हौज़ ख़ास"}, "arrivalStop": {"name": "IIT Gate"}},
                 "transitLine": {"name": "507", "vehicle": {"type": "BUS"}}, "stopCount": 3}},
         ]}]}


def test_a_google_transit_route_becomes_lines_and_stops():
    t = local_transit(ROUTE)
    assert t["fare"] == 30.0 and t["currency"] == "INR"
    metro, bus = t["steps"]
    assert metro == {"vehicle": "metro", "line": "Yellow Line", "from": "Rajiv Chowk", "to": "Hauz Khas", "stops": 7,
                     "headsign": "Millennium City Centre Gurugram"}
    assert bus["vehicle"] == "bus" and bus["line"] == "507" and bus["from"] is None  # unreadable script: left out
    assert local_transit({"legs": [{"steps": [{"travelMode": "WALK"}]}]}) is None  # walking only


class TransitMaps:
    """Routes in transit mode with a timetable, and drives for anything else."""
    def __init__(self, transit=True):
        self.transit, self.asked = transit, []

    def compute_route(self, a, b, mode="drive", depart_at=None):
        self.asked.append(mode)
        if mode == "transit":
            if not self.transit:
                raise ProviderUnavailable("no transit")
            return RouteResult(9100, 1980, None, "verified_provider", "transit", local_transit(ROUTE))
        return RouteResult(8000, 900, None, "verified_provider", mode)


def _frame():
    class P:
        def __init__(self, lat, lng):
            self.lat, self.lng = lat, lng
    cand = Candidate(key="k", place=P(28.55, 77.20), kind="attraction", unit_cost=Decimal(0), cost_state="estimated", duration=60)
    at = datetime(2026, 11, 3, 11, 0)
    stop = Stop("activity", cand, "Hauz Khas", 60, arrive=at, start=at, end=datetime(2026, 11, 3, 12, 0))
    return DayFrame(0, date(2026, 11, 3), datetime(2026, 11, 3, 9, 0), datetime(2026, 11, 3, 21, 0),
                    (28.63, 77.22), "Hotel", (28.63, 77.22), "Hotel", [stop])


def test_a_bus_and_metro_plan_rides_the_real_lines_at_their_fare():
    f, maps = _frame(), TransitMaps()
    travel = Travel("transit", 2)
    apply_real_routes(maps, [f], travel)
    leg = travel.real[((28.63, 77.22), (28.55, 77.20))]
    assert leg.mode == "transit" and leg.transit["steps"][0]["from"] == "Rajiv Chowk"
    assert leg.cost == Decimal(60)  # ₹30 each for two travellers
    assert leg.minutes == 33 + 5  # Google's door-to-door time, and a few minutes to find the stop


def test_without_a_timetable_the_estimate_stands_and_cab_plans_never_ask():
    f = _frame()
    travel = Travel("transit", 2)
    apply_real_routes(TransitMaps(transit=False), [f], travel)
    leg = travel.real[((28.63, 77.22), (28.55, 77.20))]
    assert leg.mode == "transit" and leg.transit is None and leg.cost == Decimal(50)  # ₹25 a person, estimated
    maps = TransitMaps()
    apply_real_routes(maps, [_frame()], Travel("cab", 2))
    assert "transit" not in maps.asked
