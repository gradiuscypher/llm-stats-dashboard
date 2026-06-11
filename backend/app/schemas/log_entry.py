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
    cache_read_tokens: int = 0  # proxy-populated: cached prompt tokens (KV-cache hits)
    cache_write_tokens: int = 0  # proxy-populated: tokens written to cache


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
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_total: float | None
    cost_currency: str
    cost_source: str
    latency_ms: int | None
    status: str
    client_timestamp: datetime | None
    created_at: datetime
    modification_count: int = 0
    diff_count: int = 0

    model_config = {"from_attributes": True}


class ModificationPublic(BaseModel):
    """DEPRECATED — kept for schema compatibility. Use MessageDiffPublic instead."""

    id: uuid.UUID
    plugin_name: str
    target: str  # "request" | "response"
    message_index: int | None = None
    message_role: str | None = None
    summary: str
    detail: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageDiffPublic(BaseModel):
    """An original→final content diff for a single request message."""

    id: uuid.UUID
    message_index: int
    role: str | None = None
    change_kind: str  # "modified" | "added" | "removed"
    original_content: Any
    final_content: Any
    modified_by: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class LogEntryDetail(LogEntryPublic):
    """Full detail including request/response bodies."""

    request: dict[str, Any]
    response: dict[str, Any]
    tool_calls: list[Any]
    error: str | None
    metadata_extra: dict[str, Any]
    modifications: list[ModificationPublic] = []
    request_diffs: list[MessageDiffPublic] = []


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
    # Sum of compression token savings across all calls in the conversation.
    # Only counts calls that have metadata_extra.compression.tokens_saved set.
    tokens_saved: int = 0
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
    content: Any  # str | list[MessagePart] | dict — canonical (original) content
    reasoning: str | None = None
    reasoning_details: list[Any] | None = None
    # Which call introduced this message (None for messages shared with parent)
    introduced_by_entry_id: uuid.UUID | None = None
    # 1-based index of the call that introduced this message
    introduced_by_call_index: int | None = None
    # Plugin names that modified this message
    modified_by: list[str] = []
    # Original content (before transforms) — populated only when modified.
    # UI diff toggle uses this to render original→final visual diff.
    original_content: Any | None = None
    # Final content (what was sent upstream after transforms) — populated only
    # when modified.  The UI renders this as the "sent to model" side of the diff.
    modified_content: Any | None = None


class CallDivider(BaseModel):
    """Metadata marker injected between messages at a call boundary."""

    entry_id: uuid.UUID
    call_index: int  # 1-based position in this conversation branch
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_total: float | None
    latency_ms: int | None
    status: str
    created_at: datetime
    modification_count: int = 0
    modifications: list[ModificationPublic] = []
    diff_count: int = 0
    diffs: list[MessageDiffPublic] = []


class TranscriptBranch(BaseModel):
    """A linear sequence of messages sharing a common prefix with the trunk."""

    branch_id: uuid.UUID  # entry_id of the first call that diverges
    messages: list[TranscriptMessage]
    dividers: list[CallDivider]


class CompressionSummary(BaseModel):
    """Aggregate compression savings for a conversation."""

    tokens_before: int
    tokens_after: int
    tokens_saved: int
    compression_ratio: float  # tokens_saved / tokens_before (0–1)
    calls_with_compression: int


class TranscriptResponse(BaseModel):
    """Full conversation transcript — linear or branching.

    For linear conversations: trunk contains all messages, branches is empty.
    For branching conversations: trunk contains shared prefix messages;
    branches contains each diverging path from the fork point.
    """

    conversation_id: str
    trunk: list[TranscriptMessage]
    branches: list[TranscriptBranch]
    dividers: list[CallDivider]  # call markers along the trunk
    total_tokens: int
    total_cost: float | None
    is_branched: bool
    # Aggregate compression savings (null when no call has compression data)
    compression: CompressionSummary | None = None


class DailyStats(BaseModel):
    date: str  # ISO bucket label — granularity varies by request interval
    calls: int
    total_tokens: int
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tokens_saved: int = 0  # compression savings from metadata_extra
    cost: float | None


class ModelStats(BaseModel):
    model: str
    calls: int
    total_tokens: int
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tokens_saved: int = 0  # compression savings from metadata_extra
    cost: float | None


class StatsResponse(BaseModel):
    total_calls: int
    total_tokens: int
    total_prompt_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_tokens_saved: int = 0  # compression savings
    total_cost: float | None
    interval: str = "1d"  # bucket granularity ("5m","1h","1d","1w","1mo")
    since: datetime | None = None
    until: datetime | None = None
    by_day: list[DailyStats]
    by_model: list[ModelStats]
