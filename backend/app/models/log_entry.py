"""LLM call log entry model."""

import uuid
from datetime import datetime

from sqlalchemy import UUID as SA_UUID
from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Column, Field, SQLModel

from app.utils.time import utcnow


class LogEntry(SQLModel, table=True):
    __tablename__ = "log_entries"

    __table_args__ = (
        Index("ix_log_entries_user_created", "user_id", "created_at"),
        Index("ix_log_entries_user_conversation", "user_id", "conversation_id"),
        Index("ix_log_entries_user_model", "user_id", "model"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    # Which API key made this call (nullable — old rows / non-key paths may not have one)
    api_key_id: uuid.UUID | None = Field(default=None, foreign_key="api_keys.id", index=True)

    # Conversation grouping — inferred from request structure by default;
    # explicit X-Conversation-Id / OpenRouter user field overrides.
    conversation_id: str | None = Field(default=None, index=True, max_length=256)

    # Chain key for request-prefix-based conversation identity (see plans/).
    # sha256 hex over all turn_keys of this entry's request.
    # Indexed with user_id so future turns probe it as a prefix in O(1).
    chain_key: str | None = Field(default=None, max_length=64)
    # Chain key up to the last user anchor (for retry / branch matching).
    chain_prefix_key: str | None = Field(default=None, max_length=64)

    # Ordered list of interned message UUIDs for the request (deduped across calls).
    # Stored as a Postgres ARRAY so order is native; add GIN index later if reverse
    # lookup ("which entries reference message X") becomes needed.
    message_ids: list[uuid.UUID] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(SA_UUID(as_uuid=True)), nullable=False, server_default="{}"),
    )

    # The log entry whose message_ids is the longest prefix of this entry's message_ids.
    # None for the first call in a conversation or when the parent can't be determined.
    # Used to reconstruct the conversation tree (branching / retry detection).
    parent_entry_id: uuid.UUID | None = Field(
        default=None, foreign_key="log_entries.id", index=True
    )

    # Provider / model
    provider: str = Field(max_length=64)
    model: str = Field(max_length=128)

    # request stores params + extras but NOT messages (those live in the messages table).
    # response and tool_calls are per-call unique so they stay inline.
    request: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    response: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    tool_calls: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    # Usage
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    reasoning_tokens: int = Field(default=0)

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
    created_at: datetime = Field(default_factory=utcnow)
