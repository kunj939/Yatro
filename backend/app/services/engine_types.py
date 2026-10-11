"""Internal structures shared by the planning engine stages."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.providers.types import PlaceData, TransportData


@dataclass
class PlanInput:
    origin_text: str
    destination_text: str
    origin_place_id: str | None
    destination_place_id: str | None
    start: datetime  # local wall clock
    end: datetime
    budget: Decimal
    travellers: int
    traveller_type: str
    transport_modes: list[str]
    food: str  # veg | non_veg | both
    styles: list[str]
    stay: str  # premium | tier1 | tier2 | budget | oyo | hostel | no_stay
    pace: str = "normal"
    interests: list[str] = field(default_factory=list)
    age_groups: list[str] = field(default_factory=list)  # kids | teens | adults | seniors; empty = not asked (services/age_fit.py)
    avoid_tags: set[str] = field(default_factory=set)  # place tags the traveller asked to see less of ("no temples")
    notes: str | None = None
    excluded: set[str] = field(default_factory=set)
    pinned: list[str] = field(default_factory=list)
    pinned_transport: dict = field(default_factory=dict)
    member_budgets: list[tuple[str, Decimal]] = field(default_factory=list)
    member_food: list[str] = field(default_factory=list)
    timezone: str = "Asia/Kolkata"  # the destination's own timezone (see app/utils/locale.py)
    currency: str = "INR"  # the destination's own currency, likewise

    @property
    def days(self) -> int:
        return (self.end.date() - self.start.date()).days + 1

    @property
    def nights(self) -> int:
        return max(0, self.days - 1)

    @property
    def effective_food(self) -> str:
        """If anyone in the group is vegetarian, every meal stop must serve vegetarian food."""
        prefs = [self.food, *self.member_food]
        if "veg" in prefs:
            return "veg"
        return "non_veg" if all(p == "non_veg" for p in prefs) else "both"


@dataclass
class Candidate:
    key: str  # provider place id — stable across replans
    place: PlaceData
    kind: str  # attraction | restaurant | hotel
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    matched_interests: list[str] = field(default_factory=list)  # subset of `matched` the traveller explicitly picked
    age_note: str | None = None  # "good for children": why this place suits the party's ages (only set for a real lift)
    reasons: list[str] = field(default_factory=list)
    unit_cost: Decimal = Decimal("0")  # per person (entry fee / meal) or per night (hotel)
    cost_state: str = "estimated"
    duration: int = 60
    hours_state: str = "unavailable"
    hours_note: str | None = None
    sources: list[dict] = field(default_factory=list)
    db_id: uuid.UUID | None = None
    pinned: bool = False

    @property
    def point(self) -> tuple[float, float]:
        return (self.place.lat, self.place.lng)

    def group_cost(self, travellers: int) -> Decimal:
        return self.unit_cost * travellers


@dataclass
class Leg:
    distance_m: int
    minutes: int
    mode: str  # walk | cab | transit | own_car
    cost: Decimal
    state: str
    polyline: str | None = None


@dataclass
class Stop:
    kind: str  # activity | meal | sunset | stay
    cand: Candidate | None
    name: str
    duration: int
    meal: str | None = None
    window: tuple[datetime, datetime] | None = None  # meals: allowed start window
    anchor: datetime | None = None  # sunset: target time
    wait_ok: bool = False  # meal chosen as the only remaining option: waiting (shown as free time) is fine
    # filled by simulation
    arrive: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None
    leg: Leg | None = None
    day_index: int = 0

    @property
    def point(self) -> tuple[float, float]:
        return self.cand.point if self.cand else (0.0, 0.0)


@dataclass
class DayFrame:
    index: int
    day: date
    start: datetime
    end: datetime
    start_point: tuple[float, float]
    start_name: str
    end_point: tuple[float, float]
    end_name: str
    stops: list[Stop] = field(default_factory=list)
    final_leg: Leg | None = None
    sunset: datetime | None = None


@dataclass
class TransportChoice:
    outbound: TransportData | None
    ret: TransportData | None
    cost: Decimal
    reason: str
    local_trip: bool = False


class StageReporter:
    """Collects the planning trace and forwards live events (used for the streaming endpoint)."""

    LABELS = {
        "understand": "Understanding preferences", "routes": "Finding routes", "places": "Checking places",
        "hours": "Checking opening hours", "prices": "Comparing prices", "optimize": "Optimizing route",
        "budget": "Checking budget", "alternatives": "Finding alternatives", "itinerary": "Creating itinerary",
    }

    def __init__(self, emit=None):
        self.emit = emit
        self.trace: list[dict] = []
        self._t0: float | None = None

    def start(self, key: str) -> None:
        import time

        from app.core import telemetry
        telemetry.stage_start(key)
        self._t0 = time.perf_counter()
        if self.emit:
            self.emit({"type": "stage", "key": key, "label": self.LABELS[key], "status": "running"})

    def done(self, key: str, detail: str, status: str = "done") -> None:
        import time
        ms = int((time.perf_counter() - (self._t0 or time.perf_counter())) * 1000)
        entry = {"key": key, "label": self.LABELS[key], "status": status, "detail": detail, "ms": ms}
        from app.core import telemetry
        telemetry.stage_end(key, status)
        self.trace.append(entry)
        if self.emit:
            self.emit({"type": "stage", **entry})