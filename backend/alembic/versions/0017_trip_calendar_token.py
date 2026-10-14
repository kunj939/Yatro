"""Calendar feed for a trip (ADR 0015): trips.calendar_token, the secret in the feed URL that Google Calendar, Apple
Calendar or Outlook subscribe to. Expand-only: one nullable column and its unique index. Null means no feed.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-14 21:30:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("calendar_token", sa.String(48), nullable=True))
    op.create_index("ix_trips_calendar_token", "trips", ["calendar_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_trips_calendar_token", table_name="trips")
    op.drop_column("trips", "calendar_token")
