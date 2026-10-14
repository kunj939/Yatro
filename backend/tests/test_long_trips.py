"""Trips longer than a week (up to 30 nights), and reading a plan's prices in the home currency."""
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.trip import MAX_DAYS, TripCreate
from app.utils.money import approx_rate

from .test_scenarios import _payload, _use, check_ready, plus_user  # noqa: F401  (plus_user is a fixture)

START = date.today() + timedelta(days=10)


def _trip(nights: int) -> dict:
    return {"origin": {"name": "New Delhi"}, "destination": {"name": "Jaipur"}, "start_date": START.isoformat(),
            "end_date": (START + timedelta(days=nights)).isoformat(), "start_time": "07:00", "end_time": "22:00",
            "budget": 90000, "travellers": 2, "transport_modes": ["train", "cab"], "food": "veg",
            "trip_styles": ["balanced"], "stay": "tier2"}


def test_two_weeks_is_allowed_and_a_month_is_the_limit():
    assert MAX_DAYS == 30
    TripCreate(**_trip(14))
    TripCreate(**_trip(30))
    with pytest.raises(ValidationError, match="at most 30 nights"):
        TripCreate(**_trip(31))


def test_home_currency_rate():
    assert approx_rate("AED", "INR") == (Decimal(1) / Decimal("0.044")).quantize(Decimal("0.0001"))
    assert approx_rate("INR", "INR") is None and approx_rate("XYZ", "INR") is None


def test_a_twelve_day_trip_plans_every_rule(client, plus_user):  # noqa: F811
    sc = dict(o="New Delhi", d="Jaipur", days=12, start="07:00", end="22:00", budget=150000, n=2, modes=["train", "cab"],
              food="veg", styles=["balanced"], stay="tier2", tt="normal")
    _use(sc)
    trip_id = client.post("/api/trips", headers=plus_user, json=_payload(sc)).json()["id"]
    t0 = time.monotonic()
    body = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user).json()
    assert time.monotonic() - t0 < 20
    if body["status"] == "ready":
        assert not check_ready(body["trip"], sc)
        assert len(body["trip"]["itinerary"]["days"]) >= 12
    else:  # a refusal must still explain itself and offer a fix
        assert body["status"] == "infeasible" and body["feasibility"]["adjustments"]
    app.dependency_overrides.clear()
