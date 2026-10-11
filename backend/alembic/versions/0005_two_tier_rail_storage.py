"""two-tier rail storage: Tier 1 route knowledge, Tier 2 timetable columns, reports, daily stats (ADR 0003)

Also removes stored IRCTC1 data (I7: its terms do not confirm storage; approved 2026-10-06). That deletion is not
restored on downgrade: the data can be re-fetched live.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _base():
    return [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id")]


def upgrade() -> None:
    with op.batch_alter_table("rail_timetables") as b:
        b.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("validation", JSON, nullable=True))
        b.add_column(sa.Column("needs_refresh", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("refresh_priority", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True))
        b.create_index("ix_rail_timetables_expires_at", ["expires_at"])

    conn = op.get_bind()
    # I7: IRCTC1 has not confirmed storage; remove what was kept (approved). Re-fetchable live.
    conn.execute(sa.text("DELETE FROM rail_timetables WHERE source = 'irctc1'"))
    conn.execute(sa.text("DELETE FROM rail_station_lookups WHERE source = 'irctc1'"))
    conn.execute(sa.text("DELETE FROM api_cache WHERE provider = 'irctc1'"))
    # Existing rows were saved before validation: mark them legacy (validated on read) and give them the 45-day limit.
    rows = conn.execute(sa.text("SELECT id, fetched_at FROM rail_timetables")).fetchall()
    import json
    from datetime import UTC, datetime, timedelta
    for rid, fetched in rows:
        if isinstance(fetched, str):
            fetched = datetime.fromisoformat(fetched)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        conn.execute(sa.text("UPDATE rail_timetables SET expires_at = :e, last_verified_at = :f, validation = :v WHERE id = :i"),
                     {"e": fetched + timedelta(days=45), "f": fetched, "v": json.dumps({"status": "legacy"}), "i": rid})

    op.create_table(
        "rail_city_routes", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(60), nullable=False), sa.Column("origin_key", sa.String(120), nullable=False),
        sa.Column("dest_key", sa.String(120), nullable=False), sa.Column("working_pairs", JSON, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "origin_key", "dest_key", name="uq_rail_city_route"))
    op.create_table(
        "rail_pair_results", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(60), nullable=False), sa.Column("from_code", sa.String(10), nullable=False),
        sa.Column("to_code", sa.String(10), nullable=False), sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False), sa.Column("train_numbers", JSON, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "from_code", "to_code", "weekday", name="uq_rail_pair_result"))
    op.create_table(
        "rail_train_reports", *_base(),
        sa.Column("trip_id", sa.Uuid(), sa.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(60), nullable=False), sa.Column("from_code", sa.String(10), nullable=False),
        sa.Column("to_code", sa.String(10), nullable=False), sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("train_number", sa.String(10), nullable=True), sa.Column("problem", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True))
    op.create_index("ix_rail_train_reports_trip_id", "rail_train_reports", ["trip_id"])
    op.create_table(
        "rail_daily_stats", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.String(10), nullable=False), sa.Column("source", sa.String(60), nullable=False),
        sa.Column("counters", JSON, nullable=False),
        sa.UniqueConstraint("day", "source", name="uq_rail_daily_stat"))


def downgrade() -> None:
    op.drop_table("rail_daily_stats")
    op.drop_index("ix_rail_train_reports_trip_id", table_name="rail_train_reports")
    op.drop_table("rail_train_reports")
    op.drop_table("rail_pair_results")
    op.drop_table("rail_city_routes")
    with op.batch_alter_table("rail_timetables") as b:
        b.drop_index("ix_rail_timetables_expires_at")
        b.drop_column("reported_at")
        b.drop_column("refresh_priority")
        b.drop_column("needs_refresh")
        b.drop_column("validation")
        b.drop_column("last_verified_at")
        b.drop_column("expires_at")
