"""Conversation-identity inference from request structure.

Conversation identity is derived from the request message prefix only,
hashed over a normalized projection, matched by longest-prefix overlap.
Responses are never part of identity matching.

See plans/CONVERSATION_ID_NEW_IMPLEMENTATION.md for the full design.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass

from sqlmodel import Session, desc, select

from app.models.log_entry import LogEntry

logger = logging.getLogger(__name__)

# -- Projection ----------------------------------------------------------------


def _canonical_json(obj: object) -> str:
    """Deterministic, compact JSON — reused from messages.py."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _extract_text(content: object) -> str:
    """Extract a plaintext representation from message content.

    - str → returned as-is
    - list of parts → concatenate .text from each dict part
    - anything else → ""
    Images, binary, and non-text parts are deliberately dropped.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                t = part.get("text")# ty:ignore[invalid-argument-type]
                if isinstance(t, str):
                    texts.append(t)
        return " ".join(texts)
    return ""


def _canonical_args(tc: dict) -> str:
    """Normalize a tool-call's arguments to a stable string."""
    func = tc.get("function") or {}
    args = func.get("arguments")
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return _canonical_json(parsed)
        except (json.JSONDecodeError, TypeError):
            return args
    if isinstance(args, dict):
        return _canonical_json(args)
    return ""


def normalize_message_for_identity(msg: dict) -> dict:
    """Project a message dict to a minimal, identity-stable form.

    Only role + text content + a minimal stable tool-call form are kept.
    reasoning, reasoning_details, annotations, and all provider extras are
    deliberately dropped so clients that strip/reserialize those fields on
    resend still produce the same turn key.
    """
    role = str(msg.get("role", "user"))
    text = _extract_text(msg.get("content"))
    out: dict = {"role": role, "content": text}

    tcs = msg.get("tool_calls")
    if isinstance(tcs, list) and tcs:
        out["tool_calls"] = [
            {
                "name": (tc.get("function") or {}).get("name", ""),
                "arguments": _canonical_args(tc),
            }
            for tc in tcs
        ]
    return out


def turn_key(msg: dict) -> str:
    """Return the sha256 hex digest of the normalized projection of *msg*."""
    return _sha256(_canonical_json(normalize_message_for_identity(msg)))


# -- Chain keys ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainKeys:
    turn_keys: list[str]  # turn_key(m) for each request message
    chain_key: str  # sha256 over *all* turn_keys
    chain_prefix_key: str  # sha256 over turn_keys up to the last user anchor
    last_user_index: int  # index of the last user message (≥ 0)


def compute_chain_keys(messages: list[dict]) -> ChainKeys:
    """Derive identity chain keys from raw request messages."""
    keys = [turn_key(m) for m in messages]

    # Anchor on the last user message.
    last_user = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            last_user = i
    # Fallback: if somehow there are no user messages, use the whole list.
    if last_user < 0:
        last_user = len(keys) - 1

    chain_key = _sha256(",".join(keys))
    chain_prefix_key = _sha256(",".join(keys[: last_user + 1]))
    return ChainKeys(
        turn_keys=keys,
        chain_key=chain_key,
        chain_prefix_key=chain_prefix_key,
        last_user_index=last_user,
    )


# -- Inference ----------------------------------------------------------------


def infer_conversation_id(
    messages: list[dict],
    user_id: uuid.UUID,
    db: Session,
    *,
    explicit: str | None = None,
    user_field: str | None = None,
) -> tuple[str, ChainKeys, uuid.UUID | None]:
    """Derive a conversation_id and chain keys for *messages*.

    Resolution order:
      1. explicit (X-Conversation-Id header)
      2. user_field (OpenRouter "user" field)
      3. longest-prefix match via indexed chain_key probes
      4. new conversation (mint a fresh UUID-based id)

    Returns (conversation_id, chain_keys, matched_entry_id).
    matched_entry_id is None for new conversations.
    """
    # -- 1. explicit header ----------------------------------------------
    if explicit:
        ck = compute_chain_keys(messages)
        return explicit, ck, None

    # -- 2. OpenRouter user field ----------------------------------------
    if user_field:
        ck = compute_chain_keys(messages)
        return f"or-user-{user_field}", ck, None

    # -- 3. prefix-ancestor inference via indexed chain_key probes --------
    ck = compute_chain_keys(messages)
    if len(ck.turn_keys) < 2:
        return _new_conversation(ck)

    # Probe longest proper prefix first, then shorter prefixes.
    # Bound: at most the last 8 user-anchored prefixes to avoid degenerate scans.
    user_anchors: list[int] = [
        i for i, m in enumerate(messages) if m.get("role") == "user"
    ]
    # Start with the longest proper prefix (all turn_keys except the last).
    # Then add each user-anchor boundary as a probe point.
    anchors_to_probe: set[int] = {len(ck.turn_keys) - 1}
    for a in user_anchors:
        anchors_to_probe.add(a + 1)  # prefix length = anchor index + 1

    # Sort descending (longest first) and limit.
    probe_lengths = sorted(anchors_to_probe, reverse=True)[:8]

    for prefix_len in probe_lengths:
        if prefix_len < 2 or prefix_len >= len(ck.turn_keys):
            continue
        prefix_key = _sha256(",".join(ck.turn_keys[:prefix_len]))
        row = db.exec(
            select(LogEntry)
            .where(
                LogEntry.user_id == user_id,
                LogEntry.chain_key == prefix_key,
            )
            .order_by(desc(LogEntry.created_at))
            .limit(1)
        ).first()
        if row is not None and row.conversation_id:
            return row.conversation_id, ck, row.id

    # -- 4. no match → new conversation ----------------------------------
    return _new_conversation(ck)


def _new_conversation(ck: ChainKeys) -> tuple[str, ChainKeys, None]:
    cid = f"or-{uuid.uuid4().hex[:16]}"
    return cid, ck, None
