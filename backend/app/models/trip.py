from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, Time, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedMixin, IdMixin, JSONType, TimestampMixin

TRIP_STATUSES = ("draft", "planning", "ready", "active", "completed")


class Trip(IdMixin, TimestampMixin, Base):
    __tablename__ = "trips"

    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)

    origin_name: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_place_id: Mapped[str | None] = mapped_column(String(255))
    origin_lat: Mapped[float | None] = mapped_column(Float)
    origin_lng: Mapped[float | None] = mapped_column(Float)
    destination_name: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_place_id: Mapped[str | None] = mapped_column(String(255))
    destination_lat: Mapped[float | None] = mapped_column(Float)
    destination_lng: Mapped[float | None] = mapped_column(Float)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")

    budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    travellers: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    selected_transport_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    feasibility: Mapped[dict | None] = mapped_column(JSONType)
    data_mode: Mapped[str] = mapped_column(String(10), default="demo")  # demo | live | mixed

    quota_consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quota_source: Mapped[str | None] = mapped_column(String(20))  # free | plus | credit
    plan_count: Mapped[int] = mapped_column(Integer, default=0)
    replan_count: Mapped[int] = mapped_column(Integer, default=0)

    preferences: Mapped[TripPreferences] = relationship(back_populates="trip", uselist=False,
                                                          cascade="all, delete-orphan")
    members: Mapped[list[TripMember]] = relationship(back_populates="trip", cascade="all, delete-orphan",
                                                       order_by="TripMember.created_at")
    itineraries: Mapped[list[Itinerary]] = relationship(back_populates="trip", cascade="all, delete-orphan",
                                                          order_by="Itinerary.version")
    transport_options: Mapped[list[TransportOption]] = relationship(back_populates="trip",
                                                                      cascade="all, delete-orphan")
    expenses: Mapped[list[Expense]] = relationship(back_populates="trip", cascade="all, delete-orphan",  # noqa: F821
                                                     order_by="Expense.created_at")

    @property
    def current_itinerary(self) -> Itinerary | None:
        current = [i for i in self.itineraries if i.is_current]
        return current[-1] if current else None


class TripPreferences(IdMixin, TimestampMixin, Base):
    __tablename__ = "trip_preferences"

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), unique=True)
    traveller_type: Mapped[str] = mapped_column(String(20), default="solo")
    # Which age groups are in the party (kids | teens | adults | seniors); empty/NULL = not asked, no age-based changes
    age_groups: Mapped[list | None] = mapped_column(JSONType, default=list, nullable=True)
    transport_modes: Mapped[list] = mapped_column(JSONType, default=list)
    food: Mapped[str] = mapped_column(String(10), default="both")
    trip_styles: Mapped[list] = mapped_column(JSONType, default=list)
    stay: Mapped[str] = mapped_column(String(20), default="no_stay")
    pace: Mapped[str] = mapped_column(String(10), default="normal")  # relaxed | normal | packed
    notes: Mapped[str | None] = mapped_column(Text)
    interests: Mapped[list] = mapped_column(JSONType, default=list)  # interpreted by the LLM from notes
    excluded_place_ids: Mapped[list] = mapped_column(JSONType, default=list)
    pinned_place_ids: Mapped[list] = mapped_column(JSONType, default=list)
    pinned_transport: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"outbound": key, "return": key}

    trip: Mapped[Trip] = relationship(back_populates="preferences")


class TripMember(IdMixin, CreatedMixin, Base):
    __tablename__ = "trip_members"

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    food_pref: Mapped[str | None] = mapped_column(String(10))
    preferences: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    trip: Mapped[Trip] = relationship(back_populates="members")


