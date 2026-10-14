"""When you travel changes what it costs (ADR 0016).

Live and verified prices already carry this. Our *estimated* fares and hotel rates are flat averages, so they are
too low in Diwali week or over New Year, and a flight booked three days ahead costs more than one booked a month
out. This module gives the demand factor for a date, and the planner applies it to estimated prices only, with a
Plan note saying what was added and why.

Festival dates follow the lunar calendar and are listed per year (sources: Government of India holiday lists and Drik
Panchang). The windows around them are wide enough that a day's difference between regional calendars doesn't matter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

DIWALI = {2026: date(2026, 11, 8), 2027: date(2027, 10, 29), 2028: date(2028, 10, 17)}
DUSSEHRA = {2026: date(2026, 10, 20), 2027: date(2027, 10, 9), 2028: date(2028, 9, 27)}
HOLI = {2026: date(2026, 3, 4), 2027: date(2027, 3, 22), 2028: date(2028, 3, 11)}


@dataclass(frozen=True)
class Peak:
    name: str
    flight: float
    bus: float
    stay: float


def peaks(day: date, india: bool) -> list[Peak]:
    """The busy seasons `day` falls in. `india`: the trip starts or ends in India."""
    out: list[Peak] = []
    y = day.year
    if india:
        d = DIWALI.get(y)
        if d and d - timedelta(days=6) <= day <= d + timedelta(days=4):
            out.append(Peak("Diwali week", 1.45, 1.35, 1.25))
        d = DUSSEHRA.get(y)
        if d and d - timedelta(days=5) <= day <= d + timedelta(days=1):
            out.append(Peak("Dussehra and Durga Puja", 1.25, 1.2, 1.15))
        d = HOLI.get(y)
        if d and d - timedelta(days=2) <= day <= d + timedelta(days=1):
            out.append(Peak("Holi", 1.2, 1.25, 1.1))
        if date(y, 5, 15) <= day <= date(y, 6, 30):
            out.append(Peak("summer holidays", 1.15, 1.1, 1.1))
    elif date(y, 7, 15) <= day <= date(y, 8, 31):
        out.append(Peak("peak summer season", 1.15, 1.05, 1.15))
    if (day.month == 12 and day.day >= 22) or (day.month == 1 and day.day <= 2):
        out.append(Peak("Christmas and New Year", 1.35, 1.2, 1.35))
    return out


def _strongest(ps: list[Peak], attr: str) -> tuple[float, str | None]:
    if not ps:
        return 1.0, None
    p = max(ps, key=lambda p: getattr(p, attr))
    return getattr(p, attr), p.name


def advance_factor(depart: date, today: date) -> tuple[float, str | None]:
    """Flights booked late cost more; a booking two months out is a little cheaper than average."""
    days = (depart - today).days
    if days <= 3:
        return 1.6, "booked less than 4 days ahead"
    if days <= 7:
        return 1.35, "booked less than a week ahead"
    if days <= 14:
        return 1.15, "booked less than two weeks ahead"
    if days > 60:
        return 0.95, None
    return 1.0, None


def transport_factor(mode: str, depart: date, india: bool, today: date) -> tuple[float, list[str]]:
    """Demand on an estimated fare: the busiest season it falls in, times (for flights) how late it is booked."""
    if mode not in ("flight", "bus"):
        return 1.0, []
    season, why = _strongest(peaks(depart, india), mode)
    reasons = [why] if why else []
    factor = season
    if mode == "flight":
        adv, late = advance_factor(depart, today)
        factor *= adv
        if late:
            reasons.append(late)
    return factor, reasons


def stay_factor(first_night: date, nights: int, india: bool) -> tuple[Decimal, str | None]:
    """The average demand over the nights stayed: festival and holiday nights, and Friday and Saturday nights."""
    if nights <= 0:
        return Decimal(1), None
    total, names = 0.0, []
    for n in range(nights):
        day = first_night + timedelta(days=n)
        f, why = _strongest(peaks(day, india), "stay")
        if day.weekday() in (4, 5):  # Friday and Saturday nights
            f *= 1.12
        total += f
        if why and why not in names:
            names.append(why)
    return Decimal(str(round(total / nights, 3))), (names[0] if names else None)


def apply_to_transport(options, india: bool, today: date) -> list[str]:
    """Raise each *estimated* flight and bus fare by its demand. Returns the reasons, for one Plan note."""
    reasons: list[str] = []
    for t in options:
        if t.verification_state != "estimated":
            continue
        f, why = transport_factor(t.mode, t.depart_at.date(), india, today)
        if abs(f - 1.0) < 0.01:
            continue
        t.cost_per_person = (t.cost_per_person * Decimal(str(f))).quantize(Decimal("1"))
        t.meta = {**(t.meta or {}), "demand_factor": round(f, 2), "demand": why}
        for w in why:
            if w not in reasons:
                reasons.append(w)
    return reasons
