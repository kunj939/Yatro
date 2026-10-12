"""A Jaipur -> Bali plan (rupiah) said "Switch to Tier 2 (3-star): saves about Rp6,823,200" on a Rp6,840,000 stay, and
its "Raise budget to Rp15,696,500" button was refused ("Some fields need attention"). These pin the fixes."""
from datetime import datetime
from decimal import Decimal

import pytest

from app.services import fare_model as F
from app.services import feasibility as FE
from app.services.engine_types import PlanInput
from app.utils.money import APPROX_INR_TO


def _bali(stay="tier1"):
    return PlanInput("Jaipur", "Bali", None, None, datetime(2026, 10, 17, 7), datetime(2026, 10, 23, 22), Decimal(10_000_000),
                     1, "solo", ["flight"], "both", ["balanced"], stay, currency="IDR")


def test_a_cheaper_hotel_is_priced_in_the_trips_own_currency():
    inp = _bali()
    mc = FE.MinCost(Decimal(7_215_440), Decimal(6_840_000), Decimal(890_000), Decimal(3_219), 6, 1, "flight")
    info = FE.budget_infeasible(inp, mc, None, Decimal(0), Decimal(0))
    hotel = next(a for a in info["adjustments"] if a["key"] == "cheaper_hotel")
    new_stay = Decimal(F.stay_nightly(hotel["apply"]["stay"])) * APPROX_INR_TO["IDR"] * 6  # that tier, 6 nights, in rupiah
    assert hotel["saves"] == pytest.approx(float(Decimal(6_840_000) - new_stay))
    assert hotel["saves"] < 5_500_000  # not Rp6.8 million: the cheaper room costs real rupiah too
    assert ".86" not in " ".join(a["description"] for a in info["adjustments"])  # savings are whole rupiah


def test_a_rupiah_sized_budget_is_accepted(client):
    from tests.conftest import register, trip_payload
    auth = register(client)
    ok = client.post("/api/trips", json=trip_payload(budget=15_696_500), headers=auth)
    assert ok.status_code in (200, 201), ok.text  # the old 10,000,000 cap refused this
    too_big = client.post("/api/trips", json=trip_payload(budget=10**10), headers=auth)
    assert too_big.status_code == 422  # still bounded by the column (Numeric(12, 2))
