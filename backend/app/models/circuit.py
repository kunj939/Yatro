"""Quota state that must outlive one process: a provider that said its quota is used up stays closed to us until the
next probe, across restarts and workers (ADR 0007). Rows are written only on quota events, never per call."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class ProviderCircuit(IdMixin, TimestampMixin, Base):
    __tablename__ = "provider_circuit"
    __table_args__ = (UniqueConstraint("provider", "endpoint", name="uq_provider_circuit"),)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False, default="*")  # "*" = the whole provider
    open_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # next probe time
    reason: Mapped[str | None] = mapped_column(Text)
