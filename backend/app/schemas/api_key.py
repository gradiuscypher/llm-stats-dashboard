"""API key request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

VALID_SCOPES = {"logs:write", "logs:read", "proxy:use"}


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str]

    @field_validator("scopes")
    @classmethod
    def scopes_valid(cls, v: list[str]) -> list[str]:
        invalid = set(v) - VALID_SCOPES
        if invalid:
            raise ValueError(f"invalid scopes: {invalid}. valid: {VALID_SCOPES}")
        if not v:
            raise ValueError("at least one scope is required")
        return v

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        if len(v) > 64:
            raise ValueError("name must be 64 characters or fewer")
        return v.strip()


class ApiKeyPublic(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyPublic):
    """Returned once on creation. raw_key is not stored and cannot be retrieved again."""

    raw_key: str
