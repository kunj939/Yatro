"""Provenance as a type (ADR 0002, invariants I1 and I2).

Every train fact shown to a traveller (its name, number, departure and arrival time) must come from real timetable
data no older than MAX_TIMETABLE_AGE. This module is where that is enforced by construction:

- `Fact[T]` carries a value together with where it came from and how fresh it is.
- `TrainService` can only be built from timetable sources; anything else raises `ProvenanceError`.
- `is_train_fact_source()` is what the response serializer (trip_service.item_out) checks, independently, before letting
  a train's name, number or clock times through.

The engine keeps working on `TransportData`; providers stamp each option's `meta["source_kind"]` and
`meta["observed_at"]` (see `stamp()`), and this module reads them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")

MAX_TIMETABLE_AGE = timedelta(days=45)  # I1
SEASONAL_MAX_AGE = timedelta(days=30)  # 1 Dec - 28/29 Feb: winter fog retimes and cancels North Indian trains often


def seasonal_cap_active(now: datetime) -> bool:
    from app.core.config import get_settings
    return get_settings().rail_seasonal_cap and (now.month == 12 or now.month <= 2)


def max_timetable_age(now: datetime | None = None) -> timedelta:
    """How old a saved timetable may be and still be shown: 45 days, 30 in winter (ADR 0003)."""
    return SEASONAL_MAX_AGE if seasonal_cap_active(now or datetime.now(UTC)) else MAX_TIMETABLE_AGE


class SourceKind(str, Enum):
    LIVE_API = "live_api"
    SAVED_TIMETABLE = "saved_timetable"
    OFFICIAL_DATASET = "official_dataset"
    STATION_GUIDANCE = "station_guidance"
    AI_GUIDANCE = "ai_guidance"
    SAMPLE = "sample"  # demo mode's fixed sample data, always shown with the "Demo mode: sample data" badge


TRAIN_FACT_SOURCES = frozenset({SourceKind.LIVE_API, SourceKind.SAVED_TIMETABLE, SourceKind.OFFICIAL_DATASET,
                                SourceKind.SAMPLE})


class ProvenanceError(ValueError):
    """A train fact was about to be built from a source that may not originate one (I1)."""


def freshness(source: SourceKind, observed_at: datetime | None, now: datetime | None = None) -> str:
    """fresh (≤7 days) / aging (≤21) / stale (≤45) / expired, for saved data; live data is fresh; guidance is n/a."""
    if source in (SourceKind.STATION_GUIDANCE, SourceKind.AI_GUIDANCE):
        return "n/a"
    # A live result ages too: a trip opened weeks after it was planned must not show a train older than the limit (I1).
    if source == SourceKind.SAMPLE or observed_at is None:
        return "fresh"
    now = now or datetime.now(UTC)
    obs = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    days = (now - obs).days  # whole days: "checked 7 days ago" is still fresh
    if days > max_timetable_age(now).days:
        return "expired"
    if days > 21:
        return "stale"
    if days > 7:
        return "aging"
    return "fresh"


@dataclass(frozen=True)
class Fact(Generic[T]):
    value: T
    source: SourceKind
    observed_at: datetime | None
    freshness: str

    @classmethod
    def of(cls, value: T, source: SourceKind, observed_at: datetime | None) -> Fact[T]:
        return cls(value, source, observed_at, freshness(source, observed_at))


@dataclass(frozen=True)
class TrainService:
    """A train as a fact. Construction fails unless every field comes from a timetable source and none is expired."""
    name: Fact[str]
    number: Fact[str | None]
    depart_at: Fact[datetime]
    arrive_at: Fact[datetime]

    def __post_init__(self) -> None:
        for f in (self.name, self.number, self.depart_at, self.arrive_at):
            if f.source not in TRAIN_FACT_SOURCES:
                raise ProvenanceError(f"a train fact cannot come from {f.source.value}")
            if f.freshness == "expired":
                raise ProvenanceError("a saved timetable older than 45 days cannot be shown")

    @classmethod
    def from_option(cls, t: Any) -> TrainService:
        src, obs = source_of(t), observed_of(t)
        return cls(Fact.of(t.name, src, obs), Fact.of(t.meta.get("number"), src, obs),
                   Fact.of(t.depart_at, src, obs), Fact.of(t.arrive_at, src, obs))


def source_of(t: Any) -> SourceKind:
    raw = (getattr(t, "meta", None) or {}).get("source_kind")
    try:
        return SourceKind(raw)
    except ValueError:
        return SourceKind.STATION_GUIDANCE  # unknown provenance is never treated as a timetable


def observed_of(t: Any) -> datetime | None:
    raw = (getattr(t, "meta", None) or {}).get("observed_at")
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def is_train_fact_source(source_kind: str | None, observed_at: str | None = None) -> bool:
    """May a train with this provenance show its name, number and clock times? (the serializer's check)"""
    try:
        src = SourceKind(source_kind)
    except ValueError:
        return False
    if src not in TRAIN_FACT_SOURCES:
        return False
    obs = None
    if observed_at:
        try:
            obs = datetime.fromisoformat(observed_at)
        except ValueError:
            return False
    return freshness(src, obs) != "expired"


def train_shows_facts(t: Any) -> bool:
    """For engine-side text (summaries, reasons): may this option's clock times be written out?"""
    if getattr(t, "mode", None) != "train":
        return True
    try:
        TrainService.from_option(t)
        return True
    except ProvenanceError:
        return False


def stamp(t: Any, source: SourceKind, observed_at: datetime | None = None) -> Any:
    """Record where an option came from (and when it was observed) on the option itself."""
    t.meta["source_kind"] = source.value
    t.meta["observed_at"] = (observed_at or datetime.now(UTC)).isoformat()
    t.meta["freshness"] = freshness(source, observed_at)
    return t


def part_of_day(dt: datetime) -> str:
    h = dt.hour
    return ("Night" if h < 5 else "Early morning" if h < 8 else "Morning" if h < 12 else "Afternoon" if h < 17
            else "Evening" if h < 21 else "Night")
