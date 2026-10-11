"""Plan jobs: one row per plan request, written in the same transaction as the quota change (the transactional outbox
and the quota saga, ADR 0008), and a refund record keyed by job id so a refund can never run twice."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class PlanJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "plan_jobs"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_plan_job_key"),
                      Index("ix_plan_jobs_state_lane", "state", "lane", "created_at"))

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="new")  # new | replan
    lane: Mapped[int] = mapped_column(Integer, nullable=False, default=2)  # 0 replan, 1 paid, 2 free new plan
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    # pending | running | done | failed | refunded
    quota_source: Mapped[str | None] = mapped_column(String(20))  # what was reserved for this job (free | plus | credit)
    change_note: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)  # real run time, for the wait estimate
    result_status: Mapped[str | None] = mapped_column(String(20))  # ready | infeasible | unavailable | error code
    error_code: Mapped[str | None] = mapped_column(String(60))
    # set when a plan finished AFTER its job had been refunded or re-queued (a stall longer than the heartbeat limit): the person keeps
    # the plan and stays refunded; this is how those are counted (ADR 0008)
    late_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanRefund(IdMixin, Base):
    """At most one row per job (the unique key), so a quota refund is exactly-once however many parties try."""
    __tablename__ = "plan_refunds"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("plan_jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)  # failed | infeasible | unavailable | stale | crashed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
