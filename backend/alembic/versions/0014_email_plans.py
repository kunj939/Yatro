"""users.email_plans: email a person their own plan when a new one is ready (default on). Expand-only: one defaulted column.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-11 16:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_plans", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "email_plans")