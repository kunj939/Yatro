"""share links and the owner's own UPI ID (ADR 0006)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 21:30:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("upi_id", sa.String(256), nullable=True))
    op.create_table(
        "share_links",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False), sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False), sa.Column("token_prefix", sa.String(8), nullable=False),
        sa.Column("show_budget", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("include_split", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_share_links_trip_id", "share_links", ["trip_id"])
    op.create_index("ix_share_links_token_hash", "share_links", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_share_links_token_hash", table_name="share_links")
    op.drop_index("ix_share_links_trip_id", table_name="share_links")
    op.drop_table("share_links")
    with op.batch_alter_table("users") as b:
        b.drop_column("upi_id")
