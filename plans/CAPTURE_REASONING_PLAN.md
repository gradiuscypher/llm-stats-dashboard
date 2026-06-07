# Plan: Capture reasoning / "thinking" blocks through the proxy

## Problem

Clients that enable reasoning (a.k.a. "thinking") on a model see the intermediate
reasoning steps in their own client, but those blocks **never appear in the proxy
logs / dashboard**. The proxy silently drops all reasoning content on both the
streaming and non-streaming paths, plus the associated reasoning token usage.

Goal: capture **all** assistant output the upstream returns — regular content,
tool calls, **and reasoning** — so the dashboard gives maximum review/adjust ability
over LLM usage.

## Background: how OpenRouter exposes reasoning

OpenRouter normalizes reasoning across providers into the assistant message:

- **Non-streaming** — in `choices[0].message`:
  - `reasoning`: a plain string (normalized human-readable reasoning text).
  - `reasoning_details`: a structured array of reasoning blocks (provider-native,
    may include `type`, `text`, `summary`, `signature`, encrypted/redacted blocks,
    etc.). This is the round-trippable form and the superset of `reasoning`.
- **Streaming** — in `choices[0].delta`:
  - `delta.reasoning`: incremental reasoning text fragments (stream like `content`).
  - `delta.reasoning_details`: incremental structured reasoning fragments.
- **Usage** — `usage.completion_tokens_details.reasoning_tokens` (int) when present.

We must preserve both `reasoning` (string) and `reasoning_details` (structured),
since some providers only round-trip correctly via `reasoning_details` and it can
contain redacted/encrypted blocks the plain string omits.

## Current state — where reasoning is dropped

Verified there is **zero** reasoning/thinking handling anywhere in `backend/app/`,
`frontend/src/`, or `docs/`. Specific drop points:

1. **`backend/app/proxy/assembler.py` — `StreamAssembler`**
   - `feed()` only accumulates `delta.content` (text) and `delta.tool_calls`.
     It ignores `delta.reasoning` and `delta.reasoning_details`.
   - `assemble()` builds a `message` with only `role` / `content` / `tool_calls`.
   - It does not preserve `usage.completion_tokens_details`.
   - **Net effect:** for streamed calls (the path that surfaces thinking in the
     client), reasoning is gone before logging even runs.

2. **`backend/app/services/openrouter_map.py`**
   - `_extract_message_from_choice()` → `_to_canonical_message()` builds
     `CanonicalMessage(role, content)` using only `role` + `content`. The
     `reasoning` / `reasoning_details` keys on the upstream message are dropped,
     even though `CanonicalMessage` has `extra="allow"`.
   - `UsagePayload` mapping ignores `completion_tokens_details.reasoning_tokens`.

3. **Schema (`backend/app/schemas/log_entry.py`)**
   - `CanonicalMessage` allows extra fields (`extra="allow"`), so it *can* store
     `reasoning` / `reasoning_details` once the mapper passes them through — but
     nothing currently does, and we should make the fields explicit for clarity.
   - `UsagePayload` has no `reasoning_tokens` field.

4. **Frontend (`frontend/src/routes/conversation.tsx`, `log-detail.tsx`)**
   - `contentText()` only stringifies `content`. No rendering for reasoning.
   - `frontend/src/lib/api.ts` types don't include reasoning.

## Design decisions (all resolved)

- **Store reasoning on the assistant message**, not as a separate top-level field.
  Reasoning is part of the assistant turn; keeping it on the message keeps the
  canonical shape coherent and message-dedup/interning still works.
- **Preserve both `reasoning` (string) and `reasoning_details` (structured).**
  Add them as explicit optional fields on `CanonicalMessage`.
- **Capture reasoning on BOTH request and response messages.** Clients resend prior
  assistant turns (including `reasoning_details`) on multi-turn reasoning calls.
  Since both paths go through `_to_canonical_message`, one passthrough fix covers
  request + response. (Confirmed: maximum capture.)
- **Capture `reasoning_tokens`** from `usage.completion_tokens_details.reasoning_tokens`.

### Resolved findings (verified against the code)

- **Message dedup / hashing — no message migration needed.**
  `content_hash()` (`services/messages.py`) hashes the *entire* message dict via
  `_canonical_json` (`json.dumps(sort_keys=True, separators=(",",":"))`), and the
  full dict is stored verbatim in `messages.content` (JSONB). Adding reasoning
  fields automatically (a) makes them part of the hash — two turns differing only
  in reasoning will NOT collide — and (b) persists + rehydrates them with zero
  message-table schema change.
  - **`None`-churn guard:** request messages are interned via `m.model_dump()` in
    `ingest.py` and `proxy/plugins/logging.py`. Adding explicit
    `reasoning: str | None = None` fields would otherwise inject `"reasoning": null`
    into *every* message's canonical JSON, changing all hashes and breaking dedup
    continuity with existing rows. **Fix: use `model_dump(exclude_none=True)` at
    those intern call sites** so messages without reasoning hash identically to
    today. Verify no other field relies on `None` being present (none do).

