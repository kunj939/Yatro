"""verification codes remember a keyed hash of the requesting address (send-code hotfix). Expand-only: one nullable column and an index.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07 21:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("verification_codes", sa.Column("requested_ip_hash", sa.String(64), nullable=True))
    op.create_index("ix_verification_codes_requested_ip_hash", "verification_codes", ["requested_ip_hash"])


def downgrade() -> None:
    op.drop_index("ix_verification_codes_requested_ip_hash", table_name="verification_codes")
    op.drop_column("verification_codes", "requested_ip_hash")