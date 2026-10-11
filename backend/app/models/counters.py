"""A tiny per-day counter (spend caps until Phase 4's budgets exist)."""
from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class DailyCounter(IdMixin, TimestampMixin, Base):
    __tablename__ = "daily_counters"
    __table_args__ = (UniqueConstraint("day", "name", name="uq_daily_counters"),)

    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD (UTC)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
