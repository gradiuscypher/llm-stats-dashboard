"""Log entry request/response schemas — canonical LLM call format."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Sub-schemas for the canonical message format
# ---------------------------------------------------------------------------

class MessagePart(BaseModel):
    """A single part in a multimodal message (text, image_url, etc.)."""
    type: str
    text: str | None = None
    model_config = {"extra": "allow"}  # allow provider-specific fields


ContentType = str | list[MessagePart] | list[dict[str, Any]]


class CanonicalMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: ContentType
    model_config = {"extra": "allow"}


class RequestPayload(BaseModel):
    messages: list[CanonicalMessage]
    params: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}


class ResponsePayload(BaseModel):
    message: CanonicalMessage
    finish_reason: str | None = None
    model_config = {"extra": "allow"}


class ToolCall(BaseModel):
    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    model_config = {"extra": "allow"}


class UsagePayload(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CostPayload(BaseModel):
    total: float
    currency: str = "USD"


# ---------------------------------------------------------------------------
# Top-level ingest schema
# ---------------------------------------------------------------------------

class LogEntryCreate(BaseModel):
    """
    Canonical log payload. See docs/schemas.md for the full reference.

    Required fields: provider, model, request, response.
    All other fields are optional but strongly recommended.
    """
    conversation_id: str | None = Field(
        default=None,
        description=(
            "Client-supplied identifier grouping multiple calls into one conversation/session. "
            "Use any stable string (UUID, slug, etc.). Required for conversation-level views."
        ),
    )
    provider: str = Field(description="LLM provider identifier, e.g. 'openai', 'anthropic'")
    model: str = Field(description="Model identifier, e.g. 'gpt-4o', 'claude-3-5-sonnet-20241022'")
    client_timestamp: datetime | None = None
    request: RequestPayload
    response: ResponsePayload
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: UsagePayload = Field(default_factory=UsagePayload)
    cost: CostPayload | None = None
    latency_ms: int | None = None
    status: Literal["ok", "error"] = "ok"
    error: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary passthrough metadata (provider-specific extras, client tags, etc.)",
    )

    @model_validator(mode="after")
    def validate_error_status(self) -> "LogEntryCreate":
        if self.status == "error" and not self.error:
            raise ValueError("'error' field is required when status is 'error'")
        return self


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class LogEntryPublic(BaseModel):
    """Summary row returned in list views."""
    id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: str | None
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_total: float | None
    cost_currency: str
    cost_source: str
    latency_ms: int | None
    status: str
    client_timestamp: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LogEntryDetail(LogEntryPublic):
    """Full detail including request/response bodies."""
    request: dict[str, Any]
    response: dict[str, Any]
    tool_calls: list[Any]
    error: str | None
    metadata_extra: dict[str, Any]


class ConversationResponse(BaseModel):
    conversation_id: str
    entries: list[LogEntryDetail]
    total_tokens: int
    total_cost: float | None


class DailyStats(BaseModel):
    date: str
    calls: int
    total_tokens: int
    cost: float | None


class ModelStats(BaseModel):
    model: str
    calls: int
    total_tokens: int
    cost: float | None


class StatsResponse(BaseModel):
    total_calls: int
    total_tokens: int
    total_cost: float | None
    by_day: list[DailyStats]
    by_model: list[ModelStats]
