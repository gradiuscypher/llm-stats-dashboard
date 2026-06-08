# Proxy Interceptor Redesign — Request-Only Interception with Original→Final Diff Tracking

> Status: **Planned** (not yet implemented). Supersedes the plugin-hook model
> described in `PROXY_PLAN.md` for the message-transformation path. Keep
> `PROXY_PLAN.md` as historical context.

## Goal & locked decisions

Replace the response-side and per-chunk plugin hooks with a **request-only
interceptor** model. Plugins become **pure transforms** applied in order to the
messages flowing *client → LLM*. The final transformed payload is what's sent
over the wire **and** what becomes the canonical logged/interned content. The
**original** client content is tracked separately so the UI can render a
per-message visual diff behind a client-side toggle.

| Decision | Choice |
|---|---|
| Architecture | **Interceptor core, request-only.** Response interception removed entirely. |
| Diff storage | Store **full original + final content** per modified request message. |
| Canonical content | **Final on-the-wire content is canonical**; original tracked separately. |
| Response path | **Verbatim passthrough.** Logged response == upstream response, never modified. |
| Streaming | Live stream untouched (no chunk hooks). Assembled copy logged verbatim. |
| Plugins | `word_count` → request-only pure transform; `compression` → request-only pure transform; `logging` → built-in sink (not a transform). |
| Diff delivery | Embed original+final per message in transcript + log-detail responses. |
| UI toggle | Client-side view preference (Settings + per-conversation view). |

### Why this shape (rationale)

- The current hook system smears "what happens to a message" across the router,
  pipeline, each plugin, and the logging plugin. A pure transform chain
  (`messages_in → t1 → t2 → … → messages_wire → upstream`) makes the data flow
  linear and trivially reasonable: "what did the LLM receive?" is just the value
  after the last transform.
- Removing **response/chunk interception** eliminates the hardest, riskiest code
  (SSE per-chunk mutation, chunk-drop logic, logged-copy-differs-from-client-copy
  subtleties). 95% of intended features (e.g. compressing messages before they
  hit the LLM to save tokens) are request-side only.
- We keep the genuinely-correct-and-fiddly transport plumbing (upstream client,
  stream assembler, conversation identity, background-task logging, fail-open
  isolation) intact to minimize blast radius.

---

## Part 1 — Backend: the Interceptor core

### 1.1 New plugin interface (`backend/app/proxy/plugins/base.py` — rewrite)

Replace `BasePlugin`'s hook surface with a single pure transform:

```python
class RequestTransform:
    name: str
    def transform_request(self, messages: list[dict], ctx: "TransformContext") -> list[dict]:
        """Return a new (or mutated) message list. Pure w.r.t. proxy transport."""
        return messages
```

- `transform_request` receives the **current** message list and returns the next one.
- Plugins must **not** touch transport/response. No `on_response_sync`,
  `on_stream_chunk`, `on_response`, `on_error` on transform plugins.
- Provide a lightweight read-only `TransformContext` (model name, user id,
  request metadata) so transforms can make decisions without mutating proxy state.

### 1.2 Interceptor (`backend/app/proxy/interceptor.py` — new)

A single class that owns the transform chain and diff computation:

```python
class RequestInterceptor:
    def __init__(self, transforms: list[RequestTransform]): ...
    def run(self, messages: list[dict], ctx) -> "InterceptResult"
```

`run`:
1. Deep-copy the incoming messages as `original`.
2. Apply each transform in order, **snapshotting between every plugin** (so each
   diff is attributable to the exact plugin — per-plugin attribution at low cost).
3. After the chain, compute per-message diffs by index: for each index, if
   `original[i] != final[i]`, record a `MessageDiff(index, role,
   original_content, final_content, modified_by=[plugin names that changed it])`.
4. Handle added/removed messages: if a transform changes list length, record
   `added`/`removed` entries. v1: assume in-place mutation (common case);
   document index-alignment limitation in the module docstring.
5. Return `InterceptResult(final_messages, diffs, per_plugin_summaries)`.

