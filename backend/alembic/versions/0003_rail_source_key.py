"""rail tables keyed per timetable source (irctc1, railradar, ...)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27 15:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # rail_station_lookups: query alone is no longer unique — two sources may both have a "jaipur" lookup
    with op.batch_alter_table("rail_station_lookups") as b:
        b.drop_index("ix_rail_station_lookups_query")
        b.add_column(sa.Column("source", sa.String(length=60), nullable=False, server_default="irctc1"))
        b.alter_column("source", server_default=None)
        b.create_index("ix_rail_station_lookups_query", ["query"])
        b.create_unique_constraint("uq_rail_station_lookup_query_source", ["query", "source"])

    # rail_timetables: from_code/to_code/weekday alone is no longer unique, for the same reason
    with op.batch_alter_table("rail_timetables") as b:
        b.drop_constraint("uq_rail_timetable_route_day", type_="unique")
        b.create_unique_constraint("uq_rail_timetable_route_day_source", ["from_code", "to_code", "weekday", "source"])


def downgrade() -> None:
    with op.batch_alter_table("rail_timetables") as b:
        b.drop_constraint("uq_rail_timetable_route_day_source", type_="unique")
        b.create_unique_constraint("uq_rail_timetable_route_day", ["from_code", "to_code", "weekday"])

    with op.batch_alter_table("rail_station_lookups") as b:
        b.drop_constraint("uq_rail_station_lookup_query_source", type_="unique")
        b.drop_index("ix_rail_station_lookups_query")
        b.drop_column("source")
        b.create_index("ix_rail_station_lookups_query", ["query"], unique=True)