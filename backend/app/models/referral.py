"""Invite-a-friend: who brought whom, and what each was given (services/referral_service.py, ADR 0012)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin


class Referral(IdMixin, CreatedMixin, Base):
    __tablename__ = "referrals"

    inviter_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # one inviter per account, ever: the unique key is what stops a second code being applied
    friend_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="joined", nullable=False)  # joined | rewarded | capped
    friend_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # given at sign-up
    inviter_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # given at the friend's first plan
    discount_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)     # off the friend's first month of Plus
    discount_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
