"""Unit tests for the message interning / rehydration service.

All tests run against the Postgres test DB (via the pg_engine / pg_session
fixtures from tests/conftest.py) because ARRAY and JSONB types used by
several models are not supported by SQLite.
"""

import uuid

import pytest

from app.models.log_entry import LogEntry  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.services.messages import (
    batch_rehydrate_messages,
    content_hash,
    intern_messages,
    rehydrate_messages,
    resolve_parent_entry_id,
)

# ---------------------------------------------------------------------------
# Fixtures — piggyback on the Postgres engine from tests/conftest.py
# ---------------------------------------------------------------------------


@pytest.fixture(name="db")
def db_fixture(pg_session):
    """Expose pg_session as 'db' for readability in these tests."""
    return pg_session


@pytest.fixture(name="db_user_id")
def db_user_id_fixture(db) -> uuid.UUID:
    """Insert a real user row and return its id — satisfies messages.user_id FK."""
    from app.models.user import User
    from app.security.passwords import hash_password

    uid = uuid.uuid4()
    user = User(id=uid, username=f"u_{uid.hex[:8]}", password_hash=hash_password("x"))
    db.add(user)
    db.flush()
    return uid


@pytest.fixture(name="user_id")
def user_id_fixture() -> uuid.UUID:
    """A random UUID with NO corresponding user row — use only for hash/non-DB tests."""
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


def test_hash_is_stable():
    msg = {"role": "user", "content": "hello"}
    assert content_hash(msg) == content_hash(msg)
    assert content_hash(msg) == content_hash({"content": "hello", "role": "user"})


def test_hash_differs_on_content_change():
    assert content_hash({"role": "user", "content": "a"}) != content_hash(
        {"role": "user", "content": "b"}
    )


def test_hash_differs_on_role_change():
    assert content_hash({"role": "user", "content": "x"}) != content_hash(
        {"role": "assistant", "content": "x"}
    )


def test_hash_stable_when_no_reasoning_present():
    """Plain messages (no reasoning fields) must hash identically to before.
    Uses exclude_none=True to ensure absent optional fields don't affect the hash.
    """
    msg = {"role": "user", "content": "hello"}
    expected = content_hash(msg)
    assert expected == content_hash(msg)
    # Round-trip through CanonicalMessage with exclude_none
    from app.schemas.log_entry import CanonicalMessage

    cm = CanonicalMessage(role="user", content="hello")
    assert content_hash(cm.model_dump(exclude_none=True)) == expected


def test_hash_differs_when_reasoning_differs():
    """Two messages with same content but different reasoning must NOT collide."""
    msg1 = {"role": "assistant", "content": "Answer", "reasoning": "Think A"}
    msg2 = {"role": "assistant", "content": "Answer", "reasoning": "Think B"}
    assert content_hash(msg1) != content_hash(msg2)


def test_hash_differs_when_reasoning_details_differs():
    msg1 = {
        "role": "assistant",
        "content": "X",
        "reasoning_details": [{"type": "reasoning.text", "text": "A"}],
    }
    msg2 = {
        "role": "assistant",
        "content": "X",
        "reasoning_details": [{"type": "reasoning.text", "text": "B"}],
    }
    assert content_hash(msg1) != content_hash(msg2)


# ---------------------------------------------------------------------------
# intern_messages — deduplication
# ---------------------------------------------------------------------------


def test_intern_returns_ids_in_order(db, db_user_id):
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    ids = intern_messages(msgs, db_user_id, db)
    assert len(ids) == 2
    assert all(isinstance(i, uuid.UUID) for i in ids)


def test_intern_same_message_same_id(db, db_user_id):
    msg = {"role": "user", "content": "repeat me"}
    ids1 = intern_messages([msg], db_user_id, db)
    ids2 = intern_messages([msg], db_user_id, db)
    assert ids1 == ids2, "Identical message must return same UUID on second intern"


def test_intern_deduplicates_within_call(db, db_user_id):
    """If a single call sends the same message twice, they share one row."""
    from sqlmodel import select

    msg = {"role": "user", "content": "dup"}
    ids = intern_messages([msg, msg], db_user_id, db)
    assert ids[0] == ids[1], "Duplicate messages in one call must map to same id"
    rows = db.exec(select(Message).where(Message.user_id == db_user_id)).all()
    assert len(rows) == 1


def test_intern_user_isolation(db):
    """Same message content under different users must produce different rows."""
    from app.models.user import User
    from app.security.passwords import hash_password

    uid_a, uid_b = uuid.uuid4(), uuid.uuid4()
    for uid, name in [(uid_a, "iso_a"), (uid_b, "iso_b")]:
        db.add(User(id=uid, username=name, password_hash=hash_password("x")))
    db.flush()
    msg = {"role": "user", "content": "shared text"}
    ids_a = intern_messages([msg], uid_a, db)
    ids_b = intern_messages([msg], uid_b, db)
    assert ids_a[0] != ids_b[0], "Users must not share message rows"


