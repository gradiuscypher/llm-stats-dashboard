"""OpenRouter ⇄ canonical LogEntryCreate mapping.

Translates OpenRouter request/response shapes into the existing canonical
LogEntryCreate schema so the dashboard read path works with zero changes.
"""

import json as _json
import time
import uuid as _uuid
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlmodel import Session, select

from app.models.log_entry import LogEntry
from app.schemas.log_entry import (
    CanonicalMessage,
    LogEntryCreate,
    RequestPayload,
    ResponsePayload,
    UsagePayload,
)

if TYPE_CHECKING:
    from app.proxy.context import ProxyContext


def _to_canonical_message(msg: dict, default_role: str = "user") -> CanonicalMessage:
    """Construct a CanonicalMessage from a raw message dict.

    Normal messages are stored with their structured content intact.
    If the message cannot be validated (e.g. an unrecognized content shape),
    the entire dict is serialized to a plaintext JSON string so that we
    never lose logging data.
    """
    role = msg.get("role", default_role)
    content = msg.get("content", "")
    if content is None:
        content = ""
    reasoning = msg.get("reasoning")
    reasoning_details = msg.get("reasoning_details")
    try:
        return CanonicalMessage(
            role=role,
            content=content,
            reasoning=reasoning,
            reasoning_details=reasoning_details,
        )
    except ValidationError:
        # Fall back: store the whole message as plaintext, no processing.
        return CanonicalMessage(
            role=str(role),
            content=_json.dumps(msg, ensure_ascii=False, default=str),
        )


def _extract_message_from_choice(choice: dict) -> CanonicalMessage:
    """Extract a CanonicalMessage from an OpenRouter choice's message field."""
    msg = choice.get("message", {})
    return _to_canonical_message(msg, default_role="assistant")


def _extract_tool_calls(choice: dict) -> list[dict]:
    """Extract tool calls from an OpenRouter choice's message."""
    msg = choice.get("message", {})
    tool_calls = msg.get("tool_calls", [])
    result: list[dict] = []
    for tc in tool_calls:
        func = tc.get("function", {})
        raw_args = func.get("arguments", {})
        # OpenRouter sends arguments as a JSON string; canonical schema expects a dict
        if isinstance(raw_args, str):
            try:
                args = _json.loads(raw_args)
            except _json.JSONDecodeError:
                args = {}
        else:
            args = raw_args if isinstance(raw_args, dict) else {}
        result.append(
            {
                "id": tc.get("id"),
                "name": func.get("name", ""),
                "arguments": args,
            }
        )
    return result


def derive_conversation_id(
    request_body: dict,
    api_key_prefix: str,
    explicit: str | None = None,
    *,
    message_ids: list[_uuid.UUID] | None = None,
    user_id: _uuid.UUID | None = None,
    db: Session | None = None,
) -> str:
    """Derive a stable conversation_id from the request.

    Resolution order:
      1. Explicit X-Conversation-Id header (passed as explicit)
      2. OpenRouter user field / metadata
      3. Prefix-ancestor inheritance (when DB is available):
         find an existing entry whose message_ids is a proper prefix
         of the new request's message_ids. This chains new turns to the
         same conversation structurally, matching how stateless chat APIs
         work (clients resend full history with each turn).
      4. Mint a fresh UUID-based id — guarantees unrelated sessions with
         coincidentally similar opening messages never merge.

    Returns a stable string id.
    """
    # 1. Explicit header
    if explicit:
        return explicit

    # 2. OpenRouter user field
    user_field = request_body.get("user")
    if isinstance(user_field, str) and user_field.strip():
        return f"or-user-{user_field}"

    # 3. Prefix-ancestor inheritance (requires DB + interned messages)
    if message_ids and db is not None and user_id is not None:
        parent_conv_id = _resolve_prefix_ancestor(message_ids, user_id, db)
        if parent_conv_id is not None:
            return parent_conv_id

    # 4. Mint fresh id — no structural link, so this is a new conversation
    return f"or-{_uuid.uuid4().hex[:16]}"


