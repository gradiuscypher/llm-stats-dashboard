"""Log ingest service."""

import uuid

from sqlmodel import Session

from app.models.log_entry import LogEntry
from app.schemas.log_entry import LogEntryCreate
from app.services.cost import resolve_cost
from app.services.messages import intern_messages, resolve_parent_entry_id


def ingest_log_entry(
    payload: LogEntryCreate,
    user_id: uuid.UUID,
    db: Session,
    api_key_id: uuid.UUID | None = None,
    *,
    chain_key: str | None = None,
    chain_prefix_key: str | None = None,
) -> LogEntry:
    """Validate, enrich, and persist one LLM call log entry.

    Message deduplication strategy
    --------------------------------
    The request.messages list is stripped from the inline request blob and
    stored in the content-addressed `messages` table instead.  Each unique
    (user_id, message) pair is stored exactly once; the entry records an
    ordered list of UUIDs (message_ids) that reconstruct the full history.

    This eliminates the O(n²) redundancy of multi-turn agent sessions where
    every call resends the entire message history.
    """
    cost_total, cost_currency, cost_source = resolve_cost(payload, db)

    # --- Intern messages ---------------------------------------------------
    # Intern both request and response messages so the full ordered history
    # (including the assistant reply) is available for the transcript path.
    raw_messages = [m.model_dump(exclude_none=True) for m in payload.request.messages]
    raw_response = payload.response.message.model_dump(exclude_none=True)
    raw_messages.append(raw_response)
    message_ids = intern_messages(raw_messages, user_id, db)

    # Strip messages from the request blob; keep params + extras only.
    request_blob = payload.request.model_dump()
    request_blob.pop("messages", None)

    # --- Build entry (without id so we can resolve parent after) -----------
    entry_id = uuid.uuid4()

    parent_entry_id = resolve_parent_entry_id(
        message_ids=message_ids,
        conversation_id=payload.conversation_id,
        user_id=user_id,
        current_entry_id=entry_id,
        db=db,
    )

    # Compute chain keys from request messages if not provided (push API path).
    if chain_key is None and chain_prefix_key is None and payload.request.messages:
        from app.services.conversation_identity import compute_chain_keys

        raw_req = [m.model_dump(exclude_none=True) for m in payload.request.messages]
        ck = compute_chain_keys(raw_req)
        chain_key = ck.chain_key
        chain_prefix_key = ck.chain_prefix_key

    entry = LogEntry(
        id=entry_id,
        user_id=user_id,
        api_key_id=api_key_id,
        conversation_id=payload.conversation_id,
        chain_key=chain_key,
        chain_prefix_key=chain_prefix_key,
        message_ids=message_ids,
        parent_entry_id=parent_entry_id,
        provider=payload.provider,
        model=payload.model,
        client_timestamp=payload.client_timestamp,
        request=request_blob,
        response=payload.response.model_dump(),
        tool_calls=[tc.model_dump() for tc in payload.tool_calls],
        prompt_tokens=payload.usage.prompt_tokens,
        completion_tokens=payload.usage.completion_tokens,
        total_tokens=payload.usage.total_tokens,
        reasoning_tokens=payload.usage.reasoning_tokens,
        cost_total=cost_total,
        cost_currency=cost_currency,
        cost_source=cost_source,
        latency_ms=payload.latency_ms,
        status=payload.status,
        error=payload.error,
        metadata_extra=payload.metadata,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
