"""Message interning service.

Provides content-addressed deduplication of LLM message objects:
- Each unique (user_id, canonical-JSON) is stored once in the `messages` table.
- Ingest calls `intern_messages` to exchange a list of raw message dicts for
  an ordered list of stable UUIDs.
- The read path calls `rehydrate_messages` to reverse the process, restoring
  the full message list from a set of IDs.
- Parent entry resolution detects longest-prefix matches so the conversation
  tree can be reconstructed later.
"""

import hashlib
import json
import uuid

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.log_entry import LogEntry
from app.models.message import Message


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _canonical_json(obj: dict) -> str:
    """Deterministic JSON for a message dict — used as the hash input."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(message: dict) -> str:
    """Return the sha256 hex digest of the canonical JSON of *message*."""
    return hashlib.sha256(_canonical_json(message).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Interning
# ---------------------------------------------------------------------------

def intern_messages(
    messages: list[dict],
    user_id: uuid.UUID,
    db: Session,
) -> list[uuid.UUID]:
    """Exchange *messages* for a stable ordered list of UUIDs.

    For each message:
    1. Compute its content_hash.
    2. INSERT ... ON CONFLICT DO NOTHING to ensure the row exists.
    3. SELECT all rows back to get their stable ids.
    4. Return ids in the original message order.

    Uses the underlying connection directly for the raw INSERT so that
    ON CONFLICT works reliably and the ORM's autoflush/autocommit cycle
    doesn't interfere.
    """
    if not messages:
        return []

    hashes = [content_hash(m) for m in messages]

    # Flush any pending ORM state first so the connection is clean.
    db.flush()

    # Use the raw DBAPI cursor to avoid SQLAlchemy parameter-style mixing
    # issues with psycopg3 when combining named binds and Postgres casts.
    raw_conn = db.connection().connection
    with raw_conn.cursor() as cur:
        for i, h in enumerate(hashes):
            cur.execute(
                """
                INSERT INTO messages (id, user_id, content_hash, role, content, created_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (user_id, content_hash) DO NOTHING
                """,
                (
                    str(uuid.uuid4()),
                    str(user_id),
                    h,
                    messages[i].get("role", ""),
                    _canonical_json(messages[i]),
                ),
            )

    # Fetch all ids for the hashes we just ensured exist.
    existing_rows = db.exec(
        select(Message).where(
            Message.user_id == user_id,
            Message.content_hash.in_(hashes),  # type: ignore[attr-defined]
        )
    ).all()

    hash_to_id = {row.content_hash: row.id for row in existing_rows}

    # Return ids in original message order.
    return [hash_to_id[h] for h in hashes]


# ---------------------------------------------------------------------------
# Rehydration
# ---------------------------------------------------------------------------

def rehydrate_messages(
    message_ids: list[uuid.UUID],
    db: Session,
) -> list[dict]:
    """Fetch message dicts for *message_ids* and return them in order.

    A single `WHERE id = ANY(...)` query is used so this is O(1) round-trips
    regardless of message count.
    """
    if not message_ids:
        return []

    rows = db.exec(
        select(Message).where(Message.id.in_(message_ids))  # type: ignore[attr-defined]
    ).all()

    id_to_content = {row.id: row.content for row in rows}
    return [id_to_content[mid] for mid in message_ids if mid in id_to_content]


def batch_rehydrate_messages(
    entries_message_ids: list[list[uuid.UUID]],
    db: Session,
) -> dict[uuid.UUID, dict]:
    """Fetch all messages needed for multiple entries in one query.

    Returns a mapping of message_id → content dict.  Callers reassemble
    the per-entry message lists themselves using their own message_ids order.
    """
    all_ids: set[uuid.UUID] = set()
    for ids in entries_message_ids:
        all_ids.update(ids)

    if not all_ids:
        return {}

    rows = db.exec(
        select(Message).where(Message.id.in_(list(all_ids)))  # type: ignore[attr-defined]
    ).all()

    return {row.id: row.content for row in rows}


# ---------------------------------------------------------------------------
# Parent entry resolution
# ---------------------------------------------------------------------------

def resolve_parent_entry_id(
    message_ids: list[uuid.UUID],
    conversation_id: str | None,
    user_id: uuid.UUID,
    current_entry_id: uuid.UUID,
    db: Session,
) -> uuid.UUID | None:
    """Find the existing entry whose message_ids is the longest proper prefix
    of *message_ids*.

    This resolves the conversation tree edge so branching / retry detection
    works correctly.  Returns None for the root call or when no prefix match
    exists.

    Algorithm: fetch all entries in the same conversation ordered by
    message count descending, return the first whose ids appear as a prefix
    in *message_ids*.  Works correctly for both linear appends and branching.
    """
    if not conversation_id or len(message_ids) < 2:
        return None

    # Fetch candidate entries (same conversation, different entry, already committed)
    candidates = db.exec(
        select(LogEntry).where(
            LogEntry.user_id == user_id,
            LogEntry.conversation_id == conversation_id,
            LogEntry.id != current_entry_id,
        )
    ).all()

    if not candidates:
        return None

    # Sort by descending prefix length for greedy match.
    candidates.sort(key=lambda e: len(e.message_ids), reverse=True)

    for candidate in candidates:
        prefix = candidate.message_ids
        if not prefix:
            continue
        if len(prefix) >= len(message_ids):
            continue  # can't be a *proper* prefix
        if message_ids[: len(prefix)] == prefix:
            return candidate.id

    return None
