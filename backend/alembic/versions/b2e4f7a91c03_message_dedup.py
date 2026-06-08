"""message dedup: intern messages table + log_entries linkage

Revision ID: b2e4f7a91c03
Revises: ca03586374d1
Create Date: 2026-05-31 00:00:00.000000

Changes
-------
1. Create `messages` table (content-addressed per-user message store).
2. Add `message_ids`      ARRAY(UUID) on log_entries — ordered message refs.
3. Add `parent_entry_id`  UUID FK(log_entries.id) on log_entries — tree edge.
4. Data migration: for every existing log_entry, intern its
   request->messages into the new table, populate message_ids,
   and rewrite request without the messages key.
5. Downgrade: inline messages back into request blobs, drop columns/table.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "b2e4f7a91c03"
down_revision: str | Sequence[str] | None = "ca03586374d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers (pure Python — no SQLModel imports so migration is self-contained)
# ---------------------------------------------------------------------------


def _canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_hash(message: dict) -> str:
    return hashlib.sha256(_canonical_json(message).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. Create messages table ------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_messages_user_hash"),
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_content_hash", "messages", ["content_hash"])

    # 2. Add message_ids + parent_entry_id to log_entries --------------------
    op.add_column(
        "log_entries",
        sa.Column(
            "message_ids",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "log_entries",
        sa.Column("parent_entry_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_log_entries_parent_entry_id",
        "log_entries",
        "log_entries",
        ["parent_entry_id"],
        ["id"],
    )
    op.create_index(
        "ix_log_entries_parent_entry_id",
        "log_entries",
        ["parent_entry_id"],
    )

    # 3. Data migration -------------------------------------------------------
    # Use the raw DBAPI cursor for all DML so we can use %s positional params
    # with Postgres casts without hitting psycopg3's parameter-style conflicts.
    conn = op.get_bind()
    raw = conn.connection.cursor()

    # Fetch all existing entries that have messages in their request blob.
    raw.execute(
        "SELECT id, user_id, conversation_id, request FROM log_entries ORDER BY created_at ASC"
    )
    rows = raw.fetchall()
    col_names = [desc[0] for desc in raw.description]

    # We'll resolve parent_entry_id in-memory using a dict of
    # conversation_id -> list[(entry_id, message_ids)] in creation order.
    conv_history: dict[str, list[tuple[str, list[str]]]] = {}

    for row in rows:
        r = dict(zip(col_names, row, strict=False))
        entry_id = str(r["id"])
        user_id = str(r["user_id"])
        conv_id = r["conversation_id"]
        request: dict = r["request"] if isinstance(r["request"], dict) else json.loads(r["request"])

        messages: list[dict] = request.get("messages", [])
        if not messages:
            continue

        # Intern each message via raw cursor
        msg_ids: list[str] = []
        for msg in messages:
            h = _content_hash(msg)
            raw.execute(
                "SELECT id FROM messages WHERE user_id = %s::uuid AND content_hash = %s",
                (user_id, h),
            )
            existing = raw.fetchone()
            if existing:
                msg_ids.append(str(existing[0]))
            else:
                new_id = str(uuid.uuid4())
                raw.execute(
                    "INSERT INTO messages "
                    "  (id, user_id, content_hash, role, content, created_at) "
                    "VALUES (%s::uuid, %s::uuid, %s, %s, %s::jsonb, NOW())",
                    (new_id, user_id, h, msg.get("role", ""), json.dumps(msg)),
                )
                msg_ids.append(new_id)

        # Resolve parent_entry_id from in-memory history
        parent_entry_id: str | None = None
        if conv_id and conv_id in conv_history:
            for prev_entry_id, prev_msg_ids in reversed(conv_history[conv_id]):
                if not prev_msg_ids:
                    continue
                if len(prev_msg_ids) >= len(msg_ids):
                    continue
                if msg_ids[: len(prev_msg_ids)] == prev_msg_ids:
                    parent_entry_id = prev_entry_id
                    break

        # Update the entry: set message_ids, parent_entry_id, strip messages from request
        request_without_messages = {k: v for k, v in request.items() if k != "messages"}
        array_literal = "{" + ",".join(msg_ids) + "}"

        raw.execute(
            "UPDATE log_entries SET "
            "  message_ids = %s::uuid[], "
            "  parent_entry_id = %s::uuid, "
            "  request = %s::jsonb "
            "WHERE id = %s::uuid",
            (
                array_literal,
                parent_entry_id,
                json.dumps(request_without_messages),
                entry_id,
            ),
        )

        # Record in history for future parent resolution
        if conv_id is not None:
            conv_history.setdefault(conv_id, []).append((entry_id, msg_ids))


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    conn = op.get_bind()
    raw = conn.connection.cursor()

    # Re-inline messages into each entry's request blob
    raw.execute(
        "SELECT id, request, message_ids FROM log_entries WHERE array_length(message_ids, 1) > 0"
    )
    rows = raw.fetchall()
    col_names = [desc[0] for desc in raw.description]

    for row in rows:
        r = dict(zip(col_names, row, strict=False))
        entry_id = str(r["id"])
        request: dict = r["request"] if isinstance(r["request"], dict) else json.loads(r["request"])
        msg_ids: list[str] = [str(mid) for mid in r["message_ids"]] if r["message_ids"] else []

        if not msg_ids:
            continue

        # Fetch messages in order using ANY(%s::uuid[]) — no string interpolation
        array_literal = "{" + ",".join(msg_ids) + "}"
        raw.execute(
            "SELECT id, content FROM messages WHERE id = ANY(%s::uuid[])",
            (array_literal,),
        )
        msg_rows = raw.fetchall()
        id_to_content = {
            str(mr[0]): mr[1] if isinstance(mr[1], dict) else json.loads(mr[1]) for mr in msg_rows
        }

        messages = [id_to_content[mid] for mid in msg_ids if mid in id_to_content]

        request["messages"] = messages
        raw.execute(
            "UPDATE log_entries SET request = %s::jsonb WHERE id = %s::uuid",
            (json.dumps(request), entry_id),
        )

    # Remove columns from log_entries
    op.drop_constraint("fk_log_entries_parent_entry_id", "log_entries", type_="foreignkey")
    op.drop_index("ix_log_entries_parent_entry_id", table_name="log_entries")
    op.drop_column("log_entries", "parent_entry_id")
    op.drop_column("log_entries", "message_ids")

    # Drop messages table
    op.drop_index("ix_messages_content_hash", table_name="messages")
    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_table("messages")
