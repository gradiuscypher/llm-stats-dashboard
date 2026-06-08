# Plugin Toggle, Modification Logging & WordCount Plugin — Plan

> Build on the existing proxy/plugin pipeline (`backend/app/proxy/`, see
> `plans/PROXY_PLAN.md`) to deliver three user-facing capabilities:
>
> 1. **Toggle plugins from the dashboard** — globally per-user (applies to all
>    future conversations) and per-conversation (overrides the global setting
>    for future calls in that conversation).
> 2. **Explicit modification logging** — every change a plugin makes to an LLM
>    message (request → provider, or response ← provider) is recorded in a
>    dedicated table and surfaced in the logs and conversation views with a
>    badge / color highlight.
> 3. **WordCountPlugin** — a simple first real plugin that appends a word-count
>    marker line to messages sent to *and* received from the provider, exercising
>    both the modification-logging and toggle machinery end to end.

Status: **planning**. Companion to `PROXY_PLAN.md` (the proxy + pipeline design,
already implemented). This document covers only the toggle layer, modification
logging, and the WordCount plugin.

---

## 0. Decisions (locked from clarifying Q&A)

| Area | Decision |
|------|----------|
| Toggle storage | New DB table(s): **per-user global** state **+ per-conversation overrides**. Requires an Alembic migration. |
| Per-conversation semantics | A per-conversation override applies to **future calls** in that conversation. When a request resolves to a known `conversation_id` (explicit `X-Conversation-Id` header, or derived via the existing prefix-chain logic), the per-conversation override wins over the user-global setting. |
| Modification logging | **Dedicated table** `message_modifications` linked to `log_entries`, with new read endpoints. Surfaced via badges in logs + conversation views. |
| WordCount injection | **Append a marker line to the message text content** — e.g. `\n\n[word_count: 42]` on the last user message (request side) and on the assistant message (response side). Visibly changes content; recorded as a modification. |

---

## 1. Goals & non-goals

**Goals**
- Per-user, per-plugin enable/disable controlled from the dashboard, taking
  effect for all future conversations.
- Per-conversation overrides, editable from the conversation page, affecting
  future calls in that conversation.
- Every plugin modification is explicitly persisted and visibly highlighted in
  the UI (logs table, log detail, conversation transcript).
- A working `WordCountPlugin` that demonstrates the full loop.

**Non-goals (this effort)**
- Per-API-key plugin config (still future, per `PROXY_PLAN.md §11`).
- Reordering plugins from the UI (order stays env-driven via `PROXY_PLUGINS`;
  toggles only enable/disable within that order).
- Undo/replay of modifications.
- Real compression (the existing stub stays a stub).

---

## 2. Data model changes

Three new tables. All require **one** Alembic migration
(`make migration m="plugin toggles and modification logging"`).

### 2.1 `plugin_config` — per-user global plugin state

`backend/app/models/plugin_config.py`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK → `users.id`, indexed | |
| `plugin_name` | str(64) | matches a key in `PLUGIN_REGISTRY` |
| `enabled` | bool, default `True` | per-user global on/off |
| `created_at` / `updated_at` | datetime | |

Unique constraint: `(user_id, plugin_name)`.

Semantics: a missing row means "use the plugin's default" (see §4.2 for what
"default" means relative to `PROXY_PLUGINS`).

### 2.2 `plugin_config_conversation` — per-conversation overrides

`backend/app/models/plugin_config.py` (same module)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK → `users.id`, indexed | |
| `conversation_id` | str(256), indexed | |
| `plugin_name` | str(64) | |
| `enabled` | bool | override value |
| `created_at` / `updated_at` | datetime | |

Unique constraint: `(user_id, conversation_id, plugin_name)`.

Semantics: a row here **overrides** the user-global value for that conversation.
No row → fall back to user-global → fall back to default.

### 2.3 `message_modifications` — recorded plugin modifications

`backend/app/models/message_modification.py`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `log_entry_id` | UUID FK → `log_entries.id`, indexed | the call this modification belongs to |
| `user_id` | UUID FK → `users.id`, indexed | denormalized for cheap filtering/auth |
| `plugin_name` | str(64) | which plugin made the change |
| `target` | str(16) | `"request"` (to provider) or `"response"` (from provider) |
| `message_index` | int \| null | which message in the array was changed (null = applies to whole response/request) |
| `message_role` | str(32) \| null | role of the affected message (`user`, `assistant`, …) |
| `summary` | str(256) | short human label, e.g. `"appended word_count: 42"` |
| `detail` | JSONB | structured payload: `{ "before": "...", "after": "...", "added": "...", "plugin_state": {...} }` |
| `created_at` | datetime | |

