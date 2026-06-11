"""add cache token columns to log_entries

Revision ID: e46433eb054a
Revises: dbdddb6fd90c
Create Date: 2026-06-10 16:00:04.413163

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e46433eb054a'
down_revision: Union[str, Sequence[str], None] = 'dbdddb6fd90c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'log_entries',
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'log_entries',
        sa.Column('cache_write_tokens', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('log_entries', 'cache_write_tokens')
    op.drop_column('log_entries', 'cache_read_tokens')
