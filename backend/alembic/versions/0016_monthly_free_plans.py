"""Free plans reset every month (ADR 0013): users.free_period records which month free_plans_used counts. Expand-only:
one nullable column. A user whose period is not the current month starts the month at zero.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-14 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("free_period", sa.String(7), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "free_period")
