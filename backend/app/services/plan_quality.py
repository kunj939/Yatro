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
# Countries that are one city: Google calls them a country, but the city is the trip.
CITY_STATES = {"singapore", "monaco", "vatican city", "hong kong", "macau", "macao", "san marino", "gibraltar"}
# When someone types a whole country, plan the city most visitors start from (usually the capital, sometimes the
# main gateway). Keys are lower-case country names as Google returns them, plus common spellings people type.
MAIN_CITY = {
    "india": "New Delhi", "bangladesh": "Dhaka", "nepal": "Kathmandu", "bhutan": "Thimphu", "sri lanka": "Colombo",
    "maldives": "Malé", "pakistan": "Lahore", "myanmar": "Yangon", "myanmar (burma)": "Yangon", "thailand": "Bangkok",
    "malaysia": "Kuala Lumpur", "indonesia": "Bali", "vietnam": "Hanoi", "cambodia": "Siem Reap", "laos": "Luang Prabang",
    "philippines": "Manila", "china": "Beijing", "japan": "Tokyo", "south korea": "Seoul", "taiwan": "Taipei",
    "mongolia": "Ulaanbaatar", "united arab emirates": "Dubai", "uae": "Dubai", "qatar": "Doha", "oman": "Muscat",
    "bahrain": "Manama", "kuwait": "Kuwait City", "saudi arabia": "Riyadh", "jordan": "Amman", "israel": "Jerusalem",
    "turkey": "Istanbul", "türkiye": "Istanbul", "egypt": "Cairo", "morocco": "Marrakesh", "kenya": "Nairobi",
    "tanzania": "Zanzibar", "south africa": "Cape Town", "mauritius": "Port Louis", "seychelles": "Victoria",
    "united kingdom": "London", "uk": "London", "england": "London", "scotland": "Edinburgh", "ireland": "Dublin",
    "france": "Paris", "spain": "Barcelona", "portugal": "Lisbon", "italy": "Rome", "germany": "Berlin",
    "netherlands": "Amsterdam", "belgium": "Brussels", "switzerland": "Zurich", "austria": "Vienna",
    "czechia": "Prague", "czech republic": "Prague", "hungary": "Budapest", "poland": "Kraków", "greece": "Athens",
    "croatia": "Dubrovnik", "denmark": "Copenhagen", "sweden": "Stockholm", "norway": "Oslo", "finland": "Helsinki",
    "iceland": "Reykjavík", "russia": "Moscow", "georgia": "Tbilisi", "armenia": "Yerevan", "azerbaijan": "Baku",
    "kazakhstan": "Almaty", "uzbekistan": "Samarkand", "united states": "New York", "usa": "New York",
    "canada": "Toronto", "mexico": "Mexico City", "brazil": "Rio de Janeiro", "argentina": "Buenos Aires",
    "peru": "Cusco", "chile": "Santiago", "australia": "Sydney", "new zealand": "Auckland", "fiji": "Nadi",
}
MEAL_ORDER = ["breakfast", "lunch", "snack", "dinner"]
# "on Mon 19 Oct": the date, because day numbers in the plan skip a day spent on an overnight train
_MEAL_WARNING = re.compile(r"^No (breakfast|lunch|snack|dinner) stop matched .* on (\w{3} \d{2} \w{3})\.$")


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


def too_broad(types: list[str] | None, name: str | None = None) -> bool:
    """A whole country can't be planned as one destination; a city or region can, and so can a city-state."""
    if name and name.strip().lower() in CITY_STATES:
        return False
    return bool(set(types or []) & TOO_BROAD_TYPES)


def main_city(country: str | None) -> str | None:
    """The city to plan when someone names a whole country, or None if we don't know one for it."""
    return MAIN_CITY.get((country or "").strip().lower())


def _days_text(days: list[str]) -> str:
    days = list(dict.fromkeys(days))  # in the order the plan reached them, which is date order
    if len(days) == 1:
        return days[0]
    return ", ".join(days[:-1]) + f" and {days[-1]}"


def consolidate_warnings(warnings: list[str]) -> list[str]:
    """Turn "No lunch stop matched ... on Mon 19 Oct" x 20 into one line per meal, and drop duplicates."""
    missing: dict[str, list[int]] = {}
    others: list[str] = []
    for w in warnings:
        m = _MEAL_WARNING.match(w)
        if m:
            missing.setdefault(m.group(1), []).append(m.group(2))
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
    long_trip = len(frames) > 8  # more than a week: free days are normal, so they are noted, never a reason to refuse
    if full and len(empty) * 2 > len(full) and not long_trip:
        rep.only_empty_days = not rep.blocking
        rep.blocking.append(f"{len(empty)} of {len(full)} full days have nothing planned: not enough suitable places "
                            "were found near the destination.")
    elif empty:
        dates = [f"{f.day:%a %d %b}" for f in empty]  # dates, not numbers: the plan's day labels skip a train-only day
        if long_trip and len(dates) > 3:
            rep.notes.append(f"{len(dates)} days are kept free to rest or explore on your own: the best places near the "
                             "destination are already in the plan.")
        else:
            rep.notes.append(f"{_days_text(dates)} {'is' if len(dates) == 1 else 'are'} kept free: nothing else fitted "
                             "your time and budget.")
    return rep