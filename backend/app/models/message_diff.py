"""MessageDiff — per-message original→final diff tracking for request interception.

Replaces the older message_modifications table for request-side transforms.
The older table is deprecated but retained for historical data.

Each MessageDiff records the original (client-sent) and final (sent-to-LLM) content
of a single message, along with the plugin chain that modified it.
"""

import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel

from app.utils.time import utcnow


class MessageDiff(SQLModel, table=True):
    """Original vs final content diff for a single request message, with attribution.

    One row per modified message per log entry.  An entry with N modified messages
    will have N MessageDiff rows.
    """

    __tablename__ = "message_diffs"

    __table_args__ = (
        Index("ix_message_diffs_user_created", "user_id", "created_at"),
        Index("ix_message_diffs_entry", "log_entry_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    log_entry_id: uuid.UUID = Field(foreign_key="log_entries.id")
    user_id: uuid.UUID = Field(foreign_key="users.id")

    message_index: int
    role: str | None = Field(default=None, max_length=32)
    change_kind: str = Field(max_length=16)  # "modified" | "added" | "removed"

    # Full message objects: what the client sent vs what was sent to the LLM.
    original_content: dict = Field(sa_column=Column(JSONB, nullable=False))
    final_content: dict = Field(sa_column=Column(JSONB, nullable=False))

    # Ordered list of plugin names that contributed to this diff.
    modified_by: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    created_at: datetime = Field(default_factory=utcnow)
