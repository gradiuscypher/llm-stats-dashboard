# Fix: Per-Conversation Plugin Overrides Never Apply on the Proxy Path

## Summary

For conversation `or-a2e773cd9ef84007`, the `word_count` plugin is **enabled via a
per-conversation override** (global = OFF, per-conversation = ON), yet **zero
modifications** were recorded and the messages contain no `[word_count: N]`
marker. The plugin never ran for this conversation.

## Root cause — conversation-id chicken-and-egg

Per-conversation plugin overrides are keyed on `conversation_id`. But on the
OpenRouter proxy path, the stable `conversation_id` for a multi-turn thread is
**only known *after* the call** (it's derived by prefix-ancestor inheritance in
`derive_conversation_id`, which needs interned messages + a DB lookup).

The pipeline, however, is built **before** the call:

```
proxy.py (request handler)
  candidate_conv = candidate_conversation_id(body, prefix, explicit=header)   # PRE-call
  pipeline = resolve_pipeline(user.id, candidate_conv, db)                    # uses candidate
  ... run pipeline (on_request / forward / on_response) ...
  LoggingPlugin → derive_conversation_id(... message_ids, db ...)             # POST-call (real id)
```

`candidate_conversation_id` (in `app/services/openrouter_map.py`) only does the
cheap, no-DB steps:

1. explicit `X-Conversation-Id` header → use it
2. OpenRouter `user` field → `or-user-<user>`
3. **otherwise mint a fresh random UUID** (`or-<random16>`)

For this conversation there was **no `X-Conversation-Id` header and no `user`
field** (confirmed: `request.user` is `None` on both entries). So step 3 fired
and produced a **fresh random id every request** — e.g. some `or-XXXX` that has
nothing to do with `or-a2e773cd9ef84007`.

`resolve_pipeline(user_id, candidate_conv, db)` then looked for a
`plugin_config_conversation` row matching that random id, found none, and fell
back to the **global** setting (`word_count = OFF`). Result: word_count was
excluded from the pipeline, so it never mutated messages and never recorded a
modification.

The real id `or-a2e773cd9ef84007` was assigned later by the logging plugin's
prefix-ancestor inheritance — far too late to affect which plugins ran.

### Why this is the common case, not an edge case

The plan (`PLUGIN_TOGGLE_AND_WORDCOUNT_PLAN.md` §4.2) acknowledged that a
pre-call candidate id "may not match" the final derived id, and pointed at
`X-Conversation-Id` as the escape hatch. But in practice **any** proxy client
that doesn't send `X-Conversation-Id` or a `user` field — which is the default
for OpenRouter/OpenAI SDKs — gets a fresh random candidate on *every* call.
That means **per-conversation overrides are effectively dead** on the proxy path
for the most common configuration. This is a correctness bug, not a niche edge.

## Fix

Make the pre-call candidate id resolution **also do prefix-ancestor inheritance**
so it matches the same `conversation_id` the logging plugin will later assign.
The DB + interned messages are available at request time in the router (we open
a session there already), so we can intern the request messages and run the
existing `_resolve_prefix_ancestor` *before* building the pipeline.

This makes the pre-call candidate id consistent with the post-call authoritative
id for continuing threads, so per-conversation overrides resolve correctly.

### Exact changes

**1. `app/services/openrouter_map.py` — add DB-aware candidate resolution.**

Extend `candidate_conversation_id` to optionally accept `user_id`, `message_ids`,
and `db`, and consult `_resolve_prefix_ancestor` between the `user`-field step
and the random-UUID fallback — mirroring `derive_conversation_id`'s order:

```python
def candidate_conversation_id(
    request_body: dict,
    api_key_prefix: str,
    explicit: str | None = None,
    *,
    user_id: _uuid.UUID | None = None,
    message_ids: list[_uuid.UUID] | None = None,
    db: Session | None = None,
) -> str:
    if explicit:
        return explicit
    user_field = request_body.get("user")
    if isinstance(user_field, str) and user_field.strip():
        return f"or-user-{user_field}"
    # NEW: prefix-ancestor inheritance so continuing threads resolve to their
    # existing conversation_id pre-call (same logic the logging plugin uses).
    if message_ids and db is not None and user_id is not None:
        parent = _resolve_prefix_ancestor(message_ids, user_id, db)
        if parent is not None:
            return parent
    return f"or-{_uuid.uuid4().hex[:16]}"
```

Keep the old positional signature working (new args are keyword-only with
defaults), so existing callers/tests don't break.

