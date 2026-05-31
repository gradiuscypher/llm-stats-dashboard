"""API key model."""

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, String
from sqlmodel import Column, Field, SQLModel


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=64)

    # We store a short prefix shown in UI, and the full hash for lookup
    prefix: str = Field(max_length=16, index=True)  # e.g. "lsd_abc123"
    key_hash: str  # argon2 hash of the full secret

    # e.g. ["logs:write", "logs:read"]
    scopes: list[str] = Field(default_factory=list, sa_column=Column(ARRAY(String)))

    last_used_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