def test_intern_grows_history_correctly(db, db_user_id):
    """Simulates multi-turn: call 2 re-sends call 1's messages + new ones."""
    from sqlmodel import select

    turn1 = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
    turn2 = turn1 + [{"role": "user", "content": "How are you?"}]

    ids1 = intern_messages(turn1, db_user_id, db)
    ids2 = intern_messages(turn2, db_user_id, db)

    # First two ids of turn2 must match turn1's ids (reused, not new rows).
    assert ids2[:2] == ids1
    assert len(ids2) == 3

    total_rows = db.exec(select(Message).where(Message.user_id == db_user_id)).all()
    assert len(total_rows) == 3, "Only 3 unique messages across both calls"


def test_intern_empty_list(db, db_user_id):
    assert intern_messages([], db_user_id, db) == []


# ---------------------------------------------------------------------------
# rehydrate_messages
# ---------------------------------------------------------------------------


def test_rehydrate_round_trips(db, db_user_id):
    msgs = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "What is 2+2?"},
    ]
    ids = intern_messages(msgs, db_user_id, db)
    result = rehydrate_messages(ids, db)
    # Content must match (order preserved).
    assert len(result) == 2
    assert result[0]["role"] == "system"
    assert result[1]["content"] == "What is 2+2?"


def test_rehydrate_empty(db):
    assert rehydrate_messages([], db) == []


def test_rehydrate_preserves_order(db, db_user_id):
    msgs = [{"role": "user", "content": str(i)} for i in range(5)]
    ids = intern_messages(msgs, db_user_id, db)
    result = rehydrate_messages(ids, db)
    for i, r in enumerate(result):
        assert r["content"] == str(i)


# ---------------------------------------------------------------------------
# batch_rehydrate_messages
# ---------------------------------------------------------------------------


def test_batch_rehydrate(db, db_user_id):
    msgs_a = [{"role": "user", "content": "a"}]
    msgs_b = [{"role": "user", "content": "b"}, {"role": "assistant", "content": "B"}]
    ids_a = intern_messages(msgs_a, db_user_id, db)
    ids_b = intern_messages(msgs_b, db_user_id, db)

    mapping = batch_rehydrate_messages([ids_a, ids_b], db)
    assert mapping[ids_a[0]]["content"] == "a"
    assert mapping[ids_b[1]]["content"] == "B"


# ---------------------------------------------------------------------------
# resolve_parent_entry_id
# ---------------------------------------------------------------------------


def test_no_parent_for_first_call(db, db_user_id):
    ids = [uuid.uuid4(), uuid.uuid4()]
    result = resolve_parent_entry_id(ids, "conv-1", db_user_id, uuid.uuid4(), db)
    assert result is None


def test_disjoint_messages_no_parent(pg_engine, user_id):
    """Entries with completely different first messages must not link as parent."""
    from sqlmodel import Session

    with Session(pg_engine) as db:
        from app.models.user import User
        from app.security.passwords import hash_password

        user = User(id=user_id, username="u", password_hash=hash_password("x"))
        db.add(user)
        db.flush()

        # Entry A: [msg_apple]
        msgs_a = [{"role": "user", "content": "apple"}]
        ids_a = intern_messages(msgs_a, user_id, db)
        entry_a_id = uuid.uuid4()
        db.add(
            LogEntry(
                id=entry_a_id,
                user_id=user_id,
                conversation_id="conv-disjoint",
                message_ids=ids_a,
                provider="openai",
                model="gpt-4o",
                request={},
                response={
                    "message": {"role": "assistant", "content": "A"},
                    "finish_reason": "stop",
                },
            )
        )
        db.flush()

        # Entry B: [msg_banana, msg_cherry] — completely different first message
        msgs_b = [
            {"role": "user", "content": "banana"},
            {"role": "assistant", "content": "cherry"},
        ]
        ids_b = intern_messages(msgs_b, user_id, db)

        parent = resolve_parent_entry_id(ids_b, "conv-disjoint", user_id, uuid.uuid4(), db)
        assert parent is None, (
            "Disjoint message sets (different first message) must not resolve to a parent"
        )


def test_parent_resolved_for_linear_append(pg_engine, user_id):
    """Entry 2 is a linear append of entry 1; parent should be entry 1's id."""
    from sqlmodel import Session

    with Session(pg_engine) as db:
        # Create a minimal user row so FK is satisfied
        from app.models.user import User
        from app.security.passwords import hash_password

        user = User(id=user_id, username="u", password_hash=hash_password("x"))
        db.add(user)
        db.flush()

        msgs_1 = [{"role": "user", "content": "Hello"}]
        msgs_2 = msgs_1 + [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "More"},
        ]

        ids_1 = intern_messages(msgs_1, user_id, db)
        ids_2 = intern_messages(msgs_2, user_id, db)

        entry_1_id = uuid.uuid4()
        entry_1 = LogEntry(
            id=entry_1_id,
            user_id=user_id,
            conversation_id="conv-x",
            message_ids=ids_1,
            provider="openai",
            model="gpt-4o",
            request={},
            response={
                "message": {"role": "assistant", "content": "Hi"},
                "finish_reason": "stop",
            },
        )
        db.add(entry_1)
        db.flush()

        entry_2_id = uuid.uuid4()
        parent = resolve_parent_entry_id(ids_2, "conv-x", user_id, entry_2_id, db)
        assert parent == entry_1_id
