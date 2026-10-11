"""Google content timestamps on saved trips, and a daily counter table (ADR 0005)

Items and alternatives that carry Google content get google_fetched_at; existing rows are backfilled from the time they
were planned. Everything else (demo data, our own items) stays NULL and is never gated.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 21:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("itinerary_items", "alternatives"):
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column("google_fetched_at", sa.DateTime(timezone=True), nullable=True))
    conn = op.get_bind()
    google = "(SELECT id FROM places WHERE provider = 'google')"
    conn.execute(sa.text(f"UPDATE itinerary_items SET google_fetched_at = created_at WHERE place_id IN {google}"))
    conn.execute(sa.text(f"UPDATE alternatives SET google_fetched_at = created_at WHERE place_id IN {google}"))
    # "Free time near <a Google place>" has no place of its own: it is Google content when its day's stops are
    conn.execute(sa.text(
        "UPDATE itinerary_items SET google_fetched_at = created_at WHERE kind = 'free' AND itinerary_id IN "
        f"(SELECT i2.itinerary_id FROM itinerary_items i2 WHERE i2.place_id IN {google})"))
    op.create_table(
        "daily_counters",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.String(10), nullable=False), sa.Column("name", sa.String(60), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("day", "name", name="uq_daily_counters"))
    op.create_index("ix_daily_counters_day", "daily_counters", ["day"])


def downgrade() -> None:
    op.drop_index("ix_daily_counters_day", table_name="daily_counters")
    op.drop_table("daily_counters")
    for table in ("alternatives", "itinerary_items"):
        with op.batch_alter_table(table) as b:
            b.drop_column("google_fetched_at")