Index: `ix_message_modifications_user_created (user_id, created_at)` and
`ix_message_modifications_entry (log_entry_id)`.

> Why a table instead of `metadata_extra`: clean per-modification rows make the
> badge counts, filtering ("show calls with modifications"), and future auditing
> straightforward, and keep `LogEntry.metadata_extra` reserved for passthrough
> client metadata.

---

## 3. Plumbing modifications through the pipeline

### 3.1 ProxyContext — record modifications as they happen

`backend/app/proxy/context.py`

Add a structured list plus a helper so plugins record changes uniformly:

```python
@dataclass
class RecordedModification:
    plugin_name: str
    target: str               # "request" | "response"
    message_index: int | None
    message_role: str | None
    summary: str
    detail: dict[str, Any]

@dataclass
class ProxyContext:
    ...
    modifications: list[RecordedModification] = field(default_factory=list)

    def record_modification(
        self, *, plugin_name, target, summary, detail,
        message_index=None, message_role=None,
    ) -> None:
        self.modifications.append(RecordedModification(...))
```

Plugins call `ctx.record_modification(...)` whenever they mutate request or
response data. The pipeline owns nothing extra here — the modifications travel
with the context.

### 3.2 LoggingPlugin — persist modifications alongside the LogEntry

`backend/app/proxy/plugins/logging.py`

After `ingest_log_entry(...)` returns the persisted `LogEntry`, write the
recorded modifications:

- `ingest_log_entry` already returns the `LogEntry` (with `.id`). Capture it.
- Insert one `MessageModification` row per `ctx.modifications` entry, linked to
  `entry.id` and `ctx.user.id`, in the **same** `Session`/transaction.
- Do this in both `on_response` and `on_error` paths (errors may still carry
  request-side modifications).
- Keep the existing best-effort isolation (`try/except` + log) so a modification
  write never breaks the proxied request.

No change to `openrouter_map` or `ingest_log_entry` signatures is required;
modification persistence is a new step layered in the logging plugin. (Optional
cleanliness: add a small `services/modifications.py::record_modifications(entries, log_entry_id, user_id, db)`
helper and unit-test it in isolation.)

### 3.3 Response-side modification & what the client sees

The WordCount "response" modification appends to the assistant message. Decision
for MVP, consistent with the proxy's "client always sees an OpenRouter-shaped
response" principle:

- **Response-side modifications mutate the copy that is logged AND returned to
  the client**, since the whole point of this sample plugin is to show a change
  flowing in both directions. For non-stream this is straightforward (mutate
  `ctx.response_body["choices"][0]["message"]["content"]` in `on_response`
  before the router returns it).
- For **streaming**, mutating mid-stream content is fragile. MVP rule: WordCount
  applies its response-side modification only on the **non-stream** path and on
  the **assembled** response used for logging; for streamed calls it records the
  modification against the logged/assembled copy only (client stream unchanged).
  Document this clearly. (The router already assembles the full response at
  stream end and calls `on_response` — so logging the response-side mod works
  for both; only the *client-visible* injection differs.)

> Ordering note: the non-stream router currently runs `on_response` as a
> fire-and-forget **background task** *after* `JSONResponse` is built. To let a
> plugin mutate the client-visible body, WordCount's response injection must run
> **before** the response is serialized. Plan: split into two phases —
> `on_response_sync` (mutating, runs inline before returning) vs `on_response`
> (logging side-effects, stays in the background task). See §3.4.

### 3.4 Pipeline: synchronous vs deferred response hooks

`backend/app/proxy/pipeline.py` + `plugins/base.py`

Add a new optional hook so mutators can touch the client-visible response inline
while loggers stay deferred:

- `BasePlugin.on_response_sync(ctx) -> None` — default no-op. Runs **inline**,
  before the router serializes/returns the body. Mutators (WordCount response
  side) implement this.
- `BasePlugin.on_response(ctx)` — unchanged; loggers (LoggingPlugin) implement
  this; still run as a background task / at stream end.
- `PluginPipeline.on_response_sync(ctx)` mirrors `on_response` ordering but, like
  mutators on request, surfaces errors per the plugin's fail-open contract
  (default: log + continue, never break the client response).

Router wiring (`backend/app/routers/proxy.py`):
- Non-stream: `await pipeline.on_response_sync(ctx)` immediately after populating
  `ctx.response_body`, **before** `JSONResponse(content=ctx.response_body)`; keep
  the existing background `on_response` for logging.
