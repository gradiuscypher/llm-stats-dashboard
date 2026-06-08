# Fix: Per-Conversation Plugin Overrides Still Don't Apply (Responses Are Never Interned)

## Summary

For conversation `or-61aa1d7c974b451a`, the `word_count` plugin was **enabled via
a per-conversation override** (global = OFF, per-conversation = ON) **after the
first turn**, yet turns 2 and 3 recorded **zero modifications** and no
`[word_count: N]` marker. The plugin never ran, even though the override existed
before those turns.

This is a *follow-on* bug to `WORDCOUNT_PER_CONVERSATION_FIX_PLAN.md`. That fix
added pre-call prefix-ancestor inheritance, but it still can't match continuing
threads because **a turn never interns its own assistant response** — only its
request messages.

## Evidence (from the dev DB)

Timeline for `or-61aa1d7c974b451a`:

```
02:11:15.52  TURN 1  message_ids n=2   (system + user1)
02:11:26.41  OVERRIDE word_count = true   ← enabled here
02:11:35.79  TURN 2  message_ids n=4
02:12:26.24  TURN 3  message_ids n=6
```

- Global `word_count = OFF`; per-conv override `word_count = ON` created at
  02:11:26 — **before** turns 2 and 3.
- `message_modifications`: **0 rows** for all three entries. No assistant message
  contains `[word_count: N]`. Turn 3's user text literally says *"This message is
  after the word count plugin for the proxy should be enabled."*
- Turn 1's `LogEntry.message_ids` has only **2** ids (system + user1) — the
  assistant reply is **absent**.
- The assistant reply's `messages` row was interned at **02:11:35.68899** — the
  exact moment **turn 2** arrived (turn 2 resent it), **not** when turn 1
  completed (02:11:15).

## Root cause

The proxy resolves which plugins run **before** the upstream call, via
`candidate_conversation_id(...)` → `resolve_candidate_conversation_id(...)`
(`backend/app/services/messages.py`). That function:

1. Hashes the incoming request messages.
2. Bails entirely if **any** hash is not already interned (`if None in
   message_ids: return None`).
3. Otherwise looks for a `LogEntry` whose `message_ids` is a proper prefix.

But the logging path only interns the **request** side:

- `LoggingPlugin.on_response` (`backend/app/proxy/plugins/logging.py`) calls
  `intern_messages(payload.request.messages, ...)`.
- `map_to_log_entry` puts the assistant reply in `payload.response`, which is
  **never interned** and **never added to `LogEntry.message_ids`**.

So at **turn 2 request time**, turn 1's assistant reply isn't interned yet →
`resolve_candidate_conversation_id` hits `None in message_ids` → returns `None`
→ `candidate_conversation_id` falls through to the **random-UUID fallback** →
`resolve_pipeline` finds no override for that random id → falls back to the
**global `word_count = OFF`** → word_count is excluded → no modification, no
marker. Same on turn 3.

The *post-call* logger (`derive_conversation_id` → `_resolve_prefix_ancestor`)
still assigns `or-61aa1d7c974b451a` correctly, because it only needs the
request-side prefix (system+user1) to match — which is why the conversation
groups correctly in the UI even though the override never fired.

## Fix

Intern the assistant **response** of each turn (and include it in
`message_ids`), so a later turn's resent history is fully hash-resolvable
pre-call. Also relax the all-or-nothing pre-call match to a longest-interned-
prefix match so the trailing new user message of a continuing turn doesn't block
resolution.

### 1. `backend/app/proxy/plugins/logging.py` (`on_response`)

- Build the full ordered message list = `payload.request.messages` **+
  `payload.response.message`** (the assistant reply).
- Intern the full list; pass it to `derive_conversation_id` and persist it as
  `LogEntry.message_ids` (so a continuing turn's prefix includes the prior
  assistant reply).
- Keep idempotency — `intern_messages` is a content-hashed upsert, so
  re-interning on the next turn returns the same ids.
- Leave `on_error` interning request-only (no assistant reply on errors).

