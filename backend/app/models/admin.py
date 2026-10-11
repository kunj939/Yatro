"""The admin console's own tables (ADR 0010): live settings an admin can change without a restart, and the trail of what they did.

Neither table holds personal data beyond the admin's own email (for the trail) and an id of whoever was acted on.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, JSONType


class RuntimeSetting(Base):
    """One live setting: a kill switch (`switch:signups` -> true) or a number (`brownout_level` -> 2). A row means "an admin
    chose this"; no row means the environment or the built-in default applies, so deleting the row gives the control back to them."""
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[object] = mapped_column(JSONType, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)  # the admin's email at the time
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdminAudit(IdMixin, Base):
    """Append-only: nothing in the app updates or deletes a row. No foreign keys on purpose, so the trail outlives the people and
    trips it mentions."""
    __tablename__ = "admin_audit"
    __table_args__ = (Index("ix_admin_audit_created", "created_at"), Index("ix_admin_audit_action", "action", "created_at"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)  # user.update | user.delete | trip.delete | control.switch ...
    target_type: Mapped[str | None] = mapped_column(String(20))  # user | trip | switch | setting
    target_id: Mapped[str | None] = mapped_column(String(64))
    target_label: Mapped[str | None] = mapped_column(String(255))  # what the target was called then (an email, a title, a key)
    detail: Mapped[dict | None] = mapped_column(JSONType)  # {"field": [old, new]} for an edit, or the reason
    reason: Mapped[str | None] = mapped_column(Text)
    ip_hash: Mapped[str | None] = mapped_column(String(64))  # keyed hash of the admin's address, as for sign-up codes
