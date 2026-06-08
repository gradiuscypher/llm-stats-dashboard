# Rebuild: Reliable Conversation-ID Inference From Request Structure

## Goal

Make conversation grouping **inferred automatically** from the LLM requests we
proxy — no per-agent configuration required — while remaining deterministic and
cheap. Support multiple AI agents pointing at the proxy with zero setup. Keep an
explicit override (`X-Conversation-Id` / OpenRouter `user`) as an escape hatch.

This replaces the current fragile scheme that broke conversation chaining (see
"Background" below).

---

## Background: why the current approach regressed

The prior fix (`WORDCOUNT_RESPONSE_INTERNING_FIX_PLAN.md`) made each turn intern
its assistant **response** into `LogEntry.message_ids`, then matched a new turn
by checking whether a prior entry's `message_ids` was a **prefix** of the new
turn's `message_ids`.

That coupled conversation identity to **exact-match round-tripping of the
assistant reply**, which clients reliably alter:

1. **What we store** (`_extract_message_from_choice` in `openrouter_map.py`):
   the *full* assistant message — `content` **plus** `reasoning`,
   `reasoning_details`, `tool_calls`, and provider-specific extras
   (`CanonicalMessage` uses `model_config = {"extra": "allow"}`).
2. **What the client resends next turn**: a *trimmed* assistant message — usually
   just `{role, content}` or `{role, content, tool_calls}`, dropping
   `reasoning_details`, re-serializing tool-call arguments, reordering keys, etc.

`content_hash` is sha256 over canonical JSON of the **whole** message dict
(`messages.py::content_hash` → `_canonical_json` with `sort_keys=True`). Any
difference ⇒ different hash ⇒ the stored `asst1` never equals the resent `asst1`
⇒ the prefix chain breaks ⇒ **every turn mints a fresh UUID.**

A second aggravator: request-mutating plugins like `word_count`
(`word_count.py` lines 69/96 mutate `messages[...]["content"]` in place) change
even user-message hashes between store-time and resend-time. (Mitigated today
only because pre-call resolution runs before `on_request`; see "Ordering".)

### Core principle for the rebuild

> **Conversation identity is a function of the request message prefix only,
> hashed over a normalized projection, matched by longest-prefix overlap.
> Responses are never part of identity matching.**

The request prefix is the stable, client-controlled part. Each turn's request =
prior turn's request + (prior assistant reply) + new user turn, so consecutive
turns share a long identical leading run we can match on.

---

## Decisions (locked)

1. **Normalization** — hash a minimal projection of each message:
   `role` + canonicalized **text** content + a stable, minimal tool-call form.
   Ignore `reasoning`, `reasoning_details`, annotations, and all provider extras.
   (Confirmed: text-only-ish, with tool calls folded to `{name, arguments}` in a
   stable order so tool-using turns still chain.)
2. **Mid-conversation history mutation** (trim/summarize) — if the leading prefix
   no longer matches any prior entry, **treat as a new conversation.** No attempt
   to re-link rewritten histories.
3. **Dedicated indexed chain key** — store a `chain_key` (and `chain_prefix_key`)
   on `LogEntry` so matching is an **indexed lookup**, not a 500-row scan.
   Decouple identity from the message-interning representation.

---

## Design

### What identifies a conversation

For a request with messages `m[0..n-1]` (already excluding any trailing assistant
the client may have appended — see "Trailing assistant" below), we compute:

- **`turn_keys`**: `turn_key(m[i])` for each message, where `turn_key` is the
  sha256 of the **normalized projection** of that message (see below).
- **`chain_prefix_key`**: a hash over `turn_keys[0 .. k]` where `k` is the index
  of the **last `user` message** in the request. Rationale: the assistant turns
  in between are part of history but the *user-authored* boundary is the most
  stable anchor; we chain on everything up to and including the latest user turn.
  Concretely `chain_prefix_key = sha256(join(turn_keys[0..k]))`.
- **`chain_key`**: a hash over **all** `turn_keys` of this request
  (`sha256(join(turn_keys))`). This is the value a *future* turn will look for as
  a prefix.

Matching a new request to an existing conversation:

1. Compute the new request's `turn_keys`.
2. For decreasing prefix length `L` from `len(turn_keys)-1` down to `1`
   (we need a **proper** prefix; a full-length equal match is a retry, not a
   continuation — see "Retry vs continue"), compute
   `prefix_key = sha256(join(turn_keys[0..L-1]))` and look for an existing
   `LogEntry` (same user) whose **`chain_key == prefix_key`**.
