"""count plans that finished after their refund (ADR 0008). Expand-only: one nullable column.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_jobs", sa.Column("late_finish_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("plan_jobs", "late_finish_at")