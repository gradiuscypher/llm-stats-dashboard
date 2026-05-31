"""Log ingestion and retrieval endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session, select

from app.db import get_session
from app.models.log_entry import LogEntry
from app.models.user import User
from app.schemas.log_entry import (
    ConversationResponse,
    LogEntryCreate,
    LogEntryDetail,
    LogEntryPublic,
    StatsResponse,
)
from app.security.api_key_auth import get_current_user_from_api_key, require_scope
from app.security.sessions import get_current_user
from app.services.ingest import ingest_log_entry
from app.services.stats import get_stats

router = APIRouter(tags=["logs"])


def _to_detail(e: LogEntry) -> LogEntryDetail:
    return LogEntryDetail(
        id=e.id,
        user_id=e.user_id,
        conversation_id=e.conversation_id,
        provider=e.provider,
        model=e.model,
        prompt_tokens=e.prompt_tokens,
        completion_tokens=e.completion_tokens,
        total_tokens=e.total_tokens,
        cost_total=e.cost_total,
        cost_currency=e.cost_currency,
        cost_source=e.cost_source,
        latency_ms=e.latency_ms,
        status=e.status,
        client_timestamp=e.client_timestamp,
        created_at=e.created_at,
        request=e.request,
        response=e.response,
        tool_calls=e.tool_calls,
        error=e.error,
        metadata_extra=e.metadata_extra,
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@router.post("/logs", response_model=LogEntryPublic, status_code=status.HTTP_201_CREATED)
def ingest_log(
    payload: LogEntryCreate,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_scope("logs:write")),
) -> LogEntryPublic:
    """
    Ingest one LLM call in canonical format.

    **Authentication**: API key with `logs:write` scope (`X-API-Key` header).

    See `docs/schemas.md` for the full canonical schema reference, and
    `docs/ai-client-guide.md` for a complete integration walkthrough.
    """
    # Enforce body size (belt-and-suspenders; Nginx/proxy should do outer limit)
    from app.config import settings
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_log_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Request body exceeds maximum allowed size",
        )

    entry = ingest_log_entry(payload, user.id, db)
    return LogEntryPublic.model_validate(entry)


# ---------------------------------------------------------------------------
# Retrieval — session OR API key with logs:read
# ---------------------------------------------------------------------------

async def _resolve_user(
    request: Request,
    db: Session = Depends(get_session),
) -> User:
    """Allow either session cookie or API key (logs:read) for read endpoints."""
    api_key_header = request.headers.get("x-api-key")
    if api_key_header:
        user, api_key = await get_current_user_from_api_key(api_key_header, db)
        if "logs:read" not in api_key.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="API key missing scope: logs:read")
        return user
    return get_current_user(request, db)


@router.get("/logs", response_model=list[LogEntryPublic])
async def list_logs(
    request: Request,
    db: Session = Depends(get_session),
    conversation_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: int = Query(default=0, ge=0),
) -> list[LogEntry]:
    """
    List log entries for the current user with optional filtering.
    Returns a paginated, filtered list of call summaries.
    """
    user = await _resolve_user(request, db)
    query = select(LogEntry).where(LogEntry.user_id == user.id)

    if conversation_id:
        query = query.where(LogEntry.conversation_id == conversation_id)
    if model:
        query = query.where(LogEntry.model == model)
    if provider:
        query = query.where(LogEntry.provider == provider)
    if since:
        query = query.where(LogEntry.created_at >= since)
    if until:
        query = query.where(LogEntry.created_at <= until)

    query = query.order_by(LogEntry.created_at.desc()).offset(offset).limit(limit)  # type: ignore[arg-type]
    return db.exec(query).all()  # type: ignore[return-value]


@router.get("/logs/{log_id}", response_model=LogEntryDetail)
async def get_log(
    log_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
) -> LogEntryDetail:
    """Return full detail for a single LLM call including request/response bodies."""
    user = await _resolve_user(request, db)
    entry = db.get(LogEntry, log_id)
    if not entry or entry.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log entry not found")
    return _to_detail(entry)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> ConversationResponse:
    """
    Return all log entries for a conversation in chronological order.
    Useful for reconstructing and debugging a full LLM session.
    """
    user = await _resolve_user(request, db)
    entries = db.exec(
        select(LogEntry)
        .where(LogEntry.user_id == user.id, LogEntry.conversation_id == conversation_id)
        .order_by(LogEntry.created_at)  # type: ignore[arg-type]
    ).all()

    if not entries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    total_tokens = sum(e.total_tokens for e in entries)
    costs = [e.cost_total for e in entries if e.cost_total is not None]
    total_cost = round(sum(costs), 8) if costs else None

    return ConversationResponse(
        conversation_id=conversation_id,
        entries=[_to_detail(e) for e in entries],
        total_tokens=total_tokens,
        total_cost=total_cost,
    )


@router.get("/stats/summary", response_model=StatsResponse)
async def stats_summary(
    request: Request,
    db: Session = Depends(get_session),
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> StatsResponse:
    """
    Aggregated stats for the current user: total tokens, cost, calls per day,
    breakdown by model. Default window is the last 30 days.
    """
    user = await _resolve_user(request, db)
    return get_stats(user.id, db, days=days)
