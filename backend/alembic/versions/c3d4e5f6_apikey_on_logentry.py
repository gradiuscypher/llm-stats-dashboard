"""Add api_key_id column to log_entries for tracing which key made each call.

Revision ID: c3d4e5f6
Revises: b2e4f7a91c03
Create Date: 2026-06-07 00:00:00.000000

Changes
-------
1. Add `api_key_id` UUID column (nullable FK → api_keys.id) + index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "c3d4e5f6"
down_revision: str | Sequence[str] | None = "b2e4f7a91c03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "log_entries",
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_log_entries_api_key_id",
        "log_entries",
        ["api_key_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_log_entries_api_key_id", table_name="log_entries")
    op.drop_column("log_entries", "api_key_id")