3. The **first hit at the longest `L`** wins; return its `conversation_id`.
4. No hit ⇒ **new conversation** (mint `or-<uuid16>`).

Because `chain_key` is indexed, each probe is an indexed equality lookup. We cap
the number of probes (see "Performance") so worst case is O(small constant)
indexed lookups, not a table scan.

> Note: we no longer rely on `message_ids` prefix comparison for identity at all.
> `message_ids` continues to exist for the transcript/dedup read path; identity
> is fully delegated to `chain_key`.

### Normalized projection (`normalize_message` → `turn_key`)

```
def normalize_message_for_identity(msg: dict) -> dict:
    role = str(msg.get("role", "user"))
    # text content only: str stays; list[parts] → concatenated text parts;
    # dict/other → "" (no text). This drops images/binary deliberately.
    text = extract_text(msg.get("content"))
    out = {"role": role, "content": text}
    # minimal, stable tool-call form so tool turns chain
    tcs = msg.get("tool_calls")
    if isinstance(tcs, list) and tcs:
        out["tool_calls"] = [
            {
                "name": (tc.get("function") or {}).get("name", ""),
                # arguments normalized: parse JSON-string → canonical dump,
                # else canonical dump of dict; fall back to "" on failure
                "arguments": canonical_args(tc),
            }
            for tc in tcs
        ]
    # deliberately ignored: reasoning, reasoning_details, name, tool_call_id,
    # annotations, and ALL provider extras
    return out

def turn_key(msg) -> str:
    return sha256(canonical_json(normalize_message_for_identity(msg)))
```

`canonical_json` reuses the existing `_canonical_json` (sort_keys, compact). This
makes the projection robust to the documented client mutations (dropped
reasoning, reordered keys, tool-arg re-serialization).

### Trailing assistant handling

Some clients resend the **assistant reply as the last message** of the next
request before appending the new user turn at upstream time; others send the new
user turn directly. To make `chain_prefix_key` stable we anchor on the **last
user message index** rather than raw length, so a trailing assistant doesn't
shift the anchor. This also makes "store-time `chain_key`" and "resend-time
prefix probe" line up.

### Retry vs continue

- **Continue:** new request's `turn_keys` has a proper prefix equal to a prior
  entry's `chain_key` (strictly shorter) ⇒ same conversation.
- **Retry / edit of the latest turn:** new request `turn_keys` is **equal length
  or shorter** than the matched entry, or differs before the last user anchor ⇒
  treat per existing branch logic. For identity purposes a same-length match is
  **not** a continuation; it resolves to the same conversation only if it shares
  the proper prefix up to the previous user anchor (so retries land in the same
  conversation, consistent with today's branch/retry transcript handling).

  Precise rule: find the longest proper-prefix `chain_key` match. If none, but the
  request shares the prefix up to the **second-to-last** user anchor with an
  existing entry, treat as a retry/branch of that conversation. Else new.

### Explicit override precedence (unchanged escape hatch)

Resolution order stays:

1. `X-Conversation-Id` header (explicit) — wins outright.
2. OpenRouter `user` field → `or-user-<user>`.
3. **Inferred chain match** (the new mechanism above).
4. New conversation (`or-<uuid16>`).

---

## Schema changes

Add two indexed columns to `log_entries`:

- `chain_key: str | None` — `sha256` hex (64 chars), indexed.
  Hash over **all** `turn_keys` of this entry's request (the value future turns
  probe as a prefix).
- `chain_prefix_key: str | None` — `sha256` hex, indexed.
  Hash up to and including the last user anchor (used for retry/branch matching
  and as a secondary probe).

Both nullable (old rows stay `NULL`; they simply won't match and will read as
their own pre-existing `conversation_id`, which is fine).

`backend/app/models/log_entry.py`:

```python
chain_key: str | None = Field(default=None, index=True, max_length=64)
chain_prefix_key: str | None = Field(default=None, index=True, max_length=64)
```

