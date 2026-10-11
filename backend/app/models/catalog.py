"""Verified real-world data: places, restaurants, hotels, the web sources that back them, and the API cache."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedMixin, IdMixin, JSONType, TimestampMixin


class Place(IdMixin, TimestampMixin, Base):
    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("provider", "provider_place_id", name="uq_places_provider_id"),)

    provider: Mapped[str] = mapped_column(String(30), nullable=False)  # google | demo
    provider_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    rating: Mapped[float | None] = mapped_column(Float)
    rating_count: Mapped[int | None] = mapped_column(Integer)
    price_level: Mapped[int | None] = mapped_column(Integer)
    types: Mapped[list] = mapped_column(JSONType, default=list)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    # {"weekly": {"0": [["09:30","17:00"]], ...}, "source": "google", "special": {...}}
    opening_hours: Mapped[dict | None] = mapped_column(JSONType)
    website: Mapped[str | None] = mapped_column(String(500))
    business_status: Mapped[str | None] = mapped_column(String(40))
    entry_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    entry_fee_state: Mapped[str | None] = mapped_column(String(30))
    recommended_minutes: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    restaurant: Mapped[Restaurant | None] = relationship(back_populates="place", uselist=False,
                                                           cascade="all, delete-orphan")
    hotel: Mapped[Hotel | None] = relationship(back_populates="place", uselist=False, cascade="all, delete-orphan")


class Restaurant(IdMixin, CreatedMixin, Base):
    __tablename__ = "restaurants"

    place_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("places.id", ondelete="CASCADE"), unique=True)
    veg_only: Mapped[bool] = mapped_column(Boolean, default=False)
    serves_veg: Mapped[bool] = mapped_column(Boolean, default=True)
    serves_non_veg: Mapped[bool] = mapped_column(Boolean, default=True)
    cuisines: Mapped[list] = mapped_column(JSONType, default=list)
    meal_types: Mapped[list] = mapped_column(JSONType, default=list)  # breakfast | lunch | snack | dinner
    avg_cost_per_person: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    cost_state: Mapped[str] = mapped_column(String(30), default="estimated")

    place: Mapped[Place] = relationship(back_populates="restaurant")


class Hotel(IdMixin, CreatedMixin, Base):
    __tablename__ = "hotels"

    place_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("places.id", ondelete="CASCADE"), unique=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    stars: Mapped[int | None] = mapped_column(Integer)
    nightly_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    rate_state: Mapped[str] = mapped_column(String(30), default="estimated")
    amenities: Mapped[list] = mapped_column(JSONType, default=list)

    place: Mapped[Place] = relationship(back_populates="hotel")


class WebSource(IdMixin, CreatedMixin, Base):
    __tablename__ = "web_sources"

    place_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("places.id", ondelete="CASCADE"), index=True)
    trip_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(40), nullable=False)  # opening_hours | entry_fee | closure | event
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255))
    trust_tier: Mapped[str] = mapped_column(String(20), nullable=False)  # official|structured|reputable|generic
    snippet: Mapped[str | None] = mapped_column(Text)
    extracted_value: Mapped[dict | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="unverified")  # confirmed | conflict | unverified
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiCache(IdMixin, CreatedMixin, Base):
    __tablename__ = "api_cache"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    response: Mapped[dict | list] = mapped_column(JSONType, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
