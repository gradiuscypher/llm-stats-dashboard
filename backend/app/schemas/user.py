"""User request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class UserCreate(BaseModel):
    username: str
    email: str | None = None
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("username cannot be empty")
        if len(v) > 64:
            raise ValueError("username must be 64 characters or fewer")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("username may only contain letters, numbers, hyphens and underscores")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class UserUpdate(BaseModel):
    email: str | None = None
    password: str | None = None


class UserPublic(BaseModel):
    id: uuid.UUID
    username: str
    email: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
