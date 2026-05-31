"""Log ingest service."""

import uuid

from sqlmodel import Session

from app.models.log_entry import LogEntry
from app.schemas.log_entry import LogEntryCreate
from app.services.cost import resolve_cost


def ingest_log_entry(payload: LogEntryCreate, user_id: uuid.UUID, db: Session) -> LogEntry:
    """Validate, enrich, and persist one LLM call log entry."""
    cost_total, cost_currency, cost_source = resolve_cost(payload, db)

    entry = LogEntry(
        user_id=user_id,
        conversation_id=payload.conversation_id,
        provider=payload.provider,
        model=payload.model,
        client_timestamp=payload.client_timestamp,
        request=payload.request.model_dump(),
        response=payload.response.model_dump(),
        tool_calls=[tc.model_dump() for tc in payload.tool_calls],
        prompt_tokens=payload.usage.prompt_tokens,
        completion_tokens=payload.usage.completion_tokens,
        total_tokens=payload.usage.total_tokens,
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
