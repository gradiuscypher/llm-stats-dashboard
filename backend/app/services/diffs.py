"""Diff persistence service.

Writes MessageDiff entries from the interceptor to the message_diffs table.
Replaces the old modifications.py for request-side transform diffs.
"""

import uuid

from sqlmodel import Session

from app.models.message_diff import MessageDiff
from app.proxy.interceptor import MessageDiff as InterceptorMessageDiff


def persist_diffs(
    diffs: list[InterceptorMessageDiff],
    log_entry_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> list[MessageDiff]:
    """Persist interceptor diffs linked to a LogEntry.

    Returns the list of persisted MessageDiff rows.
    """
    rows: list[MessageDiff] = []
    for d in diffs:
        row = MessageDiff(
            log_entry_id=log_entry_id,
            user_id=user_id,
            message_index=d.message_index,
            role=d.role,
            change_kind=d.change_kind,
            original_content=d.original_content or {},
            final_content=d.final_content or {},
            modified_by=d.modified_by,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def batch_fetch_diffs(
    entry_ids: list[uuid.UUID],
    db: Session,
) -> dict[uuid.UUID, list[MessageDiff]]:
    """Fetch MessageDiff rows for a batch of log entries.

    Returns a dict mapping entry_id → list of MessageDiff rows.
    """
    from sqlmodel import select

    rows = db.exec(
        select(MessageDiff).where(
            MessageDiff.log_entry_id.in_(entry_ids)  # ty:ignore[unresolved-attribute]
        )
    ).all()

    result: dict[uuid.UUID, list[MessageDiff]] = {}
    for row in rows:
        result.setdefault(row.log_entry_id, []).append(row)
    return result
