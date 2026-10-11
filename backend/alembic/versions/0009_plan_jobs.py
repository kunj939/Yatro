"""plan jobs and refund records (the quota saga and outbox, ADR 0008). Expand-only.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_jobs",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False), sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False), sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False), sa.Column("state", sa.String(12), nullable=False),
        sa.Column("quota_source", sa.String(20), nullable=True), sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True), sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_status", sa.String(20), nullable=True), sa.Column("error_code", sa.String(60), nullable=True),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("user_id", "idempotency_key", name="uq_plan_job_key"))
    op.create_index("ix_plan_jobs_trip_id", "plan_jobs", ["trip_id"])
    op.create_index("ix_plan_jobs_state_lane", "plan_jobs", ["state", "lane", "created_at"])
    op.create_table(
        "plan_refunds",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["plan_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("job_id"))


def downgrade() -> None:
    op.drop_table("plan_refunds")
    op.drop_index("ix_plan_jobs_state_lane", table_name="plan_jobs")
    op.drop_index("ix_plan_jobs_trip_id", table_name="plan_jobs")
    op.drop_table("plan_jobs")