class Itinerary(IdMixin, CreatedMixin, Base):
    """One planned version of a trip. Replans create a new version; the previous one is kept."""
    __tablename__ = "itinerary"

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    tips: Mapped[list] = mapped_column(JSONType, default=list)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    remaining: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    budget_breakdown: Mapped[dict] = mapped_column(JSONType, default=dict)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    pipeline_trace: Mapped[list] = mapped_column(JSONType, default=list)
    weather: Mapped[dict | None] = mapped_column(JSONType)
    candidate_pool: Mapped[dict] = mapped_column(JSONType, default=dict)
    llm_status: Mapped[str] = mapped_column(String(20), default="template")  # ai | template | fallback
    change_note: Mapped[str | None] = mapped_column(Text)

    trip: Mapped[Trip] = relationship(back_populates="itineraries")
    items: Mapped[list[ItineraryItem]] = relationship(back_populates="itinerary", cascade="all, delete-orphan",
                                                        order_by="ItineraryItem.position")


class ItineraryItem(IdMixin, CreatedMixin, Base):
    __tablename__ = "itinerary_items"

    itinerary_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("itinerary.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    day_index: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # transport|activity|meal|stay|sunset|arrival
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # local wall-clock time
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500))
    place_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("places.id", ondelete="SET NULL"))
    transport_option_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    # Leg from the previous stop (deterministic: Routes API or estimated fallback)
    distance_from_prev_m: Mapped[int | None] = mapped_column(Integer)
    travel_minutes_from_prev: Mapped[int | None] = mapped_column(Integer)
    travel_mode: Mapped[str | None] = mapped_column(String(20))
    travel_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    leg_state: Mapped[str | None] = mapped_column(String(30))
    polyline: Mapped[str | None] = mapped_column(Text)

    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # item cost for the whole group
    cost_state: Mapped[str | None] = mapped_column(String(30))
    recommended_minutes: Mapped[int | None] = mapped_column(Integer)
    opening_time: Mapped[str | None] = mapped_column(String(5))
    closing_time: Mapped[str | None] = mapped_column(String(5))
    hours_state: Mapped[str | None] = mapped_column(String(30))
    hours_note: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSONType, default=list)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    explanation: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    # When this row's Google content (name, address, rating, hours, coordinates) was fetched. NULL = no Google content
    # (demo data, or our own item). Older than 30 days means it is not shown (I6, ADR 0005).
    google_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    itinerary: Mapped[Itinerary] = relationship(back_populates="items")
    place: Mapped[Place | None] = relationship(lazy="joined", viewonly=True)  # noqa: F821
    alternatives: Mapped[list[Alternative]] = relationship(back_populates="item", cascade="all, delete-orphan",
                                                             order_by="Alternative.rank")


class Alternative(IdMixin, CreatedMixin, Base):
    __tablename__ = "alternatives"

    itinerary_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("itinerary_items.id", ondelete="CASCADE"),
                                                         index=True)
    place_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("places.id", ondelete="SET NULL"))
    transport_option_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # place | transport
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    cost_delta: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    time_delta_minutes: Mapped[int] = mapped_column(Integer, default=0)
    distance_from_prev_m: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    checks: Mapped[dict] = mapped_column(JSONType, default=dict)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    google_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # as on ItineraryItem

    item: Mapped[ItineraryItem] = relationship(back_populates="alternatives")
    place: Mapped[Place | None] = relationship(lazy="joined", viewonly=True)  # noqa: F821


class TransportOption(IdMixin, CreatedMixin, Base):
    __tablename__ = "transport_options"

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # outbound | return
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # train|bus|cab|own_car|flight
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_class: Mapped[str | None] = mapped_column(String(40))
    depart_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    arrive_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255))
    to_name: Mapped[str | None] = mapped_column(String(255))
    from_lat: Mapped[float | None] = mapped_column(Float)
    from_lng: Mapped[float | None] = mapped_column(Float)
    to_lat: Mapped[float | None] = mapped_column(Float)
    to_lng: Mapped[float | None] = mapped_column(Float)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[int | None] = mapped_column(Integer)
    cost_per_person: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    cost_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    comfort: Mapped[int] = mapped_column(Integer, default=3)
    verification_state: Mapped[str] = mapped_column(String(30), default="estimated")
    source: Mapped[str | None] = mapped_column(String(255))
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    polyline: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)

    trip: Mapped[Trip] = relationship(back_populates="transport_options")