### 2. `backend/app/services/ingest.py` (`ingest_log_entry`)

- Make `message_ids` persisted on the `LogEntry` consistently include request +
  response. Centralize the "what goes into message_ids" decision here so the
  logger and ingest can't diverge.
- Update any existing test that asserts an exact `message_ids` length.

### 3. `backend/app/services/messages.py` (`resolve_candidate_conversation_id`)

- Replace the strict `if None in message_ids: return None` with a
  **longest-interned-prefix** match: resolve as many leading hashes as exist
  (require ≥ 2), and run prefix-ancestor matching on that known leading prefix.
  This lets turn 2 match on `[system, user1, assistant1]` even though the new
  `user2` isn't interned yet.
- Add the **first-message safety check** the post-call path already has
  (`prefix[0] == message_ids[0]`), which is currently missing here.

## Performance (verified, acceptable)

Measured on the dev DB (493 entries, 1 user, 36 conversations):

- **Interning hash lookup**: index scan on `uq_messages_user_hash`, **~0.08 ms**;
  scales with `IN`-list size, not table size. The fix adds exactly **one** more
  message (the assistant reply) per turn.
- **Prefix-ancestor scan** already runs on the hot path today (added by the prior
  fix). At 493 rows: **~1.2 ms** (seq scan only because the table is tiny). The
  fix doesn't add a new call — it just makes the existing scan match more often.

No new query *types* on the hot path. Add this cheap hardening in the same change
(the prior plan flagged it as optional):

- In `_resolve_prefix_ancestor` **and** `resolve_candidate_conversation_id`,
  select only `id, conversation_id, message_ids, created_at` instead of
  `SELECT *` (avoids fetching wide JSON rows, `width=1322`).
- Add `.limit(500)` (most recent) to both candidate scans. A continuing thread's
  ancestor is always recent, so this bounds the scan to O(500) without changing
  correctness in practice.

## Tests

`backend/tests/proxy/`:

**Unit (`test_proxy_unit.py`)**
- After logging turn 1 (request **+ response**), `resolve_candidate_conversation_id`
  for turn 2's resent history returns turn 1's `conversation_id` (not a random
  id), even though turn 2's trailing new user message isn't interned yet.
- First-message safety: an unrelated thread with a different system/user prefix
  does **not** match.

**API/integration (`test_proxy_api.py`)**
- Global `word_count = OFF`; create per-conversation override `ON` after turn 1;
  turn 2 (resends history) → response body contains `[word_count: N]` **and** a
  `message_modifications` row exists for turn 2's entry. (Reproduces this bug.)
- Control: global OFF + no override → no marker on any turn.

**Regression**
- Existing conversation-grouping tests still pass. `message_ids` now include the
  assistant response, so update any test asserting exact `message_ids` lengths.

## Verification steps

1. `make migrate` (no schema change in this fix).
2. `make test-backend` — new + existing proxy tests green.
3. Manual: with global `word_count = OFF` and a per-conversation override `ON`,
   send a continuing turn that resends the thread's history through the proxy;
   confirm the response contains `[word_count: N]`, a new `message_modifications`
   row is written, and the transcript divider shows the `✎` badge.
4. `make check` (lint + test) green.

## Files touched

- `backend/app/proxy/plugins/logging.py` (intern request **+** response; store
  full `message_ids`)
- `backend/app/services/ingest.py` (consistent `message_ids` incl. response)
- `backend/app/services/messages.py` (`resolve_candidate_conversation_id`:
  longest-interned-prefix + first-message safety check; column-select + `.limit`
  hardening on both candidate scans, incl. `_resolve_prefix_ancestor`)
- `backend/tests/proxy/test_proxy_unit.py`, `backend/tests/proxy/test_proxy_api.py`
- `docs/proxy.md` / `docs/plugins.md` (overrides apply from the next turn after
  enabling, deterministically, because responses are now interned;
  `X-Conversation-Id` remains the turn-1-deterministic escape hatch)
- `OVERVIEW.md` (note `message_ids` now includes the assistant response)
