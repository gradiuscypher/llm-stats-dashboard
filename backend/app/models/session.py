"""Server-side session model."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.utils.time import utcnow


class UserSession(SQLModel, table=True):
    __tablename__ = "user_sessions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    csrf_secret: str  # used to validate CSRF tokens
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked: bool = Field(default=False)
