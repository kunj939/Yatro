"""mobile number and verified email/phone on users; one-time verification codes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 22:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("phone", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
        b.create_index("ix_users_phone", ["phone"], unique=True)

    op.create_table(
        "verification_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_verification_codes_target", "verification_codes", ["target"])


def downgrade() -> None:
    op.drop_index("ix_verification_codes_target", table_name="verification_codes")
    op.drop_table("verification_codes")
    with op.batch_alter_table("users") as b:
        b.drop_index("ix_users_phone")
        b.drop_column("phone_verified_at")
        b.drop_column("email_verified_at")
        b.drop_column("phone")
