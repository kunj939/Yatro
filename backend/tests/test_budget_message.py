"""Regression for a real refusal (Delhi -> Istanbul, 8 days, budget ₺1,500): "Your transport preference (train, cab,
flight, bus, metro) is the main reason ... No train or bus timetable was available for this route, so only road options
were priced." Every mode was allowed, the cheapest way was a flight, and no train or bus runs that far anyway."""
from datetime import datetime
from decimal import Decimal

from app.services import feasibility as FE
from app.services.engine_types import PlanInput


def _inp(**over) -> PlanInput:
    base = dict(origin_text="Delhi", destination_text="Istanbul", origin_place_id=None, destination_place_id=None,
                start=datetime(2026, 10, 10, 6), end=datetime(2026, 10, 17, 21), budget=Decimal(1500), travellers=2,
                traveller_type="couple", transport_modes=["train", "cab", "flight", "bus", "metro"], food="both",
                styles=["balanced"], stay="budget", currency="TRY")
    base.update(over)
    return PlanInput(**base)


ISTANBUL = FE.MinCost(Decimal(32476), Decimal(3444), Decimal(9594), Decimal(3774), 7, 1, "flight")


def test_far_over_budget_says_what_the_trip_needs_and_names_the_flight():
    info = FE.budget_infeasible(_inp(), ISTANBUL, Decimal(30000), Decimal(3000), Decimal(0))
    text = info["main_conflict"]
    assert "preference" not in text and "road" not in text
    assert "₺49,288" in text and "₺1,500" in text and "by flight" in text and "₺32,476" in text
    assert not any(a["key"] == "cheaper_transport" for a in info["adjustments"])  # every mode was already allowed


def test_a_mode_the_traveller_ruled_out_can_still_be_the_reason():
    inp = _inp(transport_modes=["flight"], budget=Decimal(45000), currency="INR", destination_text="Goa")
    mc = FE.MinCost(Decimal(30000), Decimal(3000), Decimal(6000), Decimal(2000), 2, 1, "flight")
    info = FE.budget_infeasible(inp, mc, Decimal(8000), Decimal(3000), Decimal(0))
    assert "transport choice (flight)" in info["main_conflict"] and "by train or bus" in info["main_conflict"]
    assert any(a["key"] == "cheaper_transport" for a in info["adjustments"])


def test_modes_that_cannot_run_that_far_are_not_offered():
    assert not FE.mode_realistic("train", 4500) and not FE.mode_realistic("bus", 900)
    assert FE.mode_realistic("train", 1400) and FE.mode_realistic("flight", 4500)
