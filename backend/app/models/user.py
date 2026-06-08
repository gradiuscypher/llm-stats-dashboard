"""User model."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.utils.time import utcnow


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    email: str | None = Field(default=None, unique=True, index=True)
    password_hash: str

    # OAuth scaffolding (disabled for MVP)
    auth_provider: str = Field(default="local", max_length=32)
    oauth_subject: str | None = Field(default=None)
    oauth_provider: str | None = Field(default=None)

    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
