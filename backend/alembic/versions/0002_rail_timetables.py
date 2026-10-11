"""saved Indian Railways timetables and station lookups

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27 11:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "rail_station_lookups",
        sa.Column("query", sa.String(length=120), nullable=False),
        sa.Column("rows", JSON, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("rail_station_lookups") as b:
        b.create_index("ix_rail_station_lookups_query", ["query"], unique=True)

    op.create_table(
        "rail_timetables",
        sa.Column("from_code", sa.String(length=10), nullable=False),
        sa.Column("to_code", sa.String(length=10), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("rows", JSON, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_code", "to_code", "weekday", name="uq_rail_timetable_route_day"),
    )
    with op.batch_alter_table("rail_timetables") as b:
        b.create_index("ix_rail_timetables_from_code", ["from_code"])
        b.create_index("ix_rail_timetables_to_code", ["to_code"])


def downgrade() -> None:
    op.drop_table("rail_timetables")
    op.drop_table("rail_station_lookups")