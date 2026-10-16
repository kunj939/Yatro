"""Plan together (ADR 0022): one link in the group chat collects everyone's budget, dates, food and vote.

Replies come from people without accounts, so they hold only what the page asks for (a name, an optional budget,
food, dates, votes) and a keyed hash of a random key the browser keeps; no phone, email or IP address.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, JSONType, TimestampMixin


class GroupPlan(IdMixin, TimestampMixin, Base):
    __tablename__ = "group_plans"

    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    origin_name: Mapped[str] = mapped_column(String(255))
    origin_place_id: Mapped[str | None] = mapped_column(String(255))
    origin_lat: Mapped[float | None] = mapped_column(Float)
    origin_lng: Mapped[float | None] = mapped_column(Float)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer, default=2)  # how long the trip is, in days
    budget_hint: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # the organiser's suggested budget per person
    stay: Mapped[str] = mapped_column(String(20), default="tier2")
    note: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | made | closed
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trip_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="SET NULL"))

    options: Mapped[list[GroupPlanOption]] = relationship(back_populates="group", cascade="all, delete-orphan",
                                                          order_by="GroupPlanOption.position")
    replies: Mapped[list[GroupPlanReply]] = relationship(back_populates="group", cascade="all, delete-orphan",
                                                         order_by="GroupPlanReply.created_at")


class GroupPlanOption(IdMixin, Base):
    __tablename__ = "group_plan_options"

    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("group_plans.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(255))
    place_id: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    group: Mapped[GroupPlan] = relationship(back_populates="options")


class GroupPlanReply(IdMixin, TimestampMixin, Base):
    __tablename__ = "group_plan_replies"

    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("group_plans.id", ondelete="CASCADE"), index=True)
    device_hash: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(60))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    food: Mapped[str] = mapped_column(String(10), default="both")  # veg | non_veg | both
    dates: Mapped[list] = mapped_column(JSONType, default=list)  # ISO dates this person can do
    votes: Mapped[list] = mapped_column(JSONType, default=list)  # option ids
    is_owner: Mapped[bool] = mapped_column(default=False)

    group: Mapped[GroupPlan] = relationship(back_populates="replies")
