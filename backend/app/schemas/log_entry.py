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
    role: str = "user"
    content: ContentType
    reasoning: str | None = None
    reasoning_details: list[dict[str, Any]] | None = None
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
    reasoning_tokens: int = 0


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
    api_key_id: uuid.UUID | None = None
    api_key_name: str | None = None
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0
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


class ConversationSummary(BaseModel):
    """One row in the conversations list — aggregate over all calls in a conversation."""

    conversation_id: str
    call_count: int
    total_tokens: int
    total_cost: float | None
    # Distinct models/providers seen in this conversation (sorted, deduped)
    models: list[str]
    providers: list[str]
    # Whether any call in the conversation errored
    has_error: bool
    first_activity: datetime
    last_activity: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    # Total distinct conversations matching the filters (for pagination UI)
    total: int


# ---------------------------------------------------------------------------
# Transcript schemas (Phase 2 — contiguous conversation view)
# ---------------------------------------------------------------------------

class TranscriptMessage(BaseModel):
    """A single message in the deduped transcript, with call attribution."""
    message_id: uuid.UUID
    role: str
    content: Any  # str | list[MessagePart] | dict
    reasoning: str | None = None
    reasoning_details: list[Any] | None = None
    # Which call introduced this message (None for messages shared with parent)
    introduced_by_entry_id: uuid.UUID | None = None
    # 1-based index of the call that introduced this message
    introduced_by_call_index: int | None = None


class CallDivider(BaseModel):
    """Metadata marker injected between messages at a call boundary."""
    entry_id: uuid.UUID
    call_index: int          # 1-based position in this conversation branch
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0
    cost_total: float | None
    latency_ms: int | None
    status: str
    created_at: datetime


class TranscriptBranch(BaseModel):
    """A linear sequence of messages sharing a common prefix with the trunk."""
    branch_id: uuid.UUID      # entry_id of the first call that diverges
    messages: list[TranscriptMessage]
    dividers: list[CallDivider]


class TranscriptResponse(BaseModel):
    """Full conversation transcript — linear or branching.

    For linear conversations: trunk contains all messages, branches is empty.
    For branching conversations: trunk contains shared prefix messages;
    branches contains each diverging path from the fork point.
    """
    conversation_id: str
    trunk: list[TranscriptMessage]
    branches: list[TranscriptBranch]
    dividers: list[CallDivider]   # call markers along the trunk
    total_tokens: int
    total_cost: float | None
    is_branched: bool


class DailyStats(BaseModel):
    date: str
    calls: int
    total_tokens: int
    reasoning_tokens: int = 0
    cost: float | None


class ModelStats(BaseModel):
    model: str
    calls: int
    total_tokens: int
    reasoning_tokens: int = 0
    cost: float | None


class StatsResponse(BaseModel):
    total_calls: int
    total_tokens: int
    total_reasoning_tokens: int = 0
    total_cost: float | None
    by_day: list[DailyStats]
    by_model: list[ModelStats]
