"""Invite-a-friend referrals (ADR 0012): a referral code on each user and a referrals table. Expand-only: one nullable
column with a unique index, and a new table.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-13 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referral_code", sa.String(16), nullable=True))
    op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)
    op.create_table(
        "referrals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inviter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("friend_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="joined"),
        sa.Column("friend_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inviter_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("friend_id", name="uq_referrals_friend_id"),
    )
    op.create_index("ix_referrals_inviter_id", "referrals", ["inviter_id"])


def downgrade() -> None:
    op.drop_index("ix_referrals_inviter_id", table_name="referrals")
    op.drop_table("referrals")
    op.drop_index("ix_users_referral_code", table_name="users")
    op.drop_column("users", "referral_code")
