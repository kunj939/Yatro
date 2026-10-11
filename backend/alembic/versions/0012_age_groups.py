"""which age groups are in the party (kids / teens / adults / seniors). Expand-only: one nullable column.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("trip_preferences", sa.Column("age_groups", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("trip_preferences", "age_groups")
