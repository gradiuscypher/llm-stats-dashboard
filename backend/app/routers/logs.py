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
    CallDivider,
    ConversationResponse,
    LogEntryCreate,
    LogEntryDetail,
    LogEntryPublic,
    StatsResponse,
    TranscriptBranch,
    TranscriptMessage,
    TranscriptResponse,
)
from app.security.api_key_auth import get_current_user_from_api_key, require_scope
from app.security.sessions import get_current_user
from app.services.ingest import ingest_log_entry
from app.services.messages import batch_rehydrate_messages, rehydrate_messages
from app.services.stats import get_stats

router = APIRouter(tags=["logs"])


def _to_detail(e: LogEntry, messages: list[dict] | None = None) -> LogEntryDetail:
    """Serialize a LogEntry to LogEntryDetail, rehydrating messages if provided.

    Pass pre-fetched *messages* (already in order) to avoid per-entry DB queries.
    If *messages* is None the messages field will be absent from request; callers
    that need the full request blob should use _to_detail_with_db instead.
    """
    request = dict(e.request)  # shallow copy of params blob
    if messages is not None:
        request["messages"] = messages
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
        request=request,
        response=e.response,
        tool_calls=e.tool_calls,
        error=e.error,
        metadata_extra=e.metadata_extra,
    )


def _to_detail_with_db(e: LogEntry, db: Session) -> LogEntryDetail:
    """Single-entry detail fetch — rehydrates messages in one query."""
    messages = rehydrate_messages(e.message_ids, db)
    return _to_detail(e, messages)


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
    return _to_detail_with_db(entry, db)


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

    # Batch-fetch all messages needed across all entries in one query.
    id_to_content = batch_rehydrate_messages([e.message_ids for e in entries], db)
    rehydrated_entries = [
        _to_detail(e, [id_to_content[mid] for mid in e.message_ids if mid in id_to_content])
        for e in entries
    ]

    return ConversationResponse(
        conversation_id=conversation_id,
        entries=rehydrated_entries,
        total_tokens=total_tokens,
        total_cost=total_cost,
    )


# ---------------------------------------------------------------------------
# Transcript — deduped contiguous conversation view
# ---------------------------------------------------------------------------

