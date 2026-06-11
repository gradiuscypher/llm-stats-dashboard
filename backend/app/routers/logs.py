"""Log ingestion and retrieval endpoints."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Integer, cast, func
from sqlmodel import Session, select

from app.db import get_session
from app.models.api_key import ApiKey
from app.models.log_entry import LogEntry
from app.models.message_diff import MessageDiff
from app.models.message_modification import MessageModification
from app.models.user import User
from app.schemas.log_entry import (
    CallDivider,
    CompressionSummary,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummary,
    LogEntryCreate,
    LogEntryDetail,
    LogEntryPublic,
    MessageDiffPublic,
    ModificationPublic,
    StatsResponse,
    TranscriptBranch,
    TranscriptMessage,
    TranscriptResponse,
)
from app.security.api_key_auth import (
    _extract_api_key,
    get_current_user_from_api_key,
)
from app.security.sessions import get_current_user
from app.services.ingest import ingest_log_entry
from app.services.messages import batch_rehydrate_messages, rehydrate_messages
from app.services.stats import get_stats
from app.utils.time import utcnow

router = APIRouter(tags=["logs"])


RESPONSE_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # UUID NAMESPACE_DNS


def _synthetic_trailing_reply(
    entry: LogEntry,
    call_index: int | None,
) -> TranscriptMessage | None:
    """Build a synthetic TranscriptMessage from *entry.response* for the final turn.

    When the assistant response has already been interned as part of
    LogEntry.message_ids (the post-fix behavior), the response is present in the
    trunk and no synthetic message is needed.  This helper is only needed for
    entries created before the response-interning fix, where message_ids stored
    request messages only.

    Returns None when the response is empty or the entry errored out.

    A turn with reasoning but empty final content is still emitted (reasoning-only
    / thinking-only turns from reasoning models).
    """
    resp = entry.response or {}
    msg = resp.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    reasoning = msg.get("reasoning")
    reasoning_details = msg.get("reasoning_details")

    has_content = content is not None
    if isinstance(content, str) and content.strip() == "":
        has_content = False
    has_reasoning = bool(reasoning) or bool(reasoning_details)

    # Skip truly empty replies (error entries, tool-only responses w/o reasoning, etc.)
    if not has_content and not has_reasoning:
        return None

    return TranscriptMessage(
        message_id=uuid.uuid5(RESPONSE_NAMESPACE, f"response:{entry.id}"),
        role=msg.get("role", "assistant"),
        content=content if has_content else "",
        reasoning=reasoning,
        reasoning_details=reasoning_details,
        introduced_by_entry_id=None,
        introduced_by_call_index=call_index,
    )


def _is_empty_response(content: dict) -> bool:
    """Return True if *content* represents a response with no displayable content.

    Mirrors the logic in _synthetic_trailing_reply so that interned empty
    assistant replies (error entries, tool-only turns without reasoning) are
    filtered from the transcript trunk just like the synthetic version.
    """
    c = content.get("content")
    reasoning = content.get("reasoning")
    reasoning_details = content.get("reasoning_details")

    has_content = c is not None
    if isinstance(c, str) and c.strip() == "":
        has_content = False
    has_reasoning = bool(reasoning) or bool(reasoning_details)

    return not has_content and not has_reasoning


def _to_detail(
    e: LogEntry,
    messages: list[dict] | None = None,
    modifications: list[MessageModification] | None = None,
    modification_count: int = 0,
    request_diffs: list[MessageDiff] | None = None,
    diff_count: int = 0,
) -> LogEntryDetail:
    """Serialize a LogEntry to LogEntryDetail, rehydrating messages if provided.

    Pass pre-fetched *messages* (already in order) to avoid per-entry DB queries.
    If *messages* is None the messages field will be absent from request; callers
    that need the full request blob should use _to_detail_with_db instead.
    """
    request = dict(e.request)  # shallow copy of params blob
    if messages is not None:
        request["messages"] = messages
    mods_pub = [ModificationPublic.model_validate(m) for m in (modifications or [])]
    diffs_pub = [MessageDiffPublic.model_validate(d) for d in (request_diffs or [])]
    return LogEntryDetail(
        id=e.id,
        user_id=e.user_id,
        conversation_id=e.conversation_id,
        provider=e.provider,
        model=e.model,
        prompt_tokens=e.prompt_tokens,
        completion_tokens=e.completion_tokens,
        total_tokens=e.total_tokens,
        reasoning_tokens=e.reasoning_tokens,
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
        modification_count=modification_count,
        modifications=mods_pub,
        diff_count=diff_count,
        request_diffs=diffs_pub,
    )


def _to_detail_with_db(e: LogEntry, db: Session) -> LogEntryDetail:
    """Single-entry detail fetch — rehydrates messages, modifications, and diffs."""
    messages = rehydrate_messages(e.message_ids, db)
    mods = list(
        db.exec(
            select(MessageModification).where(MessageModification.log_entry_id == e.id)
        ).all()
    )
    diffs = list(
        db.exec(
            select(MessageDiff).where(MessageDiff.log_entry_id == e.id)
        ).all()
    )
    return _to_detail(
        e,
        messages,
        modifications=mods,
        modification_count=len(mods),
        request_diffs=diffs,
        diff_count=len(diffs),
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post("/logs", response_model=LogEntryPublic, status_code=status.HTTP_201_CREATED)
def ingest_log(
    payload: LogEntryCreate,
    request: Request,
    db: Session = Depends(get_session),
    auth: tuple[User, ApiKey] = Depends(get_current_user_from_api_key),
) -> LogEntryPublic:
    """
    Ingest one LLM call in canonical format.

    **Authentication**: API key with `logs:write` scope (`X-API-Key` header).

    See `docs/schemas.md` for the full canonical schema reference, and
    `docs/ai-client-guide.md` for a complete integration walkthrough.
    """
    user, api_key = auth
    if "logs:write" not in api_key.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key missing required scope: logs:write",
        )
    from app.config import settings

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_log_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Request body exceeds maximum allowed size",
        )

    entry = ingest_log_entry(payload, user.id, db, api_key_id=api_key.id)
    return LogEntryPublic.model_validate(entry)


# ---------------------------------------------------------------------------
# Retrieval — session OR API key with logs:read
# ---------------------------------------------------------------------------


async def _resolve_user(
    request: Request,
    db: Session = Depends(get_session),
) -> User:
    """Allow either session cookie or API key (logs:read) for read endpoints."""
    api_key_header = request.headers.get("x-api-key") or _extract_api_key(request)
    if api_key_header and api_key_header.startswith("lsd_"):
        user, api_key = await get_current_user_from_api_key(request, db)
        if "logs:read" not in api_key.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="API key missing scope: logs:read"
            )
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
) -> list[LogEntryPublic]:
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

    query = query.order_by(LogEntry.created_at.desc()).offset(offset).limit(limit)# ty:ignore[unresolved-attribute]
    entries = db.exec(query).all()

    # Batch-resolve api_key names to avoid N+1
    key_ids = {e.api_key_id for e in entries if e.api_key_id is not None}
    key_name_map: dict[uuid.UUID, str] = {}
    if key_ids:
        keys = db.exec(select(ApiKey).where(ApiKey.id.in_(list(key_ids)))).all()# ty:ignore[unresolved-attribute]
        key_name_map = {k.id: k.name for k in keys}

    # Batch-resolve modification counts (backward compat)
    # and diff counts (new)
    entry_ids = [e.id for e in entries]
    mod_counts: dict[uuid.UUID, int] = {}
    diff_counts: dict[uuid.UUID, int] = {}
    if entry_ids:
        from sqlalchemy import func as sa_func

        # Modification counts (legacy table)
        count_rows = db.exec(
            select(
                MessageModification.log_entry_id,
                sa_func.count(MessageModification.id).label("cnt"),  # ty:ignore[invalid-argument-type]
            )
            .where(MessageModification.log_entry_id.in_(entry_ids))# ty:ignore[unresolved-attribute]
            .group_by(MessageModification.log_entry_id)# ty:ignore[invalid-argument-type]
        ).all()
        mod_counts = {row.log_entry_id: row.cnt for row in count_rows}# ty:ignore[unresolved-attribute]

        # Diff counts (new table)
        diff_rows = db.exec(
            select(
                MessageDiff.log_entry_id,
                sa_func.count(MessageDiff.id).label("cnt"),  # ty:ignore[invalid-argument-type]
            )
            .where(MessageDiff.log_entry_id.in_(entry_ids))# ty:ignore[unresolved-attribute]
            .group_by(MessageDiff.log_entry_id)# ty:ignore[invalid-argument-type]
        ).all()
        diff_counts = {row.log_entry_id: row.cnt for row in diff_rows}# ty:ignore[unresolved-attribute]

    result: list[LogEntryPublic] = []
    for e in entries:
        p = LogEntryPublic.model_validate(e)
        if e.api_key_id and e.api_key_id in key_name_map:
            p.api_key_name = key_name_map[e.api_key_id]
        p.modification_count = mod_counts.get(e.id, 0)
        p.diff_count = diff_counts.get(e.id, 0)
        result.append(p)
    return result


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


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    db: Session = Depends(get_session),
    conversation_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    sort: str = Query(default="last_activity"),
    order: str = Query(default="desc"),
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: int = Query(default=0, ge=0),
) -> ConversationListResponse:
    """
    List conversations for the current user — one row per conversation_id.
    Each row aggregates all calls sharing the same conversation_id.
    """
    user = await _resolve_user(request, db)

    # Validate sort / order against allow-lists; fall back to defaults if unknown.
    _ALLOWED_SORT = {"last_activity", "first_activity", "total_tokens", "total_cost", "call_count"}
    _ALLOWED_ORDER = {"asc", "desc"}
    sort_col = sort if sort in _ALLOWED_SORT else "last_activity"
    order_dir = order if order in _ALLOWED_ORDER else "desc"

    # Base WHERE conditions shared by the aggregate query and count subquery.
    conditions: list = [
        LogEntry.user_id == user.id,
        LogEntry.conversation_id.is_not(None),# ty:ignore[unresolved-attribute]
    ]
    if since:
        conditions.append(LogEntry.created_at >= since)
    if until:
        conditions.append(LogEntry.created_at <= until)
    if conversation_id:
        conditions.append(LogEntry.conversation_id.ilike(f"%{conversation_id}%"))# ty:ignore[unresolved-attribute]

    # model / provider: "any call in the conversation" semantics.
    # First find matching conversation_ids, then include ALL calls in those conversations.
    if model or provider:
        sub = select(LogEntry.conversation_id).where(
            LogEntry.user_id == user.id,
            LogEntry.conversation_id.is_not(None),# ty:ignore[unresolved-attribute]
        )
        if model:
            sub = sub.where(LogEntry.model == model)
        if provider:
            sub = sub.where(LogEntry.provider == provider)
        matching_ids_subq = sub.distinct().subquery()
        conditions.append(LogEntry.conversation_id.in_(select(matching_ids_subq.c.conversation_id)))# ty:ignore[unresolved-attribute]

    # Aggregate query
    agg = (
        select(
            LogEntry.conversation_id.label("conversation_id"),# ty:ignore[unresolved-attribute]
            func.count().label("call_count"),
            func.coalesce(func.sum(LogEntry.total_tokens), 0).label("total_tokens"),
            func.sum(LogEntry.cost_total).label("total_cost"),
            func.coalesce(
                func.sum(
                    cast(
                        LogEntry.metadata_extra.op("->")("compression").op("->>")("tokens_saved"),
                        Integer,
                    )
                ),
                0,
            ).label("tokens_saved"),
            func.min(LogEntry.created_at).label("first_activity"),
            func.max(LogEntry.created_at).label("last_activity"),
            func.bool_or(LogEntry.status == "error").label("has_error"),
            func.array_agg(func.distinct(LogEntry.model)).label("models"),
            func.array_agg(func.distinct(LogEntry.provider)).label("providers"),
        )  # ty:ignore[no-matching-overload]
        .where(*conditions)
        .group_by(LogEntry.conversation_id)
    )

    # Count total groups (before pagination)
    count_subq = agg.subquery()
    total = db.execute(select(func.count()).select_from(count_subq)).scalar_one()# ty:ignore[deprecated]

    # Sort mapping
    sort_map = {
        "last_activity": count_subq.c.last_activity,
        "first_activity": count_subq.c.first_activity,
        "total_tokens": count_subq.c.total_tokens,
        "total_cost": count_subq.c.total_cost,
        "call_count": count_subq.c.call_count,
    }
    sort_attr = sort_map[sort_col]
    sort_attr = sort_attr.asc() if order_dir == "asc" else sort_attr.desc()

    paginated = select(count_subq).order_by(sort_attr).offset(offset).limit(limit)
    rows = db.execute(paginated).all()  # ty:ignore[deprecated]

    # Build response
    convos: list[ConversationSummary] = []
    for row in rows:
        convos.append(
            ConversationSummary(
                conversation_id=row.conversation_id,
                call_count=row.call_count,
                total_tokens=row.total_tokens,
                total_cost=round(row.total_cost, 8) if row.total_cost is not None else None,
                tokens_saved=int(row.tokens_saved) if row.tokens_saved is not None else 0,
                models=sorted(set(row.models)),
                providers=sorted(set(row.providers)),
                has_error=row.has_error,
                first_activity=row.first_activity,
                last_activity=row.last_activity,
            )
        )

    return ConversationListResponse(conversations=convos, total=total)


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
        .order_by(LogEntry.created_at)# ty:ignore[invalid-argument-type]
    ).all()

    if not entries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    total_tokens = sum(e.total_tokens for e in entries)
    costs = [e.cost_total for e in entries if e.cost_total is not None]
    total_cost = round(sum(costs), 8) if costs else None

    # Batch-fetch all messages needed across all entries in one query.
    id_to_content = batch_rehydrate_messages([e.message_ids for e in entries], db)

    # Batch-fetch diffs for all entries.
    from app.services.diffs import batch_fetch_diffs
    entry_ids = [e.id for e in entries]
    diffs_by_entry = batch_fetch_diffs(entry_ids, db) if entry_ids else {}

    rehydrated_entries = [
        _to_detail(
            e,
            [id_to_content[mid] for mid in e.message_ids if mid in id_to_content],
            request_diffs=diffs_by_entry.get(e.id, []),
            diff_count=len(diffs_by_entry.get(e.id, [])),
        )
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
        .order_by(LogEntry.created_at)# ty:ignore[invalid-argument-type]
    ).all()

    if not entries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Batch-fetch all messages.
    id_to_content = batch_rehydrate_messages([e.message_ids for e in entries], db)

    # Batch-fetch modifications for all entries in this conversation (legacy).
    # Batch-fetch message_diffs for all entries (new).
    entry_ids = [e.id for e in entries]
    all_mods: list[MessageModification] = []
    all_diffs: list[MessageDiff] = []
    if entry_ids:
        all_mods = list(
            db.exec(
                select(MessageModification).where(
                    MessageModification.log_entry_id.in_(entry_ids)  # ty:ignore[unresolved-attribute]
                )
            ).all()
        )
        all_diffs = list(
            db.exec(
                select(MessageDiff).where(
                    MessageDiff.log_entry_id.in_(entry_ids)  # ty:ignore[unresolved-attribute]
                )
            ).all()
        )
    # Map entry_id → list of modifications
    mods_by_entry: dict[uuid.UUID, list[MessageModification]] = {}
    for mod in all_mods:
        mods_by_entry.setdefault(mod.log_entry_id, []).append(mod)
    # Map entry_id → list of diffs
    diffs_by_entry: dict[uuid.UUID, list[MessageDiff]] = {}
    for d in all_diffs:
        diffs_by_entry.setdefault(d.log_entry_id, []).append(d)
    # Map entry_id → set of (role, index) for transcript message modified_by
    mod_targets_by_entry: dict[uuid.UUID, set[tuple[str, int]]] = {}
    mod_plugins_by_entry: dict[uuid.UUID, set[str]] = {}
    for mod in all_mods:
        if mod.message_role and mod.message_index is not None:
            mod_targets_by_entry.setdefault(mod.log_entry_id, set()).add(
                (mod.message_role, mod.message_index)
            )
        mod_plugins_by_entry.setdefault(mod.log_entry_id, set()).add(mod.plugin_name)
    # Build a message_id-keyed diff lookup.
    # With A1 (canonical = original), entry.message_ids[diff.message_index] is the
    # interned message id for the original content — message_index aligns with the
    # position in the original message array so the lookup is stable across toggles.
    entry_map: dict[uuid.UUID, LogEntry] = {e.id: e for e in entries}
    diff_by_message_id: dict[uuid.UUID, MessageDiff] = {}
    for d in all_diffs:
        if d.change_kind != "modified":
            continue
        e = entry_map.get(d.log_entry_id)
        if e is None:
            continue
        if d.message_index < len(e.message_ids):
            mid = e.message_ids[d.message_index]
            diff_by_message_id[mid] = d

    # Build call dividers (1-indexed, chronological across entire conversation).
    all_dividers: list[CallDivider] = []
    for idx, e in enumerate(entries):
        eid = e.id
        entry_mods = mods_by_entry.get(eid, [])
        mods_pub = [ModificationPublic.model_validate(m) for m in entry_mods]
        entry_diffs = diffs_by_entry.get(eid, [])
        diffs_pub = [MessageDiffPublic.model_validate(d) for d in entry_diffs]
        all_dividers.append(
            CallDivider(
                entry_id=eid,
                call_index=idx + 1,
                model=e.model,
                provider=e.provider,
                prompt_tokens=e.prompt_tokens,
                completion_tokens=e.completion_tokens,
                total_tokens=e.total_tokens,
                reasoning_tokens=e.reasoning_tokens,
                cache_read_tokens=e.cache_read_tokens,
                cache_write_tokens=e.cache_write_tokens,
                cost_total=e.cost_total,
                latency_ms=e.latency_ms,
                status=e.status,
                created_at=e.created_at,
                modification_count=len(entry_mods),
                modifications=mods_pub,
                diff_count=len(entry_diffs),
                diffs=diffs_pub,
            )
        )
    divider_map = {d.entry_id: d for d in all_dividers}

    # Detect branching: if any entry's parent_entry_id forms a non-linear tree,
    # we have branches.
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

    is_branched = any(len(children.get(e.id, [])) > 1 for e in entries)

    # Build trunk messages — unique messages in first-seen order.
    seen_ids: set[uuid.UUID] = set()
    trunk_messages: list[TranscriptMessage] = []
    trunk_dividers: list[CallDivider] = []

    for entry in trunk_entries:
        new_ids = [mid for mid in entry.message_ids if mid not in seen_ids]
        if new_ids:
            trunk_dividers.append(divider_map[entry.id])
        # Plugin names that modified messages in this entry
        sorted(mod_plugins_by_entry.get(entry.id, set()))
        for idx_in_entry, mid in enumerate(new_ids):
            seen_ids.add(mid)
            content = id_to_content.get(mid, {})
            msg_role = content.get("role", "")

            # Skip empty assistant responses (no content, no reasoning) —
            # these are error entries / tool-only turns whose interned
            # response adds nothing to the transcript.
            if msg_role == "assistant" and _is_empty_response(content):
                continue

            # Check if this message (by role + position in entry) was modified
            msg_modified_by: list[str] = []
            targets = mod_targets_by_entry.get(entry.id, set())
            if (msg_role, idx_in_entry) in targets:
                entry_mods = mods_by_entry.get(entry.id, [])
                msg_modified_by = sorted(
                    set(
                        m.plugin_name
                        for m in entry_mods
                        if m.message_role == msg_role and m.message_index == idx_in_entry
                    )
                )
            # Look up diff by message_id (stable across toggles with A1).
            diff = diff_by_message_id.get(mid)
            if diff is not None and diff.modified_by and not msg_modified_by:
                msg_modified_by = diff.modified_by
            trunk_messages.append(
                TranscriptMessage(
                    message_id=mid,
                    role=msg_role,
                    content=content.get("content", ""),
                    reasoning=content.get("reasoning"),
                    reasoning_details=content.get("reasoning_details"),
                    introduced_by_entry_id=entry.id,
                    introduced_by_call_index=divider_map[entry.id].call_index,
                    modified_by=msg_modified_by,
                    original_content=(
                        diff.original_content.get("content")
                        if diff is not None and diff.original_content
                        else None
                    ),
                    modified_content=(
                        diff.final_content.get("content")
                        if diff is not None and diff.final_content
                        else None
                    ),
                )
            )

    # Append the final assistant reply for the last trunk entry (if any),
    # but only when the response isn't already present in message_ids.
    if trunk_entries:
        tail_entry = trunk_entries[-1]
        # Check whether the assistant response is already in the trunk —
        # if the last trunk message was introduced by this entry and has
        # role=assistant, the response was interned and already shown.
        already_present = False
        if trunk_messages:
            last_msg = trunk_messages[-1]
            if (
                last_msg.role == "assistant"
                and last_msg.introduced_by_entry_id == tail_entry.id
            ):
                already_present = True
        if not already_present:
            tail_reply = _synthetic_trailing_reply(
                tail_entry,
                call_index=divider_map[tail_entry.id].call_index,
            )
            if tail_reply is not None:
                trunk_messages.append(tail_reply)

    # Build branches — entries not on the trunk.
    branch_entries = [e for e in entries if e.id not in trunk_entry_ids]
    built_branches: list[TranscriptBranch] = []

    # Group branch entries by their divergence root (first entry off-trunk).
    branch_roots: list[LogEntry] = [
        e
        for e in branch_entries
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
            for idx_in_entry, mid in enumerate(new_ids):
                branch_seen.add(mid)
                content = id_to_content.get(mid, {})
                msg_role = content.get("role", "")

                # Skip empty assistant responses.
                if msg_role == "assistant" and _is_empty_response(content):
                    continue

                msg_modified_by: list[str] = []
                targets = mod_targets_by_entry.get(entry.id, set())
                if (msg_role, idx_in_entry) in targets:
                    entry_mods = mods_by_entry.get(entry.id, [])
                    msg_modified_by = sorted(
                        set(
                            m.plugin_name
                            for m in entry_mods
                            if m.message_role == msg_role and m.message_index == idx_in_entry
                        )
                    )
                # Look up diff by message_id (stable across toggles with A1).
                diff = diff_by_message_id.get(mid)
                if diff is not None and diff.modified_by and not msg_modified_by:
                    msg_modified_by = diff.modified_by
                branch_messages.append(
                    TranscriptMessage(
                        message_id=mid,
                        role=msg_role,
                        content=content.get("content", ""),
                        reasoning=content.get("reasoning"),
                        reasoning_details=content.get("reasoning_details"),
                        introduced_by_entry_id=entry.id,
                        introduced_by_call_index=divider_map[entry.id].call_index,
                        modified_by=msg_modified_by,
                        original_content=(
                            diff.original_content.get("content")
                            if diff is not None and diff.original_content
                            else None
                        ),
                        modified_content=(
                            diff.final_content.get("content")
                            if diff is not None and diff.final_content
                            else None
                        ),
                    )
                )

        # Append the final assistant reply for the last branch entry (if any),
        # but only when the response isn't already in message_ids.
        if branch_chain:
            tail_entry = branch_chain[-1]
            already_present = False
            if branch_messages:
                last_msg = branch_messages[-1]
                if (
                    last_msg.role == "assistant"
                    and last_msg.introduced_by_entry_id == tail_entry.id
                ):
                    already_present = True
            if not already_present:
                tail_reply = _synthetic_trailing_reply(
                    tail_entry,
                    call_index=divider_map[tail_entry.id].call_index,
                )
                if tail_reply is not None:
                    branch_messages.append(tail_reply)

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

    # Aggregate compression stats across all calls
    compression = None
    comp_before = 0
    comp_after = 0
    comp_saved = 0
    comp_calls = 0
    for e in entries:
        c = e.metadata_extra.get("compression") if e.metadata_extra else None
        if c and isinstance(c, dict):
            tb = c.get("tokens_before", 0)
            ts = c.get("tokens_saved", 0)
            if tb > 0:
                comp_before += tb
                comp_after += tb - ts
                comp_saved += ts
                comp_calls += 1
    if comp_calls > 0 and comp_before > 0:
        compression = CompressionSummary(
            tokens_before=comp_before,
            tokens_after=comp_after,
            tokens_saved=comp_saved,
            compression_ratio=comp_saved / comp_before,
            calls_with_compression=comp_calls,
        )

    return TranscriptResponse(
        conversation_id=conversation_id,
        trunk=trunk_messages,
        branches=built_branches,
        dividers=trunk_dividers,
        total_tokens=total_tokens,
        total_cost=total_cost,
        is_branched=is_branched,
        compression=compression,
    )


@router.get("/stats/summary", response_model=StatsResponse)
async def stats_summary(
    request: Request,
    db: Session = Depends(get_session),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    interval: str = Query(default="1d", pattern=r"^(5m|1h|1d|1w|1mo)$"),
    days: Annotated[int, Query(ge=1, le=365)] | None = None,
) -> StatsResponse:
    """
    Aggregated stats for the current user with flexible time range and bucket
    granularity.

    - **since**/**until**: explicit time bounds (ISO 8601).  Omit ``since`` for "all time".
    - **interval**: bucket granularity — ``5m``, ``1h``, ``1d``, ``1w``, or ``1mo``.
    - **days**: legacy shorthand; sets ``since`` to N days ago with ``interval="1d"``.
      Ignored when ``since`` is provided.
    """
    user = await _resolve_user(request, db)

    # Legacy back-compat: if only `days` is supplied, translate to since/interval.
    if since is None and days is not None:
        since = utcnow() - timedelta(days=days)
        interval = "1d"

    return get_stats(
        user.id, db, since=since, until=until, interval=interval,
    )
