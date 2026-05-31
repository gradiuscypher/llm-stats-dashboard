"""LLM call log entry model."""

import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class LogEntry(SQLModel, table=True):
    __tablename__ = "log_entries"

    __table_args__ = (
        Index("ix_log_entries_user_created", "user_id", "created_at"),
        Index("ix_log_entries_user_conversation", "user_id", "conversation_id"),
        Index("ix_log_entries_user_model", "user_id", "model"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    # Conversation grouping (client-supplied)
    conversation_id: str | None = Field(default=None, index=True, max_length=256)

    # Provider / model
    provider: str = Field(max_length=64)
    model: str = Field(max_length=128)

    # Canonical request/response blobs (JSONB)
    request: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    response: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    tool_calls: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    # Usage
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    # Cost
    cost_total: float | None = Field(default=None)
    cost_currency: str = Field(default="USD", max_length=8)
    cost_source: str = Field(default="computed", max_length=16)  # "client" | "computed"

    # Optional metadata
    latency_ms: int | None = Field(default=None)
    status: str = Field(default="ok", max_length=16)
    error: str | None = Field(default=None)
    metadata_extra: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    # Timestamps
    client_timestamp: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