> Note: the random-UUID fallback for genuinely new conversations is fine — a
> brand-new thread has no per-conversation override yet anyway, and the logging
> plugin will mint the same kind of id, so the first call's logged id and the
> pipeline's candidate id agree closely enough. (See "Residual first-call
> behaviour" below.)

**2. `app/routers/proxy.py` — intern messages and pass DB context to the candidate.**

In both `proxy_chat_completions` and `proxy_completions`, before building the
pipeline, intern the request messages and pass them in:

```python
from app.services.messages import intern_messages

conv_id_header = ctx.request_headers.get("x-conversation-id")
# Intern request messages so prefix-ancestor inheritance can run pre-call.
raw_messages = ctx.request_body.get("messages", [])
message_ids = intern_messages(raw_messages, user.id, db) if raw_messages else []
candidate_conv = candidate_conversation_id(
    ctx.request_body,
    api_key.prefix,
    explicit=conv_id_header,
    user_id=user.id,
    message_ids=message_ids,
    db=db,
)
pipeline = _build_pipeline(user.id, candidate_conv, db)
```

`intern_messages` is idempotent (content-hashed upsert), so interning here and
again inside `ingest_log_entry` returns the same ids with no duplication — this
is already how `LoggingPlugin` interns-then-ingests today.

> `/completions` (legacy text) has no `messages` array; it will just skip the
> prefix step and keep the random-UUID fallback. Acceptable — same as today.

**3. (Optional, defensive) `_resolve_prefix_ancestor` ordering.**

It currently fetches all of the user's entries and sorts in Python. Leave as-is
for the fix, but note it now runs on the hot request path (pre-call) in addition
to the logging path. Add a `.limit(...)` (e.g. most recent 500 entries) or an
index-backed query in a follow-up if this becomes a perf concern. Not required
for correctness.

## Residual first-call behaviour (documented, acceptable)

- **Continuing turns** (history resent): candidate now matches the real
  conversation id → per-conversation overrides apply correctly. ✅ (fixes the bug)
- **First turn of a brand-new thread**: no prefix ancestor exists, so candidate
  is a fresh UUID and the logging plugin also mints a fresh UUID. There is no
  per-conversation override to honour on a conversation that doesn't exist yet,
  so this is correct. The override starts applying from the *second* turn
  onward, which is exactly when the conversation id stabilises. This matches the
  documented "overrides apply to future calls in that conversation" semantics.
- **Clients that send `X-Conversation-Id`**: unchanged, deterministic from turn 1.

## Tests

Add to `backend/tests/proxy/`:

1. **Unit (`test_proxy_unit.py`)** — `candidate_conversation_id` with DB:
   - given a prior `LogEntry` whose `message_ids` is a proper prefix of the new
     request's interned messages, the candidate returns that entry's
     `conversation_id` (not a random UUID).
   - no prefix ancestor → returns a fresh `or-...` id.
   - explicit header / `user` field still take precedence.

2. **API/integration (`test_proxy_api.py`)** — end-to-end with mocked upstream:
   - Set global `word_count = OFF`, per-conversation override `ON` for the id
     that turn 1 will produce.
   - Turn 1 (new thread) → establishes the conversation.
   - Turn 2 (resends history) → assert the response body contains
     `[word_count: N]` **and** a `MessageModification` row exists for turn 2's
     entry. (Before the fix: no marker, no modification.)
   - Control: global OFF + no override → no marker on either turn.

3. Regression: existing conversation-grouping tests must still pass (the
   candidate change reuses the same `_resolve_prefix_ancestor` the logging path
   already used, so grouping is unchanged).

## Verification steps

1. `make migrate` (no schema change needed — none in this fix).
2. `make test-backend` — new + existing proxy tests green.
3. Manual: with the existing per-conversation override on
   `or-a2e773cd9ef84007` (global word_count OFF, per-conv ON), send a third turn
   that resends the thread's history through the proxy; confirm:
   - the response contains `[word_count: N]`,
   - a new `message_modifications` row is written for that call,
   - the transcript divider shows the `✎` badge and the message shows
     "modified by word_count".
4. `make check` (lint + test) green.

## Files touched

- `backend/app/services/openrouter_map.py` (extend `candidate_conversation_id`)
- `backend/app/routers/proxy.py` (intern messages + pass DB context, both routes)
- `backend/tests/proxy/test_proxy_unit.py` (new unit tests)
- `backend/tests/proxy/test_proxy_api.py` (new integration test)
- `docs/proxy.md` (clarify that per-conversation overrides now apply from the
  second turn onward for derived ids; `X-Conversation-Id` for turn-1 determinism)
- `OVERVIEW.md` / plan note if needed