**Fail-open semantics:** a transform that raises is logged and **skipped** (its
output discarded, previous messages carried forward) — never breaks the proxied
request. Preserves today's isolation guarantee.

### 1.3 Context changes (`backend/app/proxy/context.py`)

- **Remove** `original_response_body` (response is verbatim now).
- Keep `original_request_body` for convenience; authoritative diff data lives in
  a structured `request_diffs: list[MessageDiff]` field.
- Replace free-form `RecordedModification` with a structured `MessageDiff`
  dataclass:
  ```python
  @dataclass
  class MessageDiff:
      message_index: int
      role: str | None
      original_content: Any       # what the client sent
      final_content: Any          # what was sent to the LLM
      modified_by: list[str]      # plugin names, in order applied
      change_kind: str            # "modified" | "added" | "removed"
  ```
- Keep `record_modification` only as a thin back-compat shim if needed; prefer
  the interceptor populating `ctx.request_diffs` directly.

### 1.4 Pipeline removal / simplification (`backend/app/proxy/pipeline.py`)

- **Delete** `on_response_sync`, `on_stream_chunk`, and plugin-routed streaming.
- Recommended: router calls `RequestInterceptor` directly for the request path,
  then calls the logging sink directly (no `PluginPipeline` wrapper).
- Keep `StreamAssembler` usage in the router (still needed to assemble streamed
  responses for logging) but it no longer runs plugin chunk hooks.

### 1.5 Registry (`backend/app/proxy/registry.py`)

- `PLUGIN_REGISTRY` maps names → `RequestTransform` subclasses only
  (`compression`, `word_count`). **Remove `logging`** — it becomes a built-in sink.
- `resolve_pipeline` keeps its toggle-resolution logic (per-conv → global →
  default) but returns `list[RequestTransform]`. The `logging` sink is invoked
  separately by the router; remove it from `LOCKED_PLUGINS`/registry.
- Update `get_pipeline()` legacy singleton (used only by `/proxy/health`) to
  return transforms; health doesn't need logging.

### 1.6 Logging sink (`backend/app/proxy/plugins/logging.py` → `backend/app/proxy/logging_sink.py`)

- Convert from `BasePlugin` to a plain function/class
  `persist_log(ctx, response_body, db_session_factory)` called by the router
  after the response is obtained (non-stream: background task; stream: after
  assembly).
- **Intern the FINAL request messages** (`ctx.request_body["messages"]`
  post-interceptor) as canonical — behavior change per "final is canonical".
- Intern the response message **verbatim** from upstream.
- Persist the structured `request_diffs` (Part 2 table) linked to the new
  `LogEntry`.
- Keep an error path `persist_error_log(ctx, error, ...)`; still persist request
  diffs (request-side transforms ran before the upstream failure).

### 1.7 Router (`backend/app/routers/proxy.py`)

- `_build_context`: keep parsing + size guard. Snapshot original request
  messages. **Drop** `original_response_body` snapshotting.
- `_handle_non_stream`:
  1. `RequestInterceptor.run(messages)` → write final messages back into
     `ctx.request_body["messages"]`, store `ctx.request_diffs`.
  2. `forward_non_stream` with the **final** body.
  3. Set response fields. **No `on_response_sync`.**
  4. Background task → `persist_log(...)`.
- `_handle_stream`:
  1. Run interceptor before forwarding.
  2. Forward stream **straight through** to client — no per-chunk plugin loop, no
     chunk-drop logic. Still feed `StreamAssembler` for logging.
  3. After stream completes: `persist_log(...)` with the assembled (verbatim)
     response.
- **Conversation-identity inference** (`infer_conversation_id`) stays as-is but
  must run against the **ORIGINAL client messages** so identity is stable
  regardless of which transforms are enabled (compression must not fork
  identity). Document this explicitly.

---

## Part 2 — Backend: persistence & schema

### 2.1 New model `MessageDiff` (`backend/app/models/message_diff.py` — new)

