"""Local cab fares like app cabs (ADR 0018): ₹10-12 a km off-peak, surge in the rush, night charges, dearer where app
cabs don't run."""
from datetime import date, datetime
from decimal import Decimal

from app.services import fare_model as F
from app.services.engine_types import DayFrame, Leg, Stop


def _at(h, m=0):
    return datetime(2026, 11, 2, h, m)


def test_an_off_peak_ride_costs_what_an_app_cab_does():
    assert F.local_leg_cost(5000, "cab", 2) == Decimal(110)  # ₹50 + 5 km × ₹12
    assert F.local_leg_cost(1500, "cab", 2) == Decimal(80)  # the minimum fare
    assert F.local_leg_cost(5000, "cab", 6) == Decimal(220)  # two cars for six people


def test_rush_hour_and_late_night_cost_more():
    assert F.ride_factor(_at(9)) == Decimal("1.5") and F.ride_factor(_at(18, 30)) == Decimal("1.5")
    assert F.ride_factor(_at(13)) == 1 and F.ride_factor(_at(23, 30)) == Decimal("1.25")


def test_cities_without_app_cabs():
    assert F.city_cab_factor("Goa") == Decimal("1.75") and F.city_cab_factor("Baga Beach, Goa") == Decimal("1.75")
    assert F.city_cab_factor("Manali, Himachal Pradesh") == Decimal("1.4") and F.city_cab_factor("Jaipur") == 1


def _day(km_per_ride, rides, start_h=10):
    stops = []
    for n in range(rides):
        at = _at(start_h + n)
        stops.append(Stop("activity", None, f"Stop {n}", 45, arrive=at, start=at, end=_at(start_h + n, 45),
                          leg=Leg(km_per_ride * 1000, 20, "cab", Decimal(0), "estimated")))
    return DayFrame(0, date(2026, 11, 2), _at(9), _at(21), (0, 0), "Hotel", (0, 0), "Hotel", stops)


def test_each_ride_is_priced_for_when_it_leaves():
    f = _day(5, 2, start_h=11)  # leaves 10:40 (rush) and 11:40 (not)
    F.price_rides([f], 2, "Jaipur")
    assert [s.leg.cost for s in f.stops] == [Decimal(165), Decimal(110)]
    F.price_rides([f], 2, "Jaipur")  # run again on the same day: the same prices, never compounded
    assert [s.leg.cost for s in f.stops] == [Decimal(165), Decimal(110)]


def test_goa_rides_cost_its_taxi_rates_and_walks_stay_free():
    f = _day(10, 1, start_h=14)  # leaves 13:40: no rush
    f.final_leg = Leg(400, 5, "walk", Decimal(0), "estimated")
    F.price_rides([f], 2, "Goa")
    assert f.stops[0].leg.cost == Decimal(298)  # (₹50 + 10 km × ₹12) × 1.75
    assert f.final_leg.cost == 0


def test_a_goa_plan_fits_the_budget_it_was_built_for(client):
    """The scheduler prices rides the way the budget will (the city's taxi rates, the rush allowed for), so it builds
    a Goa day that fits. When it planned at big-city fares, the rides came out dearer than it had allowed: a plan with
    pinned choices went over budget (₹49,126 → ₹59,190 after "make it cheaper"), one without was refused."""
    from tests.conftest import register
    from tests.test_scenarios import _payload, _use
    user = register(client)
    for budget in (30000, 45000):
        sc = dict(o="Mumbai", d="Goa", days=4, start="08:00", end="22:00", budget=budget, n=1, modes=["train", "cab"],
                  food="both", styles=["relaxed"], stay="tier2", tt="normal")
        _use(sc)
        trip_id = client.post("/api/trips", headers=user, json=_payload(sc)).json()["id"]
        out = client.post(f"/api/trips/{trip_id}/plan", headers=user).json()
        assert out["status"] == "ready", (budget, out.get("feasibility"))
        b = out["trip"]["itinerary"]["budget"]
        assert b["status"] != "over" and b["planned"] <= budget, (budget, b["planned"], b["status"])
