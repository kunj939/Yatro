"""Our own copy of Indian Railways timetables, so the same route isn't fetched again and again.

Timetables change only a few times a year, so a saved route is reused for 14 days for the same weekday
(a source lists the trains running on a given date, and most trains don't run every day).
Station lookups ("Gurgaon" -> GGN) almost never change and are kept for 90 days.

Kept per `source` (e.g. "irctc1", "railradar"): two sources answer in different shapes, so a saved row is
only ever read back by the same source that saved it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, JSONType, TimestampMixin


class RailStationLookup(IdMixin, TimestampMixin, Base):
    __tablename__ = "rail_station_lookups"
    __table_args__ = (UniqueConstraint("query", "source", name="uq_rail_station_lookup_query_source"),)

    query: Mapped[str] = mapped_column(String(120), index=True, nullable=False)  # lower-case city
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    rows: Mapped[list] = mapped_column(JSONType, default=list)  # raw search results
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RailTimetable(IdMixin, TimestampMixin, Base):
    __tablename__ = "rail_timetables"
    __table_args__ = (UniqueConstraint("from_code", "to_code", "weekday", "source",
                                       name="uq_rail_timetable_route_day_source"),)

    from_code: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    to_code: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 = Monday
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    rows: Mapped[list] = mapped_column(JSONType, default=list)  # validated train rows for that weekday (Tier 2)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Tier 2 (ADR 0003): when it may no longer be shown, when the source last confirmed it, what validation kept
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"status": ok|legacy, "kept": n, "dropped": {...}}
    needs_refresh: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    refresh_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 3 reported, 2 stale, 1 aging
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RailCityRoute(IdMixin, TimestampMixin, Base):
    """Tier 1 (internal only, never shown): the station pairs that last returned trains for a city pair."""
    __tablename__ = "rail_city_routes"
    __table_args__ = (UniqueConstraint("source", "origin_key", "dest_key", name="uq_rail_city_route"),)

    source: Mapped[str] = mapped_column(String(60), nullable=False)
    origin_key: Mapped[str] = mapped_column(String(120), nullable=False)
    dest_key: Mapped[str] = mapped_column(String(120), nullable=False)
    working_pairs: Mapped[list] = mapped_column(JSONType, default=list)  # [["JP", "DEE"], ...], best first
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RailPairResult(IdMixin, TimestampMixin, Base):
    """Tier 1 (internal only, never shown): did a station pair return trains on a weekday, and which numbers."""
    __tablename__ = "rail_pair_results"
    __table_args__ = (UniqueConstraint("source", "from_code", "to_code", "weekday", name="uq_rail_pair_result"),)

    source: Mapped[str] = mapped_column(String(60), nullable=False)
    from_code: Mapped[str] = mapped_column(String(10), nullable=False)
    to_code: Mapped[str] = mapped_column(String(10), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # trains | empty
    train_numbers: Mapped[list] = mapped_column(JSONType, default=list)  # for refresh priority only, never shown
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RailTrainReport(IdMixin, CreatedMixin, Base):
    """A traveller saying a train didn't run or ran at another time; marks its route for refresh."""
    __tablename__ = "rail_train_reports"

    trip_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    from_code: Mapped[str] = mapped_column(String(10), nullable=False)
    to_code: Mapped[str] = mapped_column(String(10), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    train_number: Mapped[str | None] = mapped_column(String(10))
    problem: Mapped[str] = mapped_column(String(20), nullable=False)  # didnt_run | wrong_time
    note: Mapped[str | None] = mapped_column(Text)


class RailDailyStat(IdMixin, TimestampMixin, Base):
    """Per day and source: Tier 2 hits and freshness mix, calls Tier 1 saved, refresh calls (ADR 0003)."""
    __tablename__ = "rail_daily_stats"
    __table_args__ = (UniqueConstraint("day", "source", name="uq_rail_daily_stat"),)

    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    counters: Mapped[dict] = mapped_column(JSONType, default=dict)