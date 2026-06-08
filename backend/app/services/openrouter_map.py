"""OpenRouter ⇄ canonical LogEntryCreate mapping.

Translates OpenRouter request/response shapes into the existing canonical
LogEntryCreate schema so the dashboard read path works with zero changes.
"""

import json as _json
import time
import uuid as _uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlmodel import Session

from app.schemas.log_entry import (
    CanonicalMessage,
    LogEntryCreate,
    RequestPayload,
    ResponsePayload,
    ToolCall,
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


def _extract_tool_calls(choice: dict) -> list[ToolCall]:
    """Extract tool calls from an OpenRouter choice's message."""
    msg = choice.get("message", {})
    tool_calls = msg.get("tool_calls", [])
    result: list[ToolCall] = []
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
            ToolCall(
                id=tc.get("id"),
                name=func.get("name", ""),
                arguments=args,
            )
        )
    return result



def derive_conversation_id(
    request_body: dict,
    api_key_prefix: str,
    explicit: str | None = None,
    *,
    message_ids: Any = None,  # noqa: ARG001 — kept for caller compatibility
    user_id: _uuid.UUID | None = None,
    db: Session | None = None,
) -> str:
    """Derive a stable conversation_id from the request.

    Resolution order:
      1. Explicit X-Conversation-Id header (passed as explicit)
      2. OpenRouter user field / metadata
      3. Prefix-ancestor inheritance via indexed chain_key probes
      4. Mint a fresh UUID-based id (fallback for brand-new threads)

    Delegates to conversation_identity.infer_conversation_id.
    """
    if explicit:
        return explicit

    user_field = request_body.get("user")
    if isinstance(user_field, str) and user_field.strip():
        return f"or-user-{user_field}"

    if user_id is not None and db is not None:
        raw_messages = request_body.get("messages", [])
        if raw_messages:
            from app.services.conversation_identity import infer_conversation_id

            cid, _, _ = infer_conversation_id(
                raw_messages, user_id, db,
            )
            return cid

    return f"or-{_uuid.uuid4().hex[:16]}"


def candidate_conversation_id(
    request_body: dict,
    api_key_prefix: str,
    explicit: str | None = None,
    *,
    user_id: _uuid.UUID | None = None,
    db: Session | None = None,
) -> str:
    """Derive a candidate conversation_id before the call runs.

    Thin wrapper around derive_conversation_id kept for call-site
    compatibility.  Pre-call resolution in the proxy router now calls
    infer_conversation_id directly.
    """
    return derive_conversation_id(
        request_body, api_key_prefix, explicit,
        user_id=user_id, db=db,
    )


def _resolve_prefix_ancestor(
    message_ids: list[_uuid.UUID],
    user_id: _uuid.UUID,
    db: Session,
) -> str | None:
    """Find an existing entry whose message_ids is a proper prefix (DEPRECATED).

    Replaced by indexed chain_key probes in conversation_identity.py.
    Kept as a stub for backward compatibility; returns None.
    """
    return None


def map_to_log_entry(
    ctx: "ProxyContext",
    upstream_response: dict,
    conversation_id_header: str | None = None,
) -> LogEntryCreate:
    """Map an OpenRouter request + response to the canonical LogEntryCreate."""
    # Use the request body for params and conversation derivation.
    # For canonical message interning, use the ORIGINAL (pre-interceptor)
    # messages so conversation identity is stable across plugin toggles.
    request_body = ctx.request_body
    model = ctx.model

    # ---- Request ----
    original_messages = ctx.original_request_messages
    if not original_messages:
        original_messages = request_body.get("messages", [])
    messages = [_to_canonical_message(m) for m in original_messages]
    params = {k: v for k, v in request_body.items() if k not in ("messages", "model")}
    request = RequestPayload(messages=messages, params=params)

    # ---- Response ----
    # Response is verbatim — no snapshotting needed.
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
    # Use the request body for params and conversation derivation.
    # For canonical message interning, use the ORIGINAL (pre-interceptor) messages.
    request_body = ctx.request_body
    model = ctx.model

    original_messages = ctx.original_request_messages
    if not original_messages:
        original_messages = request_body.get("messages", [])
    messages = [_to_canonical_message(m) for m in original_messages]
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