@router.get("/conversations/{conversation_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> TranscriptResponse:
    """
    Return a deduplicated, ordered transcript of the conversation.

    For linear conversations: a single contiguous message thread.
    For branching conversations (retries, edits): a shared trunk with per-branch
    divergence paths.  The frontend renders the trunk as a continuous scroll
    and shows a branch switcher at fork points.

    Each unique message appears exactly once.  Per-call metadata is returned
    as `dividers` (trunk) / branch dividers so the UI can annotate boundaries
    (model, tokens, cost, latency) without breaking the reading flow.
    """
    user = await _resolve_user(request, db)
    entries = db.exec(
        select(LogEntry)
        .where(LogEntry.user_id == user.id, LogEntry.conversation_id == conversation_id)
        .order_by(LogEntry.created_at)  # type: ignore[arg-type]
    ).all()

    if not entries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Batch-fetch all messages.
    id_to_content = batch_rehydrate_messages([e.message_ids for e in entries], db)

    # Build call dividers (1-indexed, chronological across entire conversation).
    all_dividers: list[CallDivider] = [
        CallDivider(
            entry_id=e.id,
            call_index=idx + 1,
            model=e.model,
            provider=e.provider,
            prompt_tokens=e.prompt_tokens,
            completion_tokens=e.completion_tokens,
            total_tokens=e.total_tokens,
            cost_total=e.cost_total,
            latency_ms=e.latency_ms,
            status=e.status,
            created_at=e.created_at,
        )
        for idx, e in enumerate(entries)
    ]
    divider_map = {d.entry_id: d for d in all_dividers}

    # Detect branching: if any entry's parent_entry_id forms a non-linear tree,
    # we have branches.  Build a set of "branch root" entry ids.
    entry_ids = {e.id for e in entries}
    children: dict[uuid.UUID | None, list[LogEntry]] = {}
    for e in entries:
        children.setdefault(e.parent_entry_id, []).append(e)

    # The trunk is the chain of entries starting from the root with no siblings
    # (or the longest chain when branching exists).
    def _longest_chain(start_id: uuid.UUID | None) -> list[LogEntry]:
        chain: list[LogEntry] = []
        current_id = start_id
        while current_id is not None or (not chain and current_id is None):
            kids = children.get(current_id, [])
            if not kids:
                break
            # Follow the branch with the most descendants (greedy longest-path).
            best = max(kids, key=lambda e: len(e.message_ids))
            chain.append(best)
            current_id = best.id
        return chain

    trunk_entries = _longest_chain(None)
    trunk_entry_ids = {e.id for e in trunk_entries}

    is_branched = any(
        len(children.get(e.id, [])) > 1 for e in entries
    )

    # Build trunk messages — unique messages in first-seen order.
    seen_ids: set[uuid.UUID] = set()
    trunk_messages: list[TranscriptMessage] = []
    trunk_dividers: list[CallDivider] = []

    for entry in trunk_entries:
        new_ids = [mid for mid in entry.message_ids if mid not in seen_ids]
        if new_ids:
            trunk_dividers.append(divider_map[entry.id])
        for mid in new_ids:
            seen_ids.add(mid)
            content = id_to_content.get(mid, {})
            trunk_messages.append(
                TranscriptMessage(
                    message_id=mid,
                    role=content.get("role", ""),
                    content=content.get("content", ""),
                    introduced_by_entry_id=entry.id,
                    introduced_by_call_index=divider_map[entry.id].call_index,
                )
            )

    # Build branches — entries not on the trunk.
    branch_entries = [e for e in entries if e.id not in trunk_entry_ids]
    built_branches: list[TranscriptBranch] = []

    # Group branch entries by their divergence root (first entry off-trunk).
    branch_roots: list[LogEntry] = [
        e for e in branch_entries
        if e.parent_entry_id is None or e.parent_entry_id in trunk_entry_ids
    ]

    for root in branch_roots:
        # Walk this branch chain.
        branch_chain = [root]
        cur = root.id
        while True:
            kids = children.get(cur, [])
            off_trunk = [k for k in kids if k.id not in trunk_entry_ids]
            if not off_trunk:
                break
            nxt = max(off_trunk, key=lambda e: len(e.message_ids))
            branch_chain.append(nxt)
            cur = nxt.id

        branch_seen: set[uuid.UUID] = set(seen_ids)  # start from trunk context
        branch_messages: list[TranscriptMessage] = []
        branch_dividers: list[CallDivider] = []

        for entry in branch_chain:
            new_ids = [mid for mid in entry.message_ids if mid not in branch_seen]
            if new_ids:
                branch_dividers.append(divider_map[entry.id])
            for mid in new_ids:
                branch_seen.add(mid)
                content = id_to_content.get(mid, {})
                branch_messages.append(
                    TranscriptMessage(
                        message_id=mid,
                        role=content.get("role", ""),
                        content=content.get("content", ""),
                        introduced_by_entry_id=entry.id,
                        introduced_by_call_index=divider_map[entry.id].call_index,
                    )
                )

        built_branches.append(
            TranscriptBranch(
                branch_id=root.id,
                messages=branch_messages,
                dividers=branch_dividers,
            )
        )

    total_tokens = sum(e.total_tokens for e in entries)
    costs = [e.cost_total for e in entries if e.cost_total is not None]
    total_cost = round(sum(costs), 8) if costs else None

    return TranscriptResponse(
        conversation_id=conversation_id,
        trunk=trunk_messages,
        branches=built_branches,
        dividers=trunk_dividers,
        total_tokens=total_tokens,
        total_cost=total_cost,
        is_branched=is_branched,
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
