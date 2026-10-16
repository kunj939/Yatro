"""Plan together (ADR 0022): group_plans, group_plan_options, group_plan_replies. Expand-only: three new tables.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-16 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "group_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("origin_name", sa.String(255), nullable=False),
        sa.Column("origin_place_id", sa.String(255)),
        sa.Column("origin_lat", sa.Float()),
        sa.Column("origin_lng", sa.Float()),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("budget_hint", sa.Numeric(12, 2)),
        sa.Column("stay", sa.String(20), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trip_id", sa.Uuid(), sa.ForeignKey("trips.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_group_plans_owner_id", "group_plans", ["owner_id"])
    op.create_table(
        "group_plan_options",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("group_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("place_id", sa.String(255)),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
    )
    op.create_index("ix_group_plan_options_group_id", "group_plan_options", ["group_id"])
    op.create_table(
        "group_plan_replies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("group_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("budget", sa.Numeric(12, 2)),
        sa.Column("food", sa.String(10), nullable=False),
        sa.Column("dates", JSONType, nullable=False),
        sa.Column("votes", JSONType, nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_group_plan_replies_group_id", "group_plan_replies", ["group_id"])
    op.create_index("ix_group_plan_replies_device_hash", "group_plan_replies", ["device_hash"])


def downgrade() -> None:
    op.drop_table("group_plan_replies")
    op.drop_table("group_plan_options")
    op.drop_table("group_plans")
