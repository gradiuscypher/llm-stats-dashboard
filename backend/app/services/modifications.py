"""Modification persistence service (DEPRECATED).

This module is kept for backward compatibility. New code should use
app.services.diffs.persist_diffs instead.

Writes RecordedModification entries from the proxy pipeline to the
message_modifications table, linked to the persisted LogEntry.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.models.message_modification import MessageModification


# Stub the old RecordedModification type (removed from proxy.context).
@dataclass
class RecordedModification:
    plugin_name: str
    target: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    message_index: int | None = None
    message_role: str | None = None


def persist_modifications(
    modifications: list[RecordedModification],
    log_entry_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> list[MessageModification]:
    """Persist recorded modifications linked to a LogEntry.

    Returns the list of persisted MessageModification rows.
    """
    rows: list[MessageModification] = []
    for mod in modifications:
        row = MessageModification(
            log_entry_id=log_entry_id,
            user_id=user_id,
            plugin_name=mod.plugin_name,
            target=mod.target,
            message_index=mod.message_index,
            message_role=mod.message_role,
            summary=mod.summary,
            detail=mod.detail,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows