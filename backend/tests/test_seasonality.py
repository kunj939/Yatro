"""Seasonal demand on estimated prices (ADR 0016)."""
from datetime import date, datetime
from decimal import Decimal

from app.providers.types import TransportData
from app.services import seasonality as S

TODAY = date(2026, 10, 1)  # five weeks before the Diwali-week trips below


def _option(mode, day, state="estimated", fare=5000):
    at = datetime.combine(day, datetime.min.time()).replace(hour=9)
    return TransportData(direction="outbound", mode=mode, name=mode, depart_at=at, arrive_at=at, duration_minutes=120,
                         cost_per_person=Decimal(fare), verification_state=state)


def test_festivals_and_holidays():
    assert [p.name for p in S.peaks(date(2026, 11, 8), india=True)] == ["Diwali week"]
    assert S.peaks(date(2026, 11, 8), india=False) == []  # Diwali doesn't move fares between London and Paris
    assert "Christmas and New Year" in [p.name for p in S.peaks(date(2026, 12, 30), india=False)]
    assert S.peaks(date(2026, 9, 15), india=True) == []


def test_flights_follow_season_and_how_late_they_are_booked():
    f, why = S.transport_factor("flight", date(2026, 11, 7), True, TODAY)
    assert f == 1.45 and why == ["Diwali week"]
    f, why = S.transport_factor("flight", date(2026, 11, 7), True, date(2026, 11, 5))
    assert round(f, 2) == round(1.45 * 1.6, 2) and why == ["Diwali week", "booked less than 4 days ahead"]
    assert S.transport_factor("train", date(2026, 11, 7), True, TODAY) == (1.0, [])  # rail fares are fixed


def test_only_estimated_fares_move():
    est, live = _option("flight", date(2026, 11, 7)), _option("flight", date(2026, 11, 7), state="verified")
    reasons = S.apply_to_transport([est, live], True, TODAY)
    assert est.cost_per_person == Decimal(7250) and live.cost_per_person == Decimal(5000)
    assert reasons == ["Diwali week"] and est.meta["demand"] == ["Diwali week"]


def test_hotel_nights():
    quiet, _ = S.stay_factor(date(2026, 9, 14), 3, True)  # Mon-Wed nights in September
    assert quiet == Decimal(1)
    weekend, _ = S.stay_factor(date(2026, 9, 18), 2, True)  # Fri and Sat nights
    assert weekend == Decimal("1.12")
    diwali, why = S.stay_factor(date(2026, 11, 7), 2, True)
    assert diwali > Decimal("1.25") and why == "Diwali week"
