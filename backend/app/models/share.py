"""Share links (ADR 0006). Only a keyed hash of the token is stored, so a database leak opens no shared trip."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin


class ShareLink(IdMixin, CreatedMixin, Base):
    __tablename__ = "share_links"

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False)  # first characters, for the owner's list
    show_budget: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    include_split: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # "Plan + split"
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = no expiry (owner's choice)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