```python
class MessageDiff(SQLModel, table=True):
    __tablename__ = "message_diffs"
    id: uuid.UUID (pk)
    log_entry_id: uuid.UUID (fk log_entries.id, indexed)
    user_id: uuid.UUID (fk users.id)
    message_index: int
    role: str | None
    change_kind: str            # modified | added | removed
    original_content: dict      # JSONB — full message object client sent
    final_content: dict         # JSONB — full message object sent to LLM
    modified_by: list[str]      # JSONB array of plugin names
    created_at: datetime
    # indexes: (user_id, created_at), (log_entry_id)
```

**Migration approach:** add `message_diffs` as the new source of truth;
**deprecate `message_modifications`** (leave table + old rows in place, stop
writing to it). The transcript/log-detail read path reads from `message_diffs`.
Avoids a destructive migration. Document in OVERVIEW.

### 2.2 Alembic migration

- `make migration m="add message_diffs table for request interception diffs"`.
- Creates `message_diffs` + indexes. Does **not** drop `message_modifications`.

### 2.3 Service `backend/app/services/diffs.py` (new, replaces `modifications.py` usage)

- `persist_diffs(diffs, log_entry_id, user_id, db)` — writes rows.
- `batch_fetch_diffs(entry_ids, db) -> dict[entry_id, list[MessageDiff]]` for
  transcript/list endpoints.
- Remove `modifications.py` unless still referenced by old tests.

### 2.4 Schemas (`backend/app/schemas/log_entry.py`)

- Add:
  ```python
  class MessageDiffPublic(BaseModel):
      id; message_index; role; change_kind
      original_content: Any
      final_content: Any
      modified_by: list[str]
      created_at
  ```
- `LogEntryDetail`: add `request_diffs: list[MessageDiffPublic] = []`. Remove
  `modifications` field (update frontend together).
- `LogEntryPublic`: rename `modification_count` → `diff_count`.
- `TranscriptMessage`: add `original_content: Any | None = None` (populated only
  when modified; `content` already holds the final/canonical content). Keep
  `modified_by: list[str]`.
- `CallDivider`: replace `modifications`/`modification_count` with `diff_count:
  int` + `diffs: list[MessageDiffPublic]` (request-side only).

### 2.5 Read endpoints (`backend/app/routers/logs.py`)

- Single log detail: fetch `message_diffs` for the entry → `request_diffs`.
- Conversation transcript: batch-fetch diffs; for each transcript message with a
  diff, attach `original_content` and `modified_by`. Transcript `content` stays
  the **final/canonical** interned content. UI diff toggle swaps to / overlays
  `original_content`.
- Logs list: replace `modification_count` with `diff_count`.

---

## Part 3 — Plugins (convert to pure transforms)

### 3.1 `word_count` (`backend/app/proxy/plugins/word_count.py` — rewrite)

- Implement `transform_request(messages, ctx)`: find last `user` message, append
  `\n\n[word_count: N]` to its text, return the new list.
- **Remove** `on_response_sync` entirely.
- No `record_modification` calls — interceptor computes the diff automatically.
  Keep as the labeled **reference/sample** plugin.

### 3.2 `compression` (`backend/app/proxy/plugins/compression.py`)

- Convert the stub to a `RequestTransform` with `transform_request`. Keep it a
  stub (no-op or trivial whitespace collapse) but in the new shape — the working
  example of the primary use case (token-saving compression before the LLM).

### 3.3 Remove `LoggingPlugin` from plugin model

- Move logic to `logging_sink.py` (Part 1.6). Delete the class or leave a thin
  deprecation note.

---

## Part 4 — Frontend

### 4.1 API types (`frontend/src/lib/api.ts`)

- Replace `ModificationPublic` with `MessageDiffPublic { id, message_index,
  role, change_kind, original_content, final_content, modified_by, created_at }`.
- `LogEntryPublic`: `modification_count` → `diff_count`.
- `LogEntryDetail`: `modifications` → `request_diffs`.
- `TranscriptMessage`: add `original_content?: string | unknown[] | null`; keep
  `modified_by`.
- `CallDivider`: `modification_count`/`modifications` → `diff_count`/`diffs`.

