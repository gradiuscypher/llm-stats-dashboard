# LLM Proxy & Plugin Architecture — Plan

> Evolve the dashboard from a **push-based logging API** (clients call
> `POST /logs`) into an **LLM proxy** that sits transparently in front of the
> OpenRouter API. Sitting in the request path lets us log exactly what we want
> *and* mutate requests/responses on the fly. Each behaviour (logging, token
> compression, …) is an independently-toggleable **plugin** in a pipeline.

Status: **planning** (no implementation yet). Companion to `PLAN.md`, which
remains the source of truth for the dashboard/auth/UI. This document covers only
the proxy + plugin layer.

---

## 0. Decisions (locked from clarifying Q&A)

| Area | Decision |
|------|----------|
| Tech stack | Unchanged: Python 3.12 + FastAPI + SQLModel + Postgres + Alembic; `uv`/`ruff`/`ty`; existing frontend |
| Upstream target | **OpenRouter** (`https://openrouter.ai/api/v1`) |
| Proxy mode | **Transparent passthrough** — mirror OpenRouter's paths so clients are drop-in (just change `base_url` + key) |
| Auth in | Client sends **our** `lsd_` API key (new scope `proxy:use`) |
| Upstream key | Server holds a **single** OpenRouter key from config and injects it |
| Streaming | **Required** — SSE must flow through; plugins see chunks; logging assembles the full response |
| Logging | **Reuse** existing `LogEntry`/message-interning/transcript model; logging plugin maps OpenRouter ⇄ canonical schema and calls `ingest_log_entry` |
| Plugin hooks | **Full set**: `on_request`, `on_response`, `on_stream_chunk`, `on_error` |
| Compression plugin | **Interface stub only** in MVP (example plugin, no algorithm) |
| Plugin config | **Global** via app config/env for MVP; registry designed so per-user / per-key can be layered later |
| HTTP client | **httpx** (async, native streaming). Also: remove the bogus `httpx2` dependency |
| `POST /logs` | **Kept** — proxy becomes a *second* writer into the same tables; manual ingest still works |

---

## 1. Goals & non-goals

**Goals**
- Drop-in OpenRouter proxy: point an existing OpenRouter client at our base URL,
  swap the key, and it Just Works — with full logging for free.
- A clean plugin pipeline where logging and request-mutation are separate,
  ordered, individually toggleable plugins.
- First-class streaming support (SSE) without forcing plugins to all be
  stream-aware.
- Zero changes required to the existing dashboard read path / transcript views —
  the proxy feeds the same `LogEntry` tables.