Migration (`make migration m="add conversation chain keys"`), `down_revision =
'246f8a09693d'` (current head). Add columns + two btree indexes
(`ix_log_entries_user_chain_key` on `(user_id, chain_key)`,
`ix_log_entries_user_chain_prefix` on `(user_id, chain_prefix_key)`).
No backfill required (correctness goal #2: unmatched ⇒ new conversation).

---

## Code changes

### New module: `backend/app/services/conversation_identity.py`

Single home for identity logic (decouples from `messages.py` interning):

- `extract_text(content) -> str`
- `normalize_message_for_identity(msg: dict) -> dict`
- `turn_key(msg: dict) -> str`
- `compute_chain_keys(messages: list[dict]) -> ChainKeys`
  returns `{turn_keys, chain_key, chain_prefix_key, last_user_index}`.
- `infer_conversation_id(messages, user_id, db, *, explicit=None, user_field=None) -> ConversationResolution`
  returns `{conversation_id, chain_key, chain_prefix_key, matched_entry_id|None,
  is_new: bool}`. Implements the full precedence + longest-proper-prefix indexed
  lookup with a bounded number of probes.

This module performs the indexed lookups via:

```sql
SELECT conversation_id FROM log_entries
WHERE user_id = :uid AND chain_key = :prefix_key
ORDER BY created_at DESC LIMIT 1
```

one probe per candidate prefix length, longest first, **bounded** (see
Performance). It does **not** scan or sort 500 rows.

### `backend/app/services/openrouter_map.py`

- **Remove** identity logic from `derive_conversation_id` /
  `candidate_conversation_id` / `_resolve_prefix_ancestor`. Replace their bodies
  with thin wrappers that delegate to `conversation_identity.infer_conversation_id`
  (keeping signatures for call-site compatibility), or delete and update callers.
- Stop passing `message_ids` for identity; identity now uses raw request messages.

### `backend/app/routers/proxy.py`

- Pre-call: call `infer_conversation_id(ctx.request_body["messages"], user.id, db,
  explicit=header, user_field=body.get("user"))` to get the conversation id used
  for plugin-override resolution (`_build_pipeline`).
  **Capture the resulting `chain_key`/`chain_prefix_key` from the ORIGINAL,
  unmutated request and stash on `ctx.state`** (e.g. `ctx.state["identity"]`)
  so the logging plugin reuses them. This is critical: `candidate_conversation_id`
  already runs *before* `pipeline.on_request` (confirmed: router computes
  candidate at line ~320, builds pipeline, then `on_request` at ~190/227), so the
  keys reflect the client's real messages, not `word_count`-mutated ones.

### `backend/app/proxy/plugins/logging.py`

- `on_response`: reuse `ctx.state["identity"]` (chain keys + resolved
  conversation_id) instead of recomputing from the possibly-mutated request.
  Persist `chain_key` and `chain_prefix_key` on the `LogEntry`.
- **Revert response-in-identity**: stop appending the assistant response into the
  `intern_messages` call *for identity*. We still want the response available for
  the transcript, so:
  - Keep interning the response into `message_ids` for transcript completeness
    (the transcript fix in `logs.py` already handles the trailing-reply de-dup),
    **OR**
  - Move the response out of `message_ids` again and rely solely on
    `_synthetic_trailing_reply`.
  - **Decision:** keep response in `message_ids` (transcript correctness already
    built around it), but identity no longer depends on it. Identity = `chain_key`.
- `on_error`: compute/persist `chain_key` from request only (no response).

### `backend/app/services/ingest.py`

- Accept optional `chain_key` / `chain_prefix_key` on the create path (or compute
  from `payload.request.messages` when not provided, so the **push API** also gets
  chain keys). Persist them on the `LogEntry`.
- Keep current message interning behavior for `message_ids` (request + response)
  unchanged — identity is now separate.

### `backend/app/models/log_entry.py`

- Add the two fields (above).

### `backend/app/proxy/context.py`

- Document/initialize `ctx.state["identity"]` usage (no structural change needed
  if `state` is a free-form dict).

---

## Performance

- **Matching**: longest-proper-prefix search via **indexed equality lookups** on
  `(user_id, chain_key)`. Probe order: longest prefix first.
  **Bound the probes**: cap at the last `P` user-anchored prefixes (e.g. probe the
  prefix ending at the last user message, then the previous user message, up to
  `P = 8` anchors). A continuing turn matches on the very first probe (the full
  prior request = current request minus the new user turn), so the common case is
  **one indexed lookup**. Worst case `P` indexed lookups. No table scans, no
  500-row sort (removes the `_resolve_prefix_ancestor` scan entirely).
- **Hashing**: `len(messages)` sha256 over small normalized dicts, same order of
  cost as today's `content_hash`, computed once pre-call and reused at log time.
- **Writes**: two extra indexed columns; negligible.

---

## Tests (regression coverage is the point)

New `backend/tests/unit/test_conversation_identity.py`:

- **Normalization robustness (the bug):**
  - Stored assistant msg has `reasoning` + `reasoning_details` + provider extras;
    resent assistant msg is `{role, content}` only ⇒ **same `turn_key`** ⇒ turn 2
    chains to turn 1's conversation. (Directly reproduces the regression.)
  - Tool-call arguments as JSON-string vs dict ⇒ same `turn_key`.
  - Key reordering / extra provider fields ⇒ same `turn_key`.
- **Chaining:**
  - 3-turn growing history ⇒ all three share one conversation_id.
  - Longest-proper-prefix wins when multiple ancestors exist.
- **Retry/branch:** resend identical history (no new user turn) ⇒ same
  conversation (retry), not a new one; differing edit before last user anchor ⇒
  new/branch per rule.
- **First-message safety / unrelated threads:** different system prompt ⇒ no match.
- **Mid-conversation rewrite:** trimmed/summarized history that no longer shares
  the leading prefix ⇒ new conversation (decision #2).
- **Explicit precedence:** `X-Conversation-Id` and `user` field win over inference.
- **Multi-agent, no config:** two interleaved agents with distinct openings get
  distinct conversation_ids automatically; each agent's turns chain correctly.

Update/extend `backend/tests/proxy/test_proxy_api.py`:

- End-to-end: turn 1 (assistant reply with reasoning), turn 2 resends a trimmed
  assistant message ⇒ both land in the **same** conversation (queried via the
  logs/conversation API after letting the background task run, or via the
  synchronous stream path which logs inline).
- Per-conversation override applied after turn 1 still fires on turn 2
  (the original `WORDCOUNT_RESPONSE_INTERNING_FIX` scenario) — now robust because
  identity no longer depends on the assistant reply round-tripping.

Update `backend/tests/proxy/test_proxy_unit.py`:

- Replace `TestResolveCandidateConversationId` assertions that depend on
  message-id prefixing with chain-key-based equivalents.

Regression guard: existing transcript tests in `tests/api/test_logs.py` must
still pass (response stays in `message_ids`; identity is orthogonal).

---

## Migration / rollout steps

1. `make migration m="add conversation chain keys"`; edit to add columns + indexes.
2. `make migrate`.
3. Implement `conversation_identity.py`; wire `proxy.py`, `logging.py`,
   `ingest.py`, `openrouter_map.py`, `log_entry.py`, `context.py`.
4. `make test-backend` — new identity tests + updated proxy/api tests green;
   transcript regression tests green.
5. Manual: point two un-configured agents (different system prompts) at the proxy,
   run multi-turn sessions interleaved; confirm each session groups correctly with
   **no** `X-Conversation-Id`. Confirm a trimmed-assistant resend still chains.
6. `make check` (lint + test) green.

---

## Docs to update

- `docs/proxy.md` / `docs/plugins.md`: conversation IDs are inferred from request
  structure automatically (no per-agent config); `X-Conversation-Id` / `user`
  remain explicit overrides; mid-conversation history rewrites start a new
  conversation by design.
- `OVERVIEW.md`: note `LogEntry.chain_key` / `chain_prefix_key` and that
  conversation identity is request-prefix-based (decoupled from `message_ids`).

---

## Files touched (summary)

- `backend/app/services/conversation_identity.py` (new — normalization, chain
  keys, inference)
- `backend/app/models/log_entry.py` (`chain_key`, `chain_prefix_key` + indexes)
- `backend/alembic/versions/<new>_add_conversation_chain_keys.py` (migration)
- `backend/app/routers/proxy.py` (pre-call inference; stash identity on `ctx.state`)
- `backend/app/proxy/plugins/logging.py` (reuse identity; persist chain keys;
  revert response-in-identity coupling)
- `backend/app/services/ingest.py` (accept/compute + persist chain keys; push API)
- `backend/app/services/openrouter_map.py` (delegate/remove old identity logic,
  including `_resolve_prefix_ancestor`)
- `backend/app/proxy/context.py` (identity slot in `state`)
- `backend/tests/unit/test_conversation_identity.py` (new)
- `backend/tests/proxy/test_proxy_unit.py`, `backend/tests/proxy/test_proxy_api.py`
  (update)
- `docs/proxy.md`, `docs/plugins.md`, `OVERVIEW.md`