### 4.2 View toggle (client-side preference)

- New hook `frontend/src/lib/useShowDiff.ts` (localStorage-backed, mirrors
  `useFontSize`/`useTheme`).
- Settings page (`frontend/src/routes/settings.tsx`): add "Show request diffs in
  transcripts" toggle.
- Conversation page (`frontend/src/routes/conversation.tsx`): add a quick
  per-view toggle reading/writing the same preference.

### 4.3 Diff rendering

- New component `frontend/src/components/MessageDiff.tsx`: given
  `original_content` + final `content`, render an inline visual diff. Default:
  a minimal word/line diff implemented in-repo (avoid a new dependency; confirm
  during impl).
- In `conversation.tsx`: when toggle is on and a `TranscriptMessage` has
  `original_content`, render `<MessageDiff original={...} final={content} />`
  instead of plain content; otherwise render final content. Always show the
  `modified by` label (`ModifiedByLabel`) using `modified_by`.
- Update `ModificationBadge` → reflect "diff"/count; keep visual style.

### 4.4 Log detail page (`frontend/src/routes/log-detail.tsx`)

- Render `request_diffs` (original vs final per message) behind the same toggle.

---

## Part 5 — Tests

### Backend (`backend/tests/proxy/`)
- **New** `test_interceptor.py`: transform ordering, between-plugin snapshots,
  per-plugin attribution, modified/added/removed diff computation, fail-open on a
  throwing transform.
- Update `test_proxy_api.py`: request-only transforms; final messages sent
  upstream == post-transform; logged/interned request == final; response logged
  verbatim; **no** response mutation; streaming passes through untouched.
- Update `test_registry_resolution.py`: registry no longer contains `logging`;
  transforms resolve with toggles.
- **New** `test_diffs_service.py`: `persist_diffs` / `batch_fetch_diffs`.
- Update/replace `test_proxy_unit.py` modification assertions → diff assertions.
- Conversation-identity test: identity computed from **original** messages,
  stable when compression enabled/disabled.

### Frontend (`frontend/src/test/`)
- `MessageDiff` renders add/remove/modify.
- Toggle hook persists; conversation view swaps content when on.

---

## Part 6 — Docs

- `docs/proxy.md`: rewrite the plugin section — request-only interceptor,
  pure-transform plugins, "final is canonical, original tracked for diffs,"
  response verbatim, streaming untouched.
- `OVERVIEW.md`: update §3.6 (proxy subsystem table), §3.2 (new `MessageDiff`
  model, deprecate `MessageModification`), §3.4 (services), §5 flows.
- Any AI-facing API doc that referenced `modifications` → `request_diffs`.

---

## Implementation order (suggested)

1. Model + migration (`MessageDiff`) and `diffs.py` service.
2. New `base.py` transform interface + `interceptor.py` + tests in isolation.
3. Convert `word_count` + `compression` to transforms; update registry.
4. Convert logging to `logging_sink.py`; intern final request content.
5. Wire router (`proxy.py`) to interceptor + sink; drop response/chunk hooks.
6. Read endpoints (`logs.py`) + schemas → emit `request_diffs` / `original_content`.
7. Frontend types, toggle hook, `MessageDiff` component, conversation +
   log-detail wiring.
8. Tests (backend + frontend), docs, `OVERVIEW.md`.
9. `make check`.

---

## Open risks / notes to revisit during implementation

- **Added/removed message pairing** in the diff is the only genuinely tricky bit.
  v1: assume transforms mutate in place (common case); handle length changes with
  simple index alignment and a documented limitation. Most real plugins
  (compression, word_count) mutate in place.
- **Conversation identity from original messages** is deliberate — keeps threads
  stable across transform toggles. Call it out in `conversation_identity` and
  test it.
- **`message_modifications` deprecation**: leave table + rows; stop writing. If a
  full data migration into `message_diffs` is wanted, that's an extra step — flag
  before doing it.
- **No new frontend dependency** for diffing unless preferred; default is a small
  in-repo word/line differ.