**Non-goals (MVP)**
- Per-user / per-key plugin configuration UI (design for it, don't build it).
- A concrete compression algorithm (interface only).
- Providers other than OpenRouter (the upstream client is abstracted enough to
  add more later, but we target OpenRouter now).
- Caching / response replay, prompt firewalling, multi-key load balancing
  (listed in §11 future).

---

## 2. High-level request flow

```
client (OpenAI/OpenRouter SDK)
   │   POST /api/v1/chat/completions   Authorization: Bearer lsd_xxx
   ▼
┌────────────────────────────────────────────────────────────────────┐
│ FastAPI proxy router                                                 │
│  1. authenticate lsd_ key  → resolve User + scope proxy:use          │
│  2. build ProxyContext (user, model, body, headers, is_stream, …)    │
│  3. PluginPipeline.on_request(ctx)      ── plugins may mutate body ── │
│  4. inject server OpenRouter key, forward via httpx to OpenRouter    │
│     ├─ non-stream: await full JSON                                   │
│     │     PluginPipeline.on_response(ctx)   (logging maps→LogEntry)  │
│     │     return JSON to client                                      │
│     └─ stream (SSE): iterate upstream chunks                         │
│           PluginPipeline.on_stream_chunk(ctx, chunk) per delta       │
│           relay chunk to client immediately                          │
│           on stream end: assemble full response →                    │
│              PluginPipeline.on_response(ctx)  (logging)              │
│  5. on any upstream/transport error:                                 │
│        PluginPipeline.on_error(ctx, err) then surface error          │
└────────────────────────────────────────────────────────────────────┘
   │  (streamed or buffered) OpenRouter-shaped response
   ▼
client
```

Key property: **the client always sees an OpenRouter-shaped response**, byte-for
-byte for streaming where possible. Logging and mutation are side-effects.

---

## 3. Endpoints (transparent passthrough)

Mounted under the existing `/api/v1` prefix so they share middleware/CORS, but
shaped to mirror OpenRouter's surface. A client sets
`base_url = https://<our-host>/api/v1`.

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/v1/chat/completions` | Primary. Supports `stream: true/false`. The core proxied call. |
| `POST` | `/api/v1/completions` | Legacy text completions (proxy + log; optional in MVP). |
| `GET`  | `/api/v1/models` | Proxy OpenRouter's model list (handy for clients; no logging). |
| `GET`  | `/api/v1/proxy/health` | Proxy-specific readiness (upstream reachable, key configured). |

Auth: `Authorization: Bearer lsd_...` **and/or** `X-API-Key: lsd_...`. OpenAI
-style SDKs send `Authorization: Bearer`, so the proxy auth dependency must
accept the bearer header in addition to the existing `X-API-Key`
(see §7). Required scope: **`proxy:use`**.

> Path collision note: the dashboard's own API also lives under `/api/v1`
> (`/api/v1/logs`, `/api/v1/auth/...`). OpenRouter's `chat/completions`,
> `completions`, `models` don't collide with existing dashboard routes, so
> transparent mounting is safe. The proxy router is registered last and only
> claims those specific paths.

---

## 4. Plugin architecture

### 4.1 Core types

```python
# app/proxy/context.py
@dataclass
class ProxyContext:
    user: User
    api_key: ApiKey
    # request
    request_body: dict          # parsed JSON body (mutable by plugins)
    request_headers: dict
    model: str
    is_stream: bool
    started_at: float
    # response (populated as we go)
    response_body: dict | None = None      # assembled (non-stream or end-of-stream)
    response_headers: dict | None = None
    status_code: int | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    error: Exception | None = None
    # scratch space for inter-plugin state, keyed by plugin name
    state: dict[str, Any] = field(default_factory=dict)
    # plugins may set this to short-circuit (e.g. cache hit) — future
    short_circuit_response: dict | None = None
```

### 4.2 Plugin protocol

```python
# app/proxy/plugins/base.py
class ProxyPlugin(Protocol):
    name: str

    async def on_request(self, ctx: ProxyContext) -> None: ...
        # Inspect / mutate ctx.request_body before forwarding.

    async def on_stream_chunk(self, ctx: ProxyContext, chunk: dict) -> dict | None: ...
        # Called per SSE delta. Return a (possibly mutated) chunk to relay,
        # or None to drop it. Default impl returns chunk unchanged.

    async def on_response(self, ctx: ProxyContext) -> None: ...
        # Called once with the fully-assembled response (ctx.response_body,
        # ctx.usage, ctx.finish_reason). This is where logging happens.

    async def on_error(self, ctx: ProxyContext, error: Exception) -> None: ...
        # Called if the upstream call or stream fails.
```

All four hooks have no-op defaults via a `BasePlugin` ABC so a plugin only
implements what it needs (e.g. the logging plugin only implements
`on_response`/`on_error`; a mutation plugin only `on_request`).

### 4.3 Pipeline semantics

- `on_request`: run **in registration order**. Each plugin may mutate
  `ctx.request_body`. Later plugins see earlier mutations.
- `on_stream_chunk`: run in order, chained — chunk passes through each plugin;
  a `None` return drops the chunk from the relayed stream. Errors in a chunk
  hook are isolated (logged, chunk relayed unchanged) so a buggy plugin can't
  break the client stream.
- `on_response`: run in order, on the assembled response. Side-effect oriented
  (logging). Exceptions are caught & logged per-plugin (a logging failure must
  never fail the user's request).
- `on_error`: run in order; best-effort.
- **Isolation principle**: observer plugins (logging) must never break the
  proxied request. Mutator plugins (compression) run on `on_request` where a
  failure *can* legitimately abort the call (configurable: fail-open vs
  fail-closed per plugin, default fail-open = skip plugin on error).

### 4.4 Streaming assembly

Streaming complicates `on_response` because logging wants the *whole* answer.
The pipeline owns a small **StreamAssembler** that accumulates choice deltas,
tool-call fragments, and the terminal `usage` object (OpenRouter sends usage in
the final SSE chunk when `stream_options.include_usage` / its default behaviour
applies). At `[DONE]` the assembler produces a synthetic non-stream-shaped
response dict, which is placed in `ctx.response_body` and passed to
`on_response`. This means **logging is identical for stream and non-stream**
paths — plugins that only care about the final answer never touch
`on_stream_chunk`.

### 4.5 Registry & config (global for MVP)

```python
# app/proxy/registry.py
PLUGIN_REGISTRY: dict[str, type[ProxyPlugin]] = {
    "logging": LoggingPlugin,
    "compression": CompressionPlugin,   # stub
    # "ratelimit": ..., "cache": ...  (future)
}
```

Config (env): `PROXY_PLUGINS=logging` — ordered, comma-separated list of plugin
names to enable. Pipeline is built once at startup from this list.

Designed-for-later (not built in MVP): swap the global list for a
`resolve_pipeline(user, api_key)` function backed by a `proxy_plugin_config`
table so the registry can produce a per-user/per-key pipeline. The pipeline
construction call site is the only thing that changes.

---

## 5. Plugins shipped in MVP

### 5.1 LoggingPlugin (`logging`)
- Implements `on_response` and `on_error`.
- Maps OpenRouter request/response → the existing **canonical `LogEntryCreate`**
  schema, then calls the existing `ingest_log_entry(payload, user.id, db)`.
  → reuses message interning, parent/branch detection, transcript views, stats,
  cost service, and the entire dashboard read path with **no changes**.
- Mapping notes (OpenRouter → canonical):
  - `provider = "openrouter"`, `model = body["model"]`.
  - `request.messages` ← request `messages`; `request.params` ← all other
    request fields (temperature, top_p, tools, etc).
  - `response.message` ← `choices[0].message`;
    `response.finish_reason` ← `choices[0].finish_reason`.
  - `tool_calls` ← extracted from `choices[0].message.tool_calls` (+ any tool
    results if present in the next turn — best effort).
  - `usage` ← OpenRouter `usage` (`prompt_tokens`, `completion_tokens`,
    `total_tokens`).
  - `cost`: OpenRouter returns native cost in `usage` when available
    (`usage.cost` / generation stats). If present → `cost_source="client"`
    (i.e. upstream-reported); else fall back to the existing `ModelPrice`
    table → `cost_source="computed"`. **(Improvement vs push model: cost is
    authoritative because it comes from the provider.)**
  - `latency_ms` ← measured by the proxy (`now - ctx.started_at`).
  - `conversation_id`: clients can't always set it. Resolution order:
    1. explicit header `X-Conversation-Id`;
    2. OpenRouter/OpenAI `user` field or `metadata`;
    3. **derived** — hash of the leading system+first-user message (so a growing
       multi-turn thread maps to a stable id and the existing
       longest-prefix parent detection reconstructs the tree).
       See §5.2 for the failure modes this can introduce and the chosen
       safeguards. Document the tradeoffs in `docs/proxy.md`.
  - `status="error"` + `error` populated on upstream failures (`on_error`).
- Writes happen **after** the response is returned to the client (fire-and-forget
  via a background task / `BackgroundTasks`) so logging latency never adds to the
  user-perceived response time.

### 5.2 Conversation-id derivation — failure modes & safeguards

The derived id (option 3 above) is purely a **logging/grouping** concern: it
never affects the bytes returned to the client, so it cannot break a client's
functional behaviour. But a naive "hash the first system+user message" key can
mis-group or over-group logs. The risks and mitigations:

| Risk | What happens | Mitigation |
|------|--------------|------------|
| **Collision / over-grouping** | Two genuinely separate sessions that happen to start with the *same* system prompt + first user message (e.g. a fixed system prompt and a common opener like "hi") get the same `conversation_id`, so unrelated turns merge into one tree. | Keep grouping driven by the **longest-prefix parent detection** that already exists: the derived id is only a coarse bucket; within it, `resolve_parent_entry_id` links calls whose `message_ids` are actual prefixes of each other. Disjoint threads in the same bucket simply become **separate roots** (parent=None) under one conversation — visible as branches, not corrupted data. Also mix a per-`api_key` salt into the hash so different keys never share buckets. |
| **Edited / rewritten first message** | A client that edits earlier history (some agent frameworks rewrite the system prompt) changes the hash mid-thread → the continuation lands in a *new* bucket and looks like a brand-new conversation. | Acceptable for MVP and self-correcting: the new bucket still logs everything; the transcript just shows two conversations. Header `X-Conversation-Id` is the escape hatch for clients that care. Documented. |
| **Stateless single-shot calls** | Clients that send only one message per call (no history) produce a unique hash per call → every call is its own conversation. | Correct behaviour, not a bug — there genuinely is no multi-turn thread to group. |
| **Concurrency** | Two in-flight calls of the same growing thread race; parent resolution reads committed rows only. | Logging is fire-and-forget *after* the response, and `resolve_parent_entry_id` already tolerates missing/extra candidates (greedy longest prefix). Worst case is a transiently mis-attributed parent that the next call corrects; no client impact. |
| **PII in the hash input** | We hash message content. | We store only the **digest** as the id, never the raw content, and salt per key. No new exposure beyond what `messages` already stores. |

**Conclusion (answer to "could this break clients?")**: No — because the derived
id is metadata applied *after* the upstream response and is never sent back to
the client, it cannot change what a connecting client receives or how it
behaves. The only failure modes are *log-quality* issues (mis-grouping), and
the existing prefix-based parent detection plus a per-key salt contain them.
The explicit `X-Conversation-Id` header remains the precise opt-in for clients
that want guaranteed grouping. We will **not** require the header (keeps the
proxy drop-in), but we will recommend it in `docs/proxy.md`.

### 5.3 CompressionPlugin (`compression`) — **stub only**
- Implements `on_request` only.
- MVP: a documented no-op skeleton that demonstrates safe request mutation
  (e.g. where one *would* trim/dedupe/summarize `messages` to cut tokens),
  with the fail-open contract and a `state` entry recording bytes/tokens saved
  for the logging plugin to capture in `metadata`.
- Concrete strategy intentionally deferred (see §11).

---

## 6. New / changed modules

```
backend/app/
├── proxy/
│   ├── __init__.py
│   ├── context.py          # ProxyContext dataclass
│   ├── pipeline.py         # PluginPipeline: runs hooks, owns StreamAssembler
│   ├── assembler.py        # StreamAssembler: SSE deltas → full response dict
│   ├── upstream.py         # httpx client to OpenRouter (stream + non-stream)
│   ├── registry.py         # name→plugin map + build_pipeline(config)
│   └── plugins/
│       ├── __init__.py
│       ├── base.py         # BasePlugin (no-op defaults) + ProxyPlugin Protocol
│       ├── logging.py      # LoggingPlugin → ingest_log_entry
│       └── compression.py  # CompressionPlugin (stub)
├── routers/
│   └── proxy.py            # /chat/completions, /completions, /models, /proxy/health
├── services/
│   └── openrouter_map.py   # OpenRouter ⇄ canonical LogEntryCreate mapping
├── security/
│   └── api_key_auth.py     # + accept Authorization: Bearer, + proxy:use scope
└── config.py               # + openrouter_api_key, openrouter_base_url, proxy_plugins, proxy_timeouts
```

No schema migrations strictly required for MVP (we reuse `LogEntry`). Optional
additive migration: store a few proxy-specific fields in `metadata_extra`
(e.g. `compression_saved_tokens`, `upstream_request_id`) — no DDL needed since
`metadata_extra` is JSONB.

### 6.1 Config additions (`app/config.py` / `.env`)

```
OPENROUTER_API_KEY=sk-or-...            # server-held upstream key (required)
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_REFERER=                     # optional HTTP-Referer / X-Title headers OpenRouter recommends
OPENROUTER_APP_TITLE=LLM Stats Dashboard
PROXY_PLUGINS=logging                   # ordered, comma-separated
PROXY_UPSTREAM_TIMEOUT_S=120
PROXY_STREAM_IDLE_TIMEOUT_S=120
```

### 6.2 Dependency changes (`pyproject.toml`) — httpx swap

Investigation findings:
- `httpx>=0.28` is **needed and stays**. It is required transitively by
  `fastapi-cloud-cli` and used by FastAPI's `TestClient` (via Starlette). It is
  the upstream client we will use for the proxy (async + native streaming).
- `httpx2>=2.2.0` is a **real** package (next-gen httpx by Tom Christie /
  Pydantic, Beta), but it is **completely unused** — `grep` finds zero imports
  of `httpx2`/`httpcore2` anywhere in `app/` or `tests/`. It was added
  speculatively and only inflates the dependency tree (pulls in `httpcore2`).

Plan — consolidate on `httpx`:
1. Remove the `"httpx2>=2.2.0"` line from `[project].dependencies` in
   `backend/pyproject.toml`.
2. Fix the misleading comment on the `httpx` line: it is used for the proxy
   upstream client **and** the test client (not only "for TestClient").
   Suggested: `"httpx>=0.28",  # proxy upstream client (async + streaming) + TestClient`.
3. Regenerate the lock + prune the env: `uv lock` then `uv sync` (drops
   `httpx2` and its now-orphaned `httpcore2` from `uv.lock` / `.venv`).
4. Sanity check after sync: `make test-backend` (TestClient still resolves
   httpx) and a quick `uv pip show httpx2` should report not-installed.

Why `httpx` over `httpx2` for the proxy: `httpx` is stable/GA with a
well-documented streaming API (`AsyncClient.stream(...)`), it's already a
required transitive dep so it adds nothing, and pairing it with `respx` gives
us clean mocked-upstream tests. `httpx2` is still Beta — not worth adopting in
the request hot path right now.

---

## 7. Auth changes

The existing `get_current_user_from_api_key` only reads `X-API-Key`. OpenAI/
OpenRouter SDKs send `Authorization: Bearer <key>`. Plan:

- Add a combined extractor that accepts **either** `Authorization: Bearer lsd_…`
  **or** `X-API-Key: lsd_…` (both map to the same `lsd_` key validation path).
- Add a new scope **`proxy:use`**; the `/chat/completions` etc. routes depend on
  `require_scope("proxy:use")`.
- API-key creation UI/endpoint (`POST /api-keys`) gains `proxy:use` as a
  selectable scope (frontend `api-keys.tsx` scope list + backend allowed scopes).
- CSRF stays irrelevant for proxy routes (API-key auth, no ambient cookie),
  consistent with existing `/logs`.

---

## 8. Streaming details (correctness checklist)

- Use `httpx.AsyncClient.stream("POST", …)` and relay with a FastAPI
  `StreamingResponse(media_type="text/event-stream")`.
- Preserve SSE framing exactly (`data: {...}\n\n`, the terminal `data: [DONE]`).
- Forward client-relevant upstream headers; strip hop-by-hop and auth headers.
- Per-chunk: parse `data:` JSON → `on_stream_chunk` → re-serialize → write to
  client. Non-JSON keep-alives/comments relayed untouched.
- Backpressure: relay as we read (no full buffering of the stream for the
  client); the assembler keeps only the lightweight accumulated answer for
  logging.
- Ensure usage is captured: set `stream_options: {"include_usage": true}` on the
  upstream request when the client requested streaming (and the client didn't
  already), so logging gets token counts. Strip the synthetic flag's effects
  from what we count if needed.
- Client disconnect: detect via `request.is_disconnected()` / write failure;
  still run `on_response` with whatever was assembled (mark `status` / partial)
  so we log truncated streams.

---

## 9. Testing plan

Backend (`pytest`, against test DB, **OpenRouter mocked** via `respx`/httpx
mock transport — no live calls in CI):
- **Unit**
  - `openrouter_map`: OpenRouter req/resp → `LogEntryCreate` (incl. tool calls,
    multimodal content, error mapping, cost-from-usage vs computed fallback).
  - `StreamAssembler`: deltas → assembled response; usage capture; tool-call
    fragment merging; `[DONE]` handling.
  - Pipeline ordering & isolation: mutator runs before forward; logging failure
    doesn't bubble; chunk-hook returning `None` drops a chunk.
  - Conversation-id derivation determinism.
- **API/integration**
  - `POST /chat/completions` non-stream: mocked upstream → client gets correct
    body → a `LogEntry` row exists with right model/tokens/cost.
  - `POST /chat/completions` stream: SSE relayed correctly → one `LogEntry`
    written at stream end.
  - Auth: bearer header accepted; missing `proxy:use` → 403; bad key → 401.
  - Upstream 4xx/5xx surfaced to client + logged as `status="error"`.
  - Compression stub: enabled pipeline still passes through unchanged.

Add an integration switch (`PROXY_LIVE_TEST=1`) for an opt-in real-OpenRouter
smoke test, skipped by default.

---

## 10. Build order (milestones)

1. **Config + deps**: add OpenRouter settings; remove `httpx2`, fix the `httpx`
   comment, add `respx` to the `dev` extras for mocked-upstream tests;
   `uv lock && uv sync`; update `.env.example`.
2. **Upstream client**: `proxy/upstream.py` (non-stream + stream) against mocked
   OpenRouter; `/api/v1/models` passthrough + `/proxy/health`.
3. **Auth**: bearer-header acceptance + `proxy:use` scope (backend + API-key UI).
4. **Pipeline core**: `ProxyContext`, `BasePlugin`, `PluginPipeline`,
   `StreamAssembler`, registry/config — with a trivial pass-through pipeline.
5. **Non-stream proxy route**: `POST /chat/completions` (buffered) wired through
   the pipeline. Tests.
6. **Logging plugin**: `openrouter_map` + `LoggingPlugin` → `ingest_log_entry`;
   verify dashboard/transcript/stats show proxied calls with no UI change.
7. **Streaming**: SSE relay + assembler + end-of-stream logging. Tests
   (incl. disconnect/partial).
8. **Compression stub**: skeleton plugin + docs demonstrating safe mutation.
9. **Docs**: `docs/proxy.md` (drop-in setup: base_url + key, conversation-id
   header, examples curl/Python/JS), update `docs/index.md` + AI client guide,
   note coexistence with `POST /logs`.
10. **Hardening**: upstream timeouts, body-size limits, error mapping, rate
    limiting on proxy routes, plugin isolation review, `make check` green.

---

## 11. Future / post-MVP

- **Real compression strategies**: history truncation windows, message dedupe,
  summarization of old turns via a cheaper model, tool-result trimming — each a
  separate plugin; measure tokens saved (already plumbed via `ctx.state` →
  `metadata`).
- **Per-user / per-key plugin config** + management UI (table + `resolve_pipeline`).
- **Per-user OpenRouter keys** (encrypted at rest) as an alternative to the
  single server key.
- **Caching plugin** (semantic/exact-match) using `short_circuit_response`.
- **Additional providers** behind the same proxy/plugin interface (OpenAI,
  Anthropic native) via an upstream-client abstraction.
- **Idempotency / retry** keys; **rate-limit / budget** plugins (per-user spend
  caps that abort on `on_request`).
- **Prompt firewall / PII redaction** plugin.
- Streaming-aware compression (operating on `on_stream_chunk`).
```
