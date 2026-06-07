"""add reasoning_tokens to log_entries

Revision ID: a0b994911912
Revises: c3d4e5f6
Create Date: 2026-06-07 23:12:09.584875

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b994911912'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'log_entries',
        sa.Column('reasoning_tokens', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('log_entries', 'reasoning_tokens')
