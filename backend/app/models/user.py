from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedMixin, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Lifetime counter — the free plan is "N trip plans ever" (FREE_TRIP_PLANS), not per day.
    free_plans_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Mobile number in international form (+919876543210). Optional only for accounts made before it was asked for.
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The person's OWN UPI ID, entered by them (ADR 0006). Nobody else's is ever stored; never on share pages.
    upi_id: Mapped[str | None] = mapped_column(String(256))
    # Admin console access given from the console by an owner (the people in ADMIN_EMAILS). ADR 0010.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # Email the person their own plan when a new one is ready (services/plan_email.py). On by default; they can switch it off in Account.
    email_plans: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    referral_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)  # made on first use (ADR 0012)

    subscription: Mapped[Subscription | None] = relationship(back_populates="user", uselist=False,
                                                               cascade="all, delete-orphan")

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def phone_verified(self) -> bool:
        return self.phone_verified_at is not None


class VerificationCode(IdMixin, CreatedMixin, Base):
    """A one-time code sent to an email address or mobile number. Only a keyed hash of the code is stored."""
    __tablename__ = "verification_codes"

    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # email | phone
    target: Mapped[str] = mapped_column(String(255), index=True, nullable=False)  # lower-case email, or +E.164 number
    purpose: Mapped[str] = mapped_column(String(20), default="signup", nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # a keyed hash of the address that asked for the code (never the address itself): lets "10 codes per IP per hour" be counted exactly
    # across workers from the table, without a second store
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)


class Subscription(IdMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True,
                                               nullable=False)
    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)  # free | plus
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|canceled|expired
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trip_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # one-time trip purchases
    provider: Mapped[str] = mapped_column(String(30), default="mock", nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(120))

    user: Mapped[User] = relationship(back_populates="subscription")


class LLMUsage(IdMixin, CreatedMixin, Base):
    __tablename__ = "llm_usage"

    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    trip_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    guard_violations: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