- Stream: call `on_response_sync` on the assembled body before `on_response`
  (client stream already sent; this only affects the logged copy — acceptable
  and documented).

---

## 4. Toggle resolution — building the per-request pipeline

### 4.1 Replace the global singleton with a resolver

`backend/app/proxy/registry.py`

Today `get_pipeline()` returns a process-wide singleton built from
`PROXY_PLUGINS`. Add a DB-aware resolver (the call site change anticipated in
`PROXY_PLAN.md §4.5`):

```python
def resolve_pipeline(
    user_id: uuid.UUID,
    conversation_id: str | None,
    db: Session,
) -> list[BasePlugin]:
    """Build the ordered plugin list for this user/conversation.

    Order is still defined by PROXY_PLUGINS (and PLUGIN_REGISTRY). A plugin is
    included iff it resolves to enabled for (user, conversation):
      per-conversation override  →  user-global  →  default.
    """
```

- The **order** of plugins remains `PROXY_PLUGINS` (env). Toggles only filter.
- **Default** when no `plugin_config` row exists: a plugin is enabled by default
  iff its name appears in `PROXY_PLUGINS`. (So out of the box, behavior is
  unchanged: `logging` on, others off, until the user flips them.)
- `logging` should be treated as **always-on / not user-disableable** at the
  resolver level (or at least UI-locked) so users can't silently turn off all
  logging. Decision: keep `logging` toggleable in the table for symmetry but
  hard-pin it enabled in `resolve_pipeline` and grey it out in the UI. Document
  this.

### 4.2 Conversation id is known late — two-phase resolution

The proxy derives `conversation_id` *after* the call (in LoggingPlugin). But
per-conversation toggles must apply to the *request*. Resolution strategy:

1. **Pre-call**: determine a *candidate* conversation id using the same cheap,
   deterministic inputs available before forwarding:
   - explicit `X-Conversation-Id` header → use it directly; OR
   - the prefix-derivation seed (system + first user message hash + api-key
     salt) computed via the existing `derive_conversation_id` logic, **without**
     the DB prefix-chain step (that step needs interned messages). Factor the
     header/seed portion of `derive_conversation_id` into a reusable
     `candidate_conversation_id(request_body, api_key_prefix, explicit_header)`
     in `services/openrouter_map.py`.
2. Build the pipeline with `resolve_pipeline(user_id, candidate_conv_id, db)` in
   the router **before** `on_request`.
3. Logging still derives the final, authoritative `conversation_id` as today.

Edge case (documented): if the pre-call candidate id differs from the final
derived id (e.g. mid-thread system-prompt rewrite), the override may not match.
This is the same class of fuzziness already accepted in `PROXY_PLAN.md §5.2`;
the explicit `X-Conversation-Id` header is the precise opt-in. For the common
case (client sends the header, or a stable prefix), it works deterministically.

### 4.3 Router wiring

`backend/app/routers/proxy.py`

