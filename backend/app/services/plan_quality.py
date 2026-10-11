"""Quality gate: a plan is checked before anyone sees it.

Blocking problems (the plan would mislead) stop the plan and explain why. Softer problems become a few short,
consolidated notes instead of one warning per meal per day.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from app.services.engine_types import DayFrame
from app.utils.geo import haversine_m

FULL_DAY = timedelta(hours=6)  # a day with at least this much free time should have something planned
MAX_HOP_M = 60_000  # two stops in one day more than 60 km apart means the places are not really "here"
MAX_ARRIVAL_M = 150_000  # arrival point or hotel this far from the destination: wrong destination match
MAX_FROM_DEST_M = 120_000  # a planned stop this far from the destination centre: the places and the destination disagree
TOO_BROAD_TYPES = {"country", "continent"}
MEAL_ORDER = ["breakfast", "lunch", "snack", "dinner"]
_MEAL_WARNING = re.compile(r"^No (breakfast|lunch|snack|dinner) stop matched .* on day (\d+)\.$")


@dataclass
class QualityReport:
    blocking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    empty_days: int = 0  # full days with nothing planned
    only_empty_days: bool = False  # the empty days are the only blocking problem
    all_travel: bool = False  # nothing to see anywhere: the "trip" is only getting there and back

    @property
    def ok(self) -> bool:
        return not self.blocking


def too_broad(types: list[str] | None) -> bool:
    """A whole country can't be planned as one destination; a city or region can."""
    return bool(set(types or []) & TOO_BROAD_TYPES)


def _days_text(days: list[int]) -> str:
    days = sorted(set(days))
    if len(days) == 1:
        return f"day {days[0]}"
    return "days " + ", ".join(str(d) for d in days[:-1]) + f" and {days[-1]}"


def consolidate_warnings(warnings: list[str]) -> list[str]:
    """Turn "No lunch stop matched ... on day 2" x 20 into one line per meal, and drop duplicates."""
    missing: dict[str, list[int]] = {}
    others: list[str] = []
    for w in warnings:
        m = _MEAL_WARNING.match(w)
        if m:
            missing.setdefault(m.group(1), []).append(int(m.group(2)))
        elif w not in others:
            others.append(w)
    lines = []
    main = [m for m in MEAL_ORDER if m in missing and m != "snack"]
    if main:
        parts = [f"{m} on {_days_text(missing[m])}" for m in main]
        lines.append("Couldn't fit every meal within your time and budget: " + "; ".join(parts)
                     + ". Carry snacks, or raise the budget a little.")
    return lines + others


def review(frames: list[DayFrame], dest_point: tuple[float, float], arrival_point: tuple[float, float] | None,
           hotel_point: tuple[float, float] | None, local_trip: bool, dest_label: str | None = None) -> QualityReport:
    rep = QualityReport()
    where = f"\u201c{dest_label}\u201d" if dest_label else "the destination"

    # 0. Do the planned places actually belong to the destination? A misspelt or ambiguous name can resolve to one
    # place while the sights found belong to another (e.g. "Masoorie" matching a village near Ghaziabad while the
    # sights were in Mussoorie, 250 km away). That reads as a plan with hundreds of km of "local" travel.
    stray = [(s.cand.place.name, haversine_m(*s.cand.point, *dest_point)) for f in frames for s in f.stops
             if s.cand and haversine_m(*s.cand.point, *dest_point) > MAX_FROM_DEST_M]
    if stray:
        name, dist = max(stray, key=lambda x: x[1])
        rep.blocking.append(f"{name} is about {int(dist // 1000)} km from {where}, which usually means the name "
                            f"matched a different place than you meant. Pick the exact place from the suggestions "
                            f"when you type it.")
        return rep

    # 1. Is the plan actually at the destination?
    if arrival_point and not local_trip and haversine_m(*arrival_point, *dest_point) > MAX_ARRIVAL_M:
        rep.blocking.append("The arrival point is very far from the destination, so the destination is probably "
                            "too broad (a whole country or large region).")
    if hotel_point and haversine_m(*hotel_point, *dest_point) > MAX_ARRIVAL_M:
        rep.blocking.append("The only hotels found are far from the destination centre.")

    # 2. Stops of one day should be near each other, in time order, inside the day
    for f in frames:
        prev = None
        for i, s in enumerate(f.stops):
            if s.start is None or s.end is None:
                continue
            if s.start < f.start - timedelta(minutes=1) or (prev and s.start < prev):
                rep.blocking.append(f"Day {f.index + 1} came out in the wrong time order.")
                break
            prev = s.end
            if i > 0 and s.leg and s.leg.distance_m > MAX_HOP_M:
                rep.blocking.append(f"Day {f.index + 1} jumps {s.leg.distance_m // 1000} km between two stops, "
                                    "which isn't a sensible day plan.")
                break

    # 3. A trip somewhere else must include something to see there. Travelling all day, sleeping, and catching
    # the first train home is not a plan worth showing, however well the tickets fit the budget. (Full days left
    # empty are a different problem — usually the money ran out — and are explained by step 4 instead.)
    has_full_day = any(f.end - f.start >= FULL_DAY for f in frames)
    if not local_trip and not has_full_day and not any(s.kind in ("activity", "sunset") for f in frames for s in f.stops):
        rep.all_travel = True
        rep.blocking.append("There's no time left to see anything at the destination: the plan would be travel, "
                            "a night's sleep and the journey home.")
        return rep

    # 4. Days with lots of time but nothing to do
    full = [f for f in frames if f.end - f.start >= FULL_DAY]
    empty = [f for f in full if not any(s.kind in ("activity", "sunset") for s in f.stops)]
    rep.empty_days = len(empty)
    if full and len(empty) * 2 > len(full):
        rep.only_empty_days = not rep.blocking
        rep.blocking.append(f"{len(empty)} of {len(full)} full days have nothing planned: not enough suitable places "
                            "were found near the destination.")
    elif empty:
        rep.notes.append(f"{_days_text([f.index + 1 for f in empty]).capitalize()} is kept free: nothing else fitted "
                         "your time and budget.")
    return rep