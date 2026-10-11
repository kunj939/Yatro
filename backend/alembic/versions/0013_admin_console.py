"""admin console (ADR 0010): live settings, an append-only audit trail, and an admin flag on users. Expand-only: two new tables and one
defaulted column.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(60), nullable=False), sa.Column("value", JSON, nullable=False),
        sa.Column("reason", sa.String(200), nullable=True), sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("key"))
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True), sa.Column("actor_email", sa.String(255), nullable=False),
        sa.Column("action", sa.String(40), nullable=False), sa.Column("target_type", sa.String(20), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True), sa.Column("target_label", sa.String(255), nullable=True),
        sa.Column("detail", JSON, nullable=True), sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_admin_audit_created", "admin_audit", ["created_at"])
    op.create_index("ix_admin_audit_action", "admin_audit", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_action", table_name="admin_audit")
    op.drop_index("ix_admin_audit_created", table_name="admin_audit")
    op.drop_table("admin_audit")
    op.drop_table("runtime_settings")
    op.drop_column("users", "is_admin")