def _resolve_prefix_ancestor(
    message_ids: list[_uuid.UUID],
    user_id: _uuid.UUID,
    db: Session,
) -> str | None:
    """Find an existing entry whose message_ids is a proper prefix.

    Returns the entry's conversation_id if found, None otherwise.
    Scans recent entries (same user) ordered by creation time descending
    so the most-recent prefix-match wins when there are ties.
    """
    if len(message_ids) < 2:
        return None

    candidates = db.exec(
        select(LogEntry)
        .where(
            LogEntry.user_id == user_id,
            LogEntry.conversation_id.is_not(None),
        )
        .order_by(LogEntry.created_at.desc())
    ).all()

    # Sort by prefix length descending for greedy longest-match
    candidates.sort(key=lambda e: len(e.message_ids), reverse=True)

    for candidate in candidates:
        prefix = candidate.message_ids
        if not prefix:
            continue
        if len(prefix) >= len(message_ids):
            continue  # must be a *proper* prefix
        if message_ids[: len(prefix)] == prefix:
            return candidate.conversation_id

    return None


def map_to_log_entry(
    ctx: "ProxyContext",
    upstream_response: dict,
    conversation_id_header: str | None = None,
) -> LogEntryCreate:
    """Map an OpenRouter request + response to the canonical LogEntryCreate."""
    request_body = ctx.request_body
    model = ctx.model

    # ---- Request ----
    messages = [_to_canonical_message(m) for m in request_body.get("messages", [])]
    params = {k: v for k, v in request_body.items() if k not in ("messages", "model")}
    request = RequestPayload(messages=messages, params=params)

    # ---- Response ----
    choices = upstream_response.get("choices", [])
    if choices:
        response_message = _extract_message_from_choice(choices[0])
        finish_reason = choices[0].get("finish_reason")
        tool_calls = _extract_tool_calls(choices[0])
    else:
        response_message = _to_canonical_message({"role": "assistant", "content": ""})
        finish_reason = None
        tool_calls = []

    response = ResponsePayload(message=response_message, finish_reason=finish_reason)

    # ---- Usage ----
    usage = upstream_response.get("usage", {})
    details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = details.get("reasoning_tokens", 0) or 0
    usage_payload = UsagePayload(
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        reasoning_tokens=reasoning_tokens,
    )

    # ---- Cost ----
    # OpenRouter returns native cost in usage when available
    cost = None
    cost_from_usage = usage.get("cost")
    if cost_from_usage is not None:
        from app.schemas.log_entry import CostPayload

        cost = CostPayload(total=cost_from_usage, currency="USD")

    # ---- Conversation ID ----
    conversation_id = derive_conversation_id(
        request_body,
        ctx.api_key.prefix,
        explicit=conversation_id_header,
    )

    # ---- Latency ----
    latency_ms = int((time.time() - ctx.started_at) * 1000)

    return LogEntryCreate(
        conversation_id=conversation_id,
        provider="openrouter",
        model=model,
        client_timestamp=None,
        request=request,
        response=response,
        tool_calls=tool_calls,
        usage=usage_payload,
        cost=cost,
        latency_ms=latency_ms,
        status="ok",
        error=None,
        metadata={
            "upstream_request_id": upstream_response.get("id"),
            "upstream_model": upstream_response.get("model"),
            "compression": ctx.state.get("compression", {}),
        },
    )


def map_error_to_log_entry(
    ctx: "ProxyContext",
    error: Exception,
    conversation_id_header: str | None = None,
) -> LogEntryCreate:
    """Map a failed request to a LogEntryCreate with status='error'."""
    request_body = ctx.request_body
    model = ctx.model

    messages = [_to_canonical_message(m) for m in request_body.get("messages", [])]
    params = {k: v for k, v in request_body.items() if k not in ("messages", "model")}
    request = RequestPayload(messages=messages, params=params)

    response = ResponsePayload(
        message=_to_canonical_message({"role": "assistant", "content": ""}),
        finish_reason=None,
    )

    conversation_id = derive_conversation_id(
        request_body,
        ctx.api_key.prefix,
        explicit=conversation_id_header,
    )

    latency_ms = int((time.time() - ctx.started_at) * 1000)

    return LogEntryCreate(
        conversation_id=conversation_id,
        provider="openrouter",
        model=model,
        client_timestamp=None,
        request=request,
        response=response,
        tool_calls=[],
        usage=UsagePayload(),
        cost=None,
        latency_ms=latency_ms,
        status="error",
        error=str(error),
        metadata={
            "compression": ctx.state.get("compression", {}),
        },
    )
