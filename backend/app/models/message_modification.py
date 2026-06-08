"""Message modification records — track plugin mutations to request/response."""

import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel

from app.utils.time import utcnow


class MessageModification(SQLModel, table=True):
    """A record of a plugin mutation made to an LLM request or response message.

    Linked to the log_entry that the modification belongs to, and denormalized
    to user_id for cheap AuthZ filtering.
    """

    __tablename__ = "message_modifications"

    __table_args__ = (
        Index("ix_message_modifications_user_created", "user_id", "created_at"),
        Index("ix_message_modifications_entry", "log_entry_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    log_entry_id: uuid.UUID = Field(foreign_key="log_entries.id")
    user_id: uuid.UUID = Field(foreign_key="users.id")

    plugin_name: str = Field(max_length=64)
    target: str = Field(max_length=16)  # "request" | "response"
    message_index: int | None = Field(default=None)
    message_role: str | None = Field(default=None, max_length=32)

    summary: str = Field(max_length=256)
    detail: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    created_at: datetime = Field(default_factory=utcnow)