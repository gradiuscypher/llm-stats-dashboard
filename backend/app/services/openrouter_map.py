"""OpenRouter ⇄ canonical LogEntryCreate mapping.

Translates OpenRouter request/response shapes into the existing canonical
LogEntryCreate schema so the dashboard read path works with zero changes.
"""

import hashlib
import time

from app.schemas.log_entry import (
    CanonicalMessage,
    LogEntryCreate,
    RequestPayload,
    ResponsePayload,
    UsagePayload,
)


def _extract_message_from_choice(choice: dict) -> CanonicalMessage:
    """Extract a CanonicalMessage from an OpenRouter choice's message field."""
    msg = choice.get("message", {})
    role = msg.get("role", "assistant")
    content = msg.get("content", "")
    # CanonicalMessage requires content to be a string or list, not None
    if content is None:
        content = ""
    return CanonicalMessage(role=role, content=content)


def _extract_tool_calls(choice: dict) -> list[dict]:
    """Extract tool calls from an OpenRouter choice's message."""
    import json as _jsonlib
    msg = choice.get("message", {})
    tool_calls = msg.get("tool_calls", [])
    result: list[dict] = []
    for tc in tool_calls:
        func = tc.get("function", {})
        raw_args = func.get("arguments", {})
        # OpenRouter sends arguments as a JSON string; canonical schema expects a dict
        if isinstance(raw_args, str):
            try:
                args = _jsonlib.loads(raw_args)
            except _jsonlib.JSONDecodeError:
                args = {}
        else:
            args = raw_args if isinstance(raw_args, dict) else {}
        result.append({
            "id": tc.get("id"),
            "name": func.get("name", ""),
            "arguments": args,
        })
    return result


def derive_conversation_id(
    request_body: dict,
    api_key_prefix: str,
    explicit: str | None = None,
) -> str:
    """Derive a stable conversation_id from the request.

    Resolution order:
      1. Explicit X-Conversation-Id header (passed as explicit)
      2. OpenRouter user field / metadata
      3. Hash of leading system + first user message, salted per api_key

    Returns a stable string id.
    """
    # 1. Explicit header
    if explicit:
        return explicit

    # 2. OpenRouter user field
    user_field = request_body.get("user")
    if isinstance(user_field, str) and user_field.strip():
        return f"or-user-{user_field}"

    # 3. Derived hash
    messages = request_body.get("messages", [])
    # Take system message(s) + first user message as hash input
    hash_parts: list[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                hash_parts.append(content)
        elif role == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                hash_parts.append(content)
            break  # only first user message

    if not hash_parts:
        # Fallback: hash the full first message
        if messages:
            hash_parts.append(str(messages[0]))

    # Salt with api_key prefix so different keys never share buckets
    salted = api_key_prefix + "|" + "|".join(hash_parts)
    digest = hashlib.sha256(salted.encode()).hexdigest()[:16]
    return f"or-derived-{digest}"


def map_to_log_entry(
    ctx,
    upstream_response: dict,
    conversation_id_header: str | None = None,
) -> LogEntryCreate:
    """Map an OpenRouter request + response to the canonical LogEntryCreate."""
    request_body = ctx.request_body
    model = ctx.model

    # ---- Request ----
    messages = [
        CanonicalMessage(role=m.get("role", "user"), content=m.get("content", ""))
        for m in request_body.get("messages", [])
    ]
    params = {k: v for k, v in request_body.items() if k not in ("messages", "model")}
    request = RequestPayload(messages=messages, params=params)

    # ---- Response ----
    choices = upstream_response.get("choices", [])
    if choices:
        response_message = _extract_message_from_choice(choices[0])
        finish_reason = choices[0].get("finish_reason")
        tool_calls = _extract_tool_calls(choices[0])
    else:
        response_message = CanonicalMessage(role="assistant", content="")
        finish_reason = None
        tool_calls = []

    response = ResponsePayload(message=response_message, finish_reason=finish_reason)

    # ---- Usage ----
    usage = upstream_response.get("usage", {})
    usage_payload = UsagePayload(
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
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
    ctx,
    error: Exception,
    conversation_id_header: str | None = None,
) -> LogEntryCreate:
    """Map a failed request to a LogEntryCreate with status='error'."""
    request_body = ctx.request_body
    model = ctx.model

    messages = [
        CanonicalMessage(role=m.get("role", "user"), content=m.get("content", ""))
        for m in request_body.get("messages", [])
    ]
    params = {k: v for k, v in request_body.items() if k not in ("messages", "model")}
    request = RequestPayload(messages=messages, params=params)

    response = ResponsePayload(
        message=CanonicalMessage(role="assistant", content=""),
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
