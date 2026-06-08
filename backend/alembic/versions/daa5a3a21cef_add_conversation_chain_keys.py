"""add conversation chain keys

Revision ID: daa5a3a21cef
Revises: 246f8a09693d
Create Date: 2026-06-08 03:26:27.778398

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'daa5a3a21cef'
down_revision: str | Sequence[str] | None = '246f8a09693d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- chain_key: hash over all turn keys of this entry's request ---
    op.add_column(
        "log_entries",
        sa.Column("chain_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_log_entries_user_chain_key",
        "log_entries",
        ["user_id", "chain_key"],
        postgresql_where=sa.text("chain_key IS NOT NULL"),
    )

    # --- chain_prefix_key: hash up to the last user anchor ---
    op.add_column(
        "log_entries",
        sa.Column("chain_prefix_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_log_entries_user_chain_prefix",
        "log_entries",
        ["user_id", "chain_prefix_key"],
        postgresql_where=sa.text("chain_prefix_key IS NOT NULL"),
    )

    # Pre-existing FK that wasn't in a prior migration (harmless to add now).
    op.create_foreign_key(None, "log_entries", "api_keys", ["api_key_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(None, "log_entries", type_="foreignkey")
    op.drop_index("ix_log_entries_user_chain_prefix", table_name="log_entries")
    op.drop_column("log_entries", "chain_prefix_key")
    op.drop_index("ix_log_entries_user_chain_key", table_name="log_entries")
    op.drop_column("log_entries", "chain_key")
