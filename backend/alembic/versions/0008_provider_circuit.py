"""provider circuit state (quota exhausted) for the gateway (ADR 0007)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 23:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_circuit",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=False), sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("provider", "endpoint", name="uq_provider_circuit"))


def downgrade() -> None:
    op.drop_table("provider_circuit")
