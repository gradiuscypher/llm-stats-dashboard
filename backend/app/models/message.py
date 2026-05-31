"""Content-addressed message store.

Each unique (user_id, content_hash) pair is stored exactly once.
Log entries reference their ordered message sequence via log_entries.message_ids.
"""

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel, UniqueConstraint


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    __table_args__ = (
        # Dedup is scoped per user so tenants never share rows.
        UniqueConstraint("user_id", "content_hash", name="uq_messages_user_hash"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    # sha256 hex of canonical JSON: json.dumps(message, sort_keys=True, separators=(",", ":"))
    content_hash: str = Field(max_length=64, index=True)

    # Denormalised for cheap filtering (e.g. "show only assistant messages")
    role: str = Field(max_length=32)

    # Full canonical message object — kept as JSONB so SQL can query inside it.
    content: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    created_at: datetime = Field(default_factory=datetime.utcnow)