- **`reasoning_tokens` needs a dedicated column + Alembic migration.**
  Usage is stored as discrete int columns on `log_entries`
  (`prompt_tokens`/`completion_tokens`/`total_tokens`), not JSON. Add a
  `reasoning_tokens int NOT NULL DEFAULT 0` column to match, surface it on
  `LogEntryPublic`/`LogEntryDetail`, and **aggregate it in `stats.py` now**.

- **Redacted/encrypted `reasoning_details` blocks:** store **verbatim** in the DB
  (we log everything); the frontend renders them as a **labeled placeholder**
  (e.g. `[encrypted reasoning block]`) rather than dumping the opaque blob.

- **Thinking-only turns:** `_response_to_transcript_message` (logs.py) currently
  drops a response whose `content` is empty/None. A reasoning-only turn (reasoning
  present, no final content) would be lost. **Relax the guard** so a turn with
  reasoning is still emitted even when `content` is empty.

## Changes

### 1. `backend/app/schemas/log_entry.py`
- Add explicit optional fields to `CanonicalMessage`:
  ```python
  reasoning: str | None = None
  reasoning_details: list[dict[str, Any]] | None = None
  ```
  (Keep `extra="allow"` so unknown provider fields still survive.)
- Add to `UsagePayload`:
  ```python
  reasoning_tokens: int = 0
  ```

### 2. `backend/app/proxy/assembler.py` — `StreamAssembler`
- Add accumulators in `__init__`:
  - `self._reasoning_parts: list[str] = []`
  - `self._reasoning_details: list[dict] = []` (accumulate fragments; OpenRouter
    streams these similarly to tool_calls — append/merge by index where present,
    otherwise append in arrival order).
- In `feed()`:
  - Append `delta.get("reasoning")` (when non-empty) to `_reasoning_parts`.
  - Accumulate `delta.get("reasoning_details", [])` fragments into
    `_reasoning_details`. Mirror the tool-call merge approach: if fragments carry
    an `index`, merge text by index; otherwise append.
  - Capture `usage.completion_tokens_details` (already capturing `usage`; ensure the
    nested details object is retained — it already is since the whole `usage` dict
    is stored).
- In `assemble()`:
  - If `_reasoning_parts` non-empty, set `message["reasoning"] = "".join(...)`.
  - If `_reasoning_details` non-empty, set `message["reasoning_details"] = [...]`.

### 3. `backend/app/services/openrouter_map.py`
- In `_to_canonical_message()`: when constructing `CanonicalMessage`, pass through
  `reasoning` and `reasoning_details` from the source dict:
  ```python
  return CanonicalMessage(
      role=role,
      content=content,
      reasoning=msg.get("reasoning"),
      reasoning_details=msg.get("reasoning_details"),
  )
  ```
  Keep the existing JSON-fallback branch (it already preserves the whole dict).
- In `map_to_log_entry()` usage mapping, capture reasoning tokens:
  ```python
  details = usage.get("completion_tokens_details") or {}
  reasoning_tokens = details.get("reasoning_tokens", 0) or 0
  usage_payload = UsagePayload(..., reasoning_tokens=reasoning_tokens)
  ```
- Confirm `_extract_message_from_choice` now carries reasoning through (it calls
  `_to_canonical_message`, so the fix above covers it).

### 4. Interning preserves reasoning + protect existing hashes
- `content_hash`/`intern_messages`/`rehydrate_messages` already operate on the full
  message dict (verified) — no code change needed there to *store* reasoning.
- **Required change to avoid hash churn:** switch the intern call sites to
  `m.model_dump(exclude_none=True)`:
  - `app/services/ingest.py` (`raw_messages = [...]`)
  - `app/proxy/plugins/logging.py` (`on_response` and `on_error`, both
    `raw_messages = [...]`)
  This keeps messages without reasoning hashing identically to existing rows while
  letting reasoning-bearing messages dedup correctly.
- Add a regression test asserting a plain message's `content_hash` is unchanged
  after the schema additions.

### 5. Usage token storage (`reasoning_tokens`) — column + migration + stats
Usage is stored as discrete int columns (confirmed), so:
- `backend/app/models/log_entry.py`: add
  `reasoning_tokens: int = Field(default=0)` to `LogEntry`.
- **Alembic migration**: `make migration m="add reasoning_tokens to log_entries"`
  adding the column `NOT NULL DEFAULT 0` (server_default "0" so existing rows
  backfill). Apply with `make migrate`.
- `app/services/ingest.py`: set `reasoning_tokens=payload.usage.reasoning_tokens`
  when building the `LogEntry`.
- `app/schemas/log_entry.py`: add `reasoning_tokens: int` to `LogEntryPublic`
  (inherited by `LogEntryDetail`); also add to `DailyStats`/`ModelStats`/
  `StatsResponse` totals.
- `app/services/stats.py`: aggregate `reasoning_tokens` into the summary totals,
  by-day, and by-model rollups (decision: include in stats now).