- `_build_pipeline()` becomes `_build_pipeline(user_id, conversation_id, db)` and
  calls `resolve_pipeline(...)`. It needs a DB session — inject `get_session`
  into the proxy route handlers (currently they don't take one) or open a
  short-lived `Session(engine)` for resolution.
- Compute the candidate conversation id from `ctx` right after `_build_context`,
  then build the pipeline.

---

## 5. WordCountPlugin (the sample plugin)

`backend/app/proxy/plugins/word_count.py`, registered as `"word_count"` in
`PLUGIN_REGISTRY`.

Behavior:
- `on_request(ctx)`:
  - Find the **last `user` message** in `ctx.request_body["messages"]`.
  - Count words in its text content (`len(text.split())`; handle list/multimodal
    content by counting words across text parts).
  - Append `\n\n[word_count: N]` to that message's text content.
  - `ctx.record_modification(plugin_name="word_count", target="request",
    message_index=i, message_role="user", summary=f"appended word_count: {N}",
    detail={"added": f"[word_count: {N}]", "count": N})`.
  - Fail-open: any exception → log + leave body unchanged (no modification
    recorded).
- `on_response_sync(ctx)`:
  - Take `ctx.response_body["choices"][0]["message"]["content"]`.
  - Count words, append `\n\n[word_count: N]`.
  - Record a `target="response"` modification (message_index=0, role=assistant).
- It does **not** implement `on_stream_chunk` (response injection is non-stream
  only for MVP; streamed calls still get the request-side injection + a logged
  response-side modification on the assembled body).

Config: add `word_count` to `PLUGIN_REGISTRY`. It is **not** added to the default
`PROXY_PLUGINS` env (so it's off by default); users enable it via the dashboard.
Update `.env.example` to mention it as an available plugin name.

---

## 6. Backend API surface

New endpoints (REST, under `/api/v1`, session-or-`logs:read`-key auth like the
rest of the dashboard reads; mutations require CSRF + session, consistent with
existing patterns).

### 6.1 Plugin metadata + global config

New router `backend/app/routers/plugins.py` (prefix `/plugins`):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/plugins` | List available plugins (name, description, `default_enabled`, `locked` for `logging`) merged with the caller's per-user `enabled` state. |
| `PUT` | `/plugins/{name}` | Set per-user global `enabled` (upsert `plugin_config`). CSRF + session. 400 on unknown plugin; 409/locked on `logging`. |

Plugin descriptions come from a static registry map (extend `PLUGIN_REGISTRY`
values or a parallel `PLUGIN_META` dict with `description`).

### 6.2 Per-conversation overrides

Add to the existing logs/conversations router (`backend/app/routers/logs.py`)
or the new plugins router:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/conversations/{id}/plugins` | Effective state per plugin for this conversation: `{ name, global_enabled, override: bool\|null, effective: bool }`. |
| `PUT` | `/conversations/{id}/plugins/{name}` | Upsert a per-conversation override (`enabled`). CSRF + session. |
| `DELETE` | `/conversations/{id}/plugins/{name}` | Remove the override (revert to global). |

### 6.3 Surfacing modifications in reads

- **Transcript** (`GET /conversations/{id}/transcript`): extend `CallDivider`
  with `modification_count: int` and `modifications: list[ModificationPublic]`
  (or at least a count + a flag). Populate by querying `message_modifications`
  for the entries in the conversation (batch by `log_entry_id`). Also annotate
  `TranscriptMessage` with an optional `modified_by: list[str]` (plugin names)
  when a modification targets that message index/role for its introducing entry.
- **Log detail** (`GET /logs/{id}`): extend `LogEntryDetail` with
  `modifications: list[ModificationPublic]`.
- **Logs list** (`GET /logs`): extend `LogEntryPublic` with
  `modification_count: int` (cheap aggregate `COUNT(*)` grouped by
  `log_entry_id`) so the table can show a badge without N+1 queries.

New schema `ModificationPublic` in `backend/app/schemas/log_entry.py`:
`{ id, plugin_name, target, message_index, message_role, summary, detail, created_at }`.

---

## 7. Frontend changes

All HTTP through `frontend/src/lib/api.ts`. New API group `pluginsApi`
(`list`, `setGlobal`, `getConversationPlugins`, `setConversationOverride`,
`deleteConversationOverride`) + new TS types (`PluginInfo`, `ModificationPublic`,
`ConversationPluginState`) and extensions to `LogEntryPublic`/`LogEntryDetail`/
`CallDivider`/`TranscriptMessage`.

### 7.1 Global plugin management (Settings)

`frontend/src/routes/settings.tsx` — add a **"Proxy Plugins"** section:
- List each plugin: name, description, a toggle (Switch/checkbox), `logging`
  shown locked/disabled with a tooltip ("always on").
- Toggling calls `pluginsApi.setGlobal(name, enabled)`; invalidate the plugins
  query. Add a short "applies to all future conversations" helper line.

(Alternatively a dedicated `/plugins` route + nav link in
`frontend/src/components/Layout.tsx`. MVP: put it under Settings to avoid a new
nav entry; note the route option.)

### 7.2 Per-conversation toggles (Conversation page)

`frontend/src/routes/conversation.tsx` — add a **plugins panel** near the summary
bar:
- Fetch `pluginsApi.getConversationPlugins(conversationId)`.
- For each plugin show: effective state, whether it's overriding the global, and
  a toggle. Toggling sets/removes the override; a "revert to global" affordance.
- Helper text: "Overrides apply to future calls in this conversation."

### 7.3 Modification badges / highlighting

- **Logs table** (`frontend/src/routes/logs.tsx`): when
  `modification_count > 0`, render a small badge (e.g. amber pill `✎ N`) in the
  row. Reuse the project's badge styling conventions (`StatusBadge` as a
  reference component).
- **Conversation transcript** (`conversation.tsx`):
  - In `DividerBar`, when the divider has modifications, add a colored badge
    (`✎ N`) with a tooltip listing `plugin_name → summary`.
  - In `MessageBubble`, when a message was modified (`modified_by` non-empty),
    add a left border / background tint (use a CSS var like
    `--color-accent` at low opacity) and a tiny `modified by word_count` label.
- **Log detail** (`frontend/src/routes/log-detail.tsx`): a "Modifications"
  section listing each modification (plugin, target request/response, summary,
  and an expandable before/after diff from `detail`).
- Add a new shared component `frontend/src/components/ModificationBadge.tsx` for
  consistency.

---

## 8. Docs

- Update `docs/proxy.md`: new "Plugins" section covering the available plugins,
  how toggles resolve (per-conversation → global → default), the always-on
  `logging` plugin, the `word_count` example, and the streaming caveat for
  response-side modifications.
- Add `docs/plugins.md` (linked from `docs/index.md`) documenting the toggle
  endpoints and modification records for AI/API clients.
- Update `OVERVIEW.md`: new models (`plugin_config`,
  `plugin_config_conversation`, `message_modifications`), new router
  (`plugins.py`), the `resolve_pipeline` resolver, and the WordCount plugin.

---

## 9. Testing plan

Backend (`pytest`, OpenRouter mocked via `respx`):

**Unit**
- `resolve_pipeline`: default (no rows) matches `PROXY_PLUGINS`; user-global
  disable removes a plugin; per-conversation override beats global; `logging`
  stays pinned even if a row disables it.
- `candidate_conversation_id`: deterministic; header beats derivation.
- `WordCountPlugin.on_request`: appends marker to last user message; correct
  count; records one modification; fail-open on malformed body.
- `WordCountPlugin.on_response_sync`: appends to assistant content; records a
  response modification.
- `record_modifications` service: writes N rows linked to the entry.

**API/integration**
- `PUT /plugins/{name}` then a proxied `POST /chat/completions` with WordCount
  enabled → response body contains `[word_count: N]`; a `LogEntry` exists with
  two `message_modifications` (request + response).
- WordCount **disabled** (default) → no marker, no modification rows.
- Per-conversation override: enable globally, disable for one conversation via
  `PUT /conversations/{id}/plugins/word_count` + `X-Conversation-Id` header →
  that conversation's next call is unmodified; others still modified.
- `GET /conversations/{id}/transcript` returns dividers with
  `modification_count > 0` and messages flagged `modified_by`.
- `GET /logs` rows carry `modification_count`; `GET /logs/{id}` returns the
  `modifications` array.
- Auth/CSRF on the mutating plugin endpoints.

Frontend (`vitest` + Testing Library):
- Settings plugins section renders, toggle calls the API, `logging` locked.
- Conversation plugins panel reflects override vs global.
- Logs row + transcript divider render the modification badge when
  `modification_count > 0`.

`make check` green before declaring done.

---

## 10. Build order (milestones)

1. **Migration + models**: `plugin_config`, `plugin_config_conversation`,
   `message_modification`; `make migration` + `make migrate`. Add
   `ModificationPublic` schema.
2. **Context + pipeline**: `RecordedModification`, `ctx.record_modification`,
   `on_response_sync` hook + pipeline method; router wiring for the sync phase.
3. **Resolver**: `resolve_pipeline` + `candidate_conversation_id`; switch the
   router from the singleton to per-request resolution.
4. **WordCountPlugin**: implement + register; `.env.example` note. Tests.
5. **Modification persistence**: LoggingPlugin writes `message_modifications`;
   `services/modifications.py` helper. Tests.
6. **Read surfacing**: extend transcript/log-detail/log-list schemas + queries
   with modification data. Tests.
7. **Plugin config API**: `routers/plugins.py` (global) + conversation override
   endpoints. Tests.
8. **Frontend toggles**: `pluginsApi` + types; Settings section; conversation
   plugins panel. Tests.
9. **Frontend badges**: `ModificationBadge`; logs table, transcript divider +
   message highlight, log-detail modifications section. Tests.
10. **Docs**: `docs/proxy.md`, `docs/plugins.md`, `OVERVIEW.md`. `make check`.

---

## 11. Open considerations / future

- Move plugin management to its own `/plugins` nav route if the Settings page
  gets crowded.
- Per-API-key plugin config (already noted in `PROXY_PLAN.md §11`).
- Streaming response-side mutation (would require chunk-level rewriting in
  `on_stream_chunk`).
- Diff visualization improvements (inline word-level diff) for modifications.
- A "modifications" filter on the logs list (`?has_modifications=true`).