- `app/routers/logs.py`: include `reasoning_tokens=e.reasoning_tokens` in
  `_to_detail`, and in the `CallDivider` build in the transcript path so per-call
  reasoning token counts show on dividers.

### 6. Frontend display
- `frontend/src/lib/api.ts`: add `reasoning?: string` and
  `reasoning_details?: unknown[]` to the message type(s); add
  `reasoning_tokens?: number` to usage type.
- `frontend/src/routes/conversation.tsx` and `log-detail.tsx`: render reasoning as a
  visually distinct, collapsible block (a muted "Reasoning / thinking" disclosure
  above the assistant content) so it's reviewable but not confused with the final
  answer. Reuse existing markdown rendering for the `reasoning` string.
- **Redacted/encrypted `reasoning_details` blocks:** render as a labeled
  placeholder (e.g. `[encrypted reasoning block]` / `[redacted]`) using the block's
  `type`/label — never dump the raw opaque blob. Plain-text reasoning blocks render
  normally.
- **Thinking-only turns:** ensure the transcript/detail still shows a turn that has
  reasoning but empty final `content` (paired with the logs.py guard relaxation in
  step 4b below).
- Show `reasoning_tokens` in the usage/cost breakdown wherever token counts appear
  (detail page + transcript call dividers + dashboard stats).

### 4b. Relax thinking-only guard (`backend/app/routers/logs.py`)
- `_response_to_transcript_message` currently returns `None` when response
  `content` is empty/None. Update it to also emit the turn when
  `message.reasoning` or `message.reasoning_details` is present, and add optional
  `reasoning` / `reasoning_details` to `TranscriptMessage`
  (`app/schemas/log_entry.py`) carried through here and in the trunk/branch
  builders that introduce request-side messages.

### 7. Docs
- Update `docs/proxy.md` and `docs/schemas.md` to document that reasoning is
  captured: `reasoning` + `reasoning_details` on assistant messages and
  `usage.reasoning_tokens`.
- Update `OVERVIEW.md` only if structure changes (likely just the assembler +
  mapper behavior note; minor).

## Tests

- **`backend/tests/proxy/`**
  - `StreamAssembler`: feed chunks containing `delta.reasoning` and
    `delta.reasoning_details` (multi-fragment) + content + tool_calls; assert
    `assemble()` produces a message with merged reasoning string and reasoning
    details, and that `usage.completion_tokens_details.reasoning_tokens` survives.
- **`backend/tests/unit/`** (mapper + messages + stats)
  - `_to_canonical_message` carries `reasoning` / `reasoning_details` through.
  - `map_to_log_entry`: non-stream response with reasoning → `response.message`
    has reasoning fields; `usage.reasoning_tokens` populated from
    `completion_tokens_details`.
  - Request messages with `reasoning_details` round-trip through interning.
  - **Hash stability:** `content_hash` of a plain (no-reasoning) message is
    unchanged after the schema additions, given `exclude_none=True` at intern sites.
  - **Dedup correctness:** two messages identical except for reasoning produce
    different hashes (no collision).
  - **Stats:** `get_stats` aggregates `reasoning_tokens` into totals/by-day/by-model.
- **`backend/tests/api/`**
  - End-to-end proxy log (mock upstream) with reasoning → fetch
    `GET /logs/{id}` (detail) and `GET /conversations/{id}/transcript`, assert
    reasoning + `reasoning_tokens` are present in the persisted/returned payload.
  - **Thinking-only turn:** response with reasoning but empty content still appears
    in the transcript.
  - Migration round-trips: existing rows get `reasoning_tokens=0`; new rows persist
    the value.
- **Frontend (`frontend/src/test/`)**
  - Conversation/log-detail render a reasoning disclosure when present and omit it
    when absent.
  - Redacted/encrypted `reasoning_details` block renders as a labeled placeholder,
    not a raw blob.
  - `reasoning_tokens` shown in usage breakdown.

## Verification steps

1. `make migration m="..."` (only if step 5 needs a column) + `make migrate`.
2. `make lint` (ruff + ty; eslint + prettier + tsc).
3. `make test` (pytest + vitest).
4. Manual: point a reasoning-capable client at the proxy (streaming), make a call,
   then confirm the reasoning block + reasoning tokens appear in the log detail and
   conversation transcript in the dashboard.
5. `make check` before declaring done.

## Decisions (all resolved — no open questions)

- **Reasoning affects dedup hashing:** YES. It's part of the message dict, so it's
  already in the hash. Use `model_dump(exclude_none=True)` at intern sites to avoid
  churning existing non-reasoning hashes.
- **`reasoning_tokens`:** dedicated column + Alembic migration + **stats
  aggregation now** (totals, by-day, by-model) + surfaced on detail/transcript.
- **Redacted/encrypted reasoning blocks:** store **verbatim**; frontend renders a
  **labeled placeholder**, not the raw blob.
- **Request-side reasoning:** captured too (both request and response messages
  carry reasoning through `_to_canonical_message`).
- **Thinking-only turns:** emitted in the transcript even when final content is
  empty (guard relaxed in logs.py).
