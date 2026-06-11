# Project Overview

A comprehensive map of the **LLM Stats Dashboard (LSD)** codebase — layout, features,
and where things live. Read this first to orient yourself before making changes.

> For day-to-day agent workflow rules (planning, conventions), see [AGENTS.md](AGENTS.md).
> For human-facing summary + quick start, see [README.md](README.md).

---

## 1. What this project is

LSD tracks LLM usage — token counts, costs, latency, tool calls — and lets you browse
calls grouped into conversations. There are **two ways** usage data enters the system:

1. **Push API (manual logging)** — clients `POST /api/v1/logs` with a canonical log
   payload. They compute/track their own calls and send them.
2. **OpenRouter proxy (automatic logging)** — clients point their OpenRouter-compatible
   client at LSD's base URL. LSD transparently forwards to OpenRouter and logs the call
   in the background. This is the "drop-in" path.

A web dashboard (React SPA) provides login, an overview/stats page, a logs table, a
conversation transcript view, API-key management, in-app docs, and settings.

---

## 2. Top-level layout

```
llm-stats-dashboard/
├── AGENTS.md            # Agent workflow rules (planning, conventions) — read this
├── OVERVIEW.md          # This file — project map
├── README.md            # Human summary + quick start
├── Makefile             # ALL dev actions (setup, db, dev, test, lint) — `make help`
├── .env.example         # Env var template → copy to backend/.env
├── plans/               # Design/implementation plans (one .md per feature)
│   ├── PLAN.md          # Original project plan
│   ├── PROXY_PLAN.md    # Proxy subsystem design
│   ├── CONVERSATIONS_PAGE_PLAN.md
│   └── PLUGIN_TOGGLE_AND_WORDCOUNT_PLAN.md
├── docs/                # Markdown docs served by the API + rendered in /docs UI
├── scripts/             # db-create.sh, db-reset.sh helpers
├── backend/             # FastAPI + SQLModel + Postgres
└── frontend/            # React 19 + Vite + TanStack Router/Query + Tailwind v4
```

Note: `main.py`, `pyproject.toml`, `uv.lock`, `.venv/` at the repo root are vestigial;
the real backend lives in `backend/`.

---

## 3. Backend (`backend/`)

FastAPI app, SQLModel ORM, Alembic migrations, PostgreSQL. Python ≥ 3.12, managed with
`uv`. Entry point: `app.main:app`.

### 3.1 Directory map

```
backend/
├── app/
│   ├── main.py              # App factory, middleware, router mounting
│   ├── config.py            # Settings (env vars) — `settings` singleton
│   ├── db.py                # Engine + `get_session` dependency
│   ├── logging_config.py    # Logging setup (file + console)
│   ├── docs_loader.py       # Loads docs/ Markdown for the docs API
│   ├── models/              # SQLModel table definitions
│   │   ├── api_key.py
│   │   ├── log_entry.py
│   │   ├── message.py
│   │   ├── message_modification.py
│   │   ├── model_price.py
│   │   ├── plugin_config.py
│   │   ├── session.py
│   │   └── user.py
│   ├── schemas/             # Pydantic request/response schemas
│   ├── routers/             # API endpoints (one module per resource)
│   ├── services/            # Business logic (ingest, cost, stats, dedup, mapping)
│   ├── security/            # Auth, passwords, sessions, CSRF, API-key auth
│   └── proxy/               # OpenRouter proxy subsystem (pipeline + plugins)
├── alembic/                 # Migrations (env.py + versions/)
├── tests/                   # unit/, api/, proxy/  (pytest)
├── scripts/seed.py          # Seeds model_prices table
├── conftest.py / tests/conftest.py
└── pyproject.toml           # Deps, ruff, pytest config
```

### 3.2 Models (`app/models/`) → DB tables

| Model | Table | Purpose |
|-------|-------|---------|
| `User` | `users` | Account (username, email, password hash) |
| `UserSession` | `user_sessions` | Server-side session records (cookie-backed) |
| `ApiKey` | `api_keys` | Scoped API keys (`prefix`, argon2 `key_hash`, `scopes`, revocation) |
| `LogEntry` | `log_entries` | One LLM call. Stores provider/model, usage (incl. cache tokens), cost, status, `conversation_id`, `message_ids` (ARRAY), `parent_entry_id` (conversation tree) |
| `Message` | `messages` | Deduplicated/interned messages (content-hashed), referenced by `LogEntry.message_ids` |
| `ModelPrice` | `model_prices` | Per-model pricing for cost computation |
| `PluginConfig` | `plugin_config` | Per-user global plugin enable/disable state |
| `PluginConfigConversation` | `plugin_config_conversation` | Per-conversation plugin override (beats global) |
| `MessageModification` | `message_modifications` | (Legacy) Plugin mutations to request/response messages. Superseded by `message_diffs`. |
| `MessageDiff` | `message_diffs` | Per-message original→final content diffs from proxy transforms (replaces `message_modifications`). |

Key design point: messages are **interned/deduplicated**. A `LogEntry` does not store
its messages inline — it stores an ordered list of `message_ids` pointing at the
`messages` table. `parent_entry_id` reconstructs the conversation tree (enabling
branching/retry detection in the transcript view).

The canonical history stores **original** (pre-transform) messages — plugin transforms
are tracked as diffs in `message_diffs`, which function as an overlay. This keeps the
conversation tree stable across plugin toggles.

### 3.3 Routers (`app/routers/`) — all mounted under `/api/v1` (except health)

| Module | Prefix | Notable endpoints |
|--------|--------|-------------------|
| `health.py` | (none) | `GET /healthz`, `GET /readyz` |
| `auth.py` | `/auth` | `GET /csrf`, `POST /login`, `POST /logout`, `GET /me`, OAuth scaffold |
| `users.py` | `/users` | `POST /users` (register), `PATCH /users/me` |
| `api_keys.py` | `/api-keys` | `GET` list, `POST` create, `DELETE /{id}` revoke |
| `logs.py` | (none, mounted at prefix) | `POST /logs`, `GET /logs`, `GET /logs/{id}`, `GET /conversations/{id}`, `GET /conversations/{id}/transcript`, `GET /stats/summary` |
| `docs_router.py` | `/docs-md` | `GET` list docs, `GET /{path}` raw Markdown (**public, no auth**) |
| `proxy.py` | (none, mounted at prefix) | `POST /chat/completions`, `POST /completions`, `GET /models`, `GET /proxy/health` |
| `plugins.py` | `/plugins` | Plugin toggles: per-user global (`GET/PUT /plugins`, `PUT /plugins/{name}`) + per-conversation overrides (`GET/PUT/DELETE /conversations/{id}/plugins/{name}`) |

OpenAPI: `/api/openapi.json`; Swagger `/api/docs`; ReDoc `/api/redoc`.

### 3.4 Services (`app/services/`) — business logic

| File | Responsibility |
|------|----------------|
| `ingest.py` | `ingest_log_entry()` — validate, intern messages, resolve cost + parent, persist a `LogEntry` |
| `messages.py` | Message interning/dedup: `content_hash`, `intern_messages`, `rehydrate_messages`, `batch_rehydrate_messages`, `resolve_parent_entry_id`. Canonical history stores **original** (pre-transform) messages; diffs are an overlay. |
| `cost.py` | `resolve_cost()` — use client-supplied cost or compute from `model_prices` |
| `stats.py` | `get_stats()` — aggregates for the dashboard (totals, by-day, by-model) with flexible time ranges (intervals: 5m/1h/1d/1w/1mo), cache metrics, and compression savings |
| `openrouter_map.py` | Maps OpenRouter responses → canonical `LogEntryCreate`; `derive_conversation_id`, `candidate_conversation_id` (pre-call, no-DB), `map_to_log_entry`, `map_error_to_log_entry` |
| `modifications.py` | `persist_modifications` — (Legacy) writes `RecordedModification` entries to the `message_modifications` table |
| `diffs.py` | `persist_diffs`, `batch_fetch_diffs` — persists `MessageDiff` rows from the interceptor to the `message_diffs` table |

### 3.5 Security (`app/security/`)

- **Sessions** (`sessions.py`): `create_session`, `revoke_session`, `get_current_user`
  (reads signed `lsd_session` cookie). Used by the browser/dashboard.
- **CSRF** (`csrf.py`): signed `lsd_csrf` token; `require_csrf` guards mutating routes.
- **Passwords** (`passwords.py`): argon2 hash/verify.
- **API-key auth** (`api_key_auth.py`): `get_current_user_from_api_key`,
  `_extract_api_key`, `_parse_key`, `require_scope`. Keys look like `lsd_...`.

**Scopes**: `logs:write` (push ingest), `logs:read` (read endpoints via API key),
`proxy:use` (allowed through the OpenRouter proxy).

**Auth model**: read endpoints accept *either* a session cookie *or* an API key with
the right scope (see `logs.py::_resolve_user`). Ingest requires `logs:write`. Proxy
requires `proxy:use`.

### 3.6 Proxy subsystem (`app/proxy/`)

Transparent OpenRouter passthrough that logs calls automatically. See
`plans/PROXY_PLAN.md` and `docs/proxy.md`.

| File | Responsibility |
|------|----------------|
| `upstream.py` | `forward_non_stream` / `forward_stream` to OpenRouter via httpx; header building + hop-by-hop stripping |
| `context.py` | `ProxyContext` — per-request state passed through the pipeline |
| `pipeline.py` | `PluginPipeline` — runs ordered plugins around the upstream call |
| `registry.py` | Maps plugin names → classes; `resolve_pipeline(user_id, conversation_id, db)` builds per-request pipelines with toggle support; legacy `get_pipeline()` singleton |
| `assembler.py` | `StreamAssembler` — reconstructs a full response from SSE chunks (so streamed calls can be logged) |
| `plugins/base.py` | `BasePlugin` interface (request/response/response_sync/stream hooks) |
| `plugins/logging.py` | `LoggingPlugin` — persists a `LogEntry` + `MessageModification` rows after the call |
| `plugins/compression.py` | `CompressionPlugin` — Headroom-backed request compression; reduces token usage before forwarding |
| `plugins/word_count.py` | `WordCountPlugin` — sample plugin: appends word-count markers to messages, records modifications |

Flow: client → `/api/v1/chat/completions` → `resolve_pipeline` (per-user/per-conv) → pre-hooks → forward to OpenRouter
→ (stream assembled if needed) → `on_response_sync` (mutate client-visible body) → `on_response` (logging) → response to client.

### 3.7 Config (`app/config.py` ← `.env`)

Key env vars (see `.env.example`): `DATABASE_URL`, `TEST_DATABASE_URL`, `SECRET_KEY`,
`SESSION_MAX_AGE_SECONDS`, `ALLOWED_ORIGINS`, `MAX_LOG_BODY_BYTES`, `LOG_LEVEL`,
`LOG_FILE`, and proxy vars (`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`,
`PROXY_PLUGINS`, timeouts, compression settings).

### 3.8 Migrations

Alembic in `backend/alembic/`. Current versions: initial, message-dedup,
api-key-on-log-entry. Generate with `make migration m="..."`, apply with `make migrate`.

---

## 4. Frontend (`frontend/`)

React 19 SPA. Vite build, TanStack Router (routing) + TanStack Query (data),
Tailwind CSS v4, Recharts (charts), react-markdown + remark-gfm (docs rendering).
Vitest + Testing Library for tests. Package manager: **pnpm**.

### 4.1 Directory map

```
frontend/src/
├── main.tsx              # App bootstrap
├── router.tsx            # Route tree + auth guard (requireAuth)
├── routes/               # One file per page
│   ├── login.tsx / register.tsx
│   ├── dashboard.tsx     # Stats overview (StatCards + Recharts), /stats/summary
│   ├── logs.tsx          # Logs table (per-call), filters + pagination
│   ├── log-detail.tsx    # Single log detail (/logs/$logId)
│   ├── conversation.tsx  # Transcript view (/conversations/$conversationId) — trunk + branches
│   ├── api-keys.tsx      # API key CRUD
│   ├── docs.tsx          # Renders docs/ Markdown from the API
│   └── settings.tsx      # Theme + font-size preferences
├── components/           # Reusable UI: Layout (nav), PageHeader, Button, Field,
│                         #   StatCard, StatusBadge, ThemeToggle
├── lib/
│   ├── api.ts            # Typed API client (apiFetch + CSRF handling) + all types
│   ├── queryClient.ts    # TanStack Query client
│   ├── useTheme.ts       # Light/dark/system theme hook
│   └── useFontSize.ts    # Font-size preference hook
├── styles/               # global.css, fonts.css
└── test/                 # Vitest component tests
```

### 4.2 Routing & nav

- Routes are defined in `frontend/src/router.tsx`. Most pages are guarded by
  `requireAuth` (fetches `/me`, redirects to `/login` on 401). `/docs` is public.
- The top nav lives in `frontend/src/components/Layout.tsx` (`NAV_LINKS`). Every page
  wraps its content in `<Layout>`.

### 4.3 Data layer

- All HTTP goes through `frontend/src/lib/api.ts`. `apiFetch` sends `credentials:
  "include"` and attaches `X-CSRF-Token` (read from the `lsd_csrf` cookie) on mutating
  requests. API groups: `authApi`, `usersApi`, `apiKeysApi`, `logsApi`, `docsApi`.
- Server state is cached via TanStack Query; query keys are co-located in each route.

---

## 5. Key end-to-end flows

**Login (browser)**: `GET /auth/csrf` → `POST /auth/login` (sets `lsd_session` +
`lsd_csrf` cookies) → subsequent requests carry the cookie; mutations send the CSRF
header.

**Manual log ingest**: client with `logs:write` key → `POST /api/v1/logs` →
`ingest_log_entry` interns messages (dedup), resolves cost + `parent_entry_id`, persists
a `LogEntry`.

**Proxy logging**: client with `proxy:use` key → `POST /api/v1/chat/completions` →
proxy pipeline forwards to OpenRouter, assembles the response, `LoggingPlugin` maps it
via `openrouter_map` and persists a `LogEntry`.

**Conversation view**: `GET /conversations/{id}/transcript` rebuilds a deduped,
ordered transcript using `message_ids` + `parent_entry_id`, returning a trunk plus
branch paths for retries/edits; rendered by `conversation.tsx`.

---

## 6. Dev commands (via `Makefile` — run `make help`)

| Task | Command |
|------|---------|
| Install everything | `make setup` |
| Create databases | `make db-create` |
| Migrate | `make migrate` · new migration: `make migration m="..."` |
| Seed pricing | `make seed` |
| Run both dev servers | `make dev` (or `make dev-backend` / `make dev-frontend`) |
| All tests | `make test` (`test-backend`, `test-frontend`) |
| Lint | `make lint` (ruff + ty; eslint + prettier + tsc) |
| Format | `make fmt` |
| Full CI check | `make check` |

Backend: FastAPI on `:8000`. Frontend: Vite on `:5173`.

---

## 7. Where to look for X

| Looking for… | Go to |
|--------------|-------|
| A new API endpoint | `backend/app/routers/` + matching schema in `app/schemas/` |
| DB schema / a table | `backend/app/models/` (+ add an Alembic migration) |
| Business logic | `backend/app/services/` |
| Auth / scopes / CSRF | `backend/app/security/` |
| Proxy behavior / plugins | `backend/app/proxy/` |
| Plugin toggles (API) | `backend/app/routers/plugins.py` |
| Plugin toggles (UI) | `frontend/src/routes/settings.tsx` (global), `frontend/src/routes/conversation.tsx` (per-conversation) |
| Plugin modifications / badges | `backend/app/models/message_modification.py`, `frontend/src/components/ModificationBadge.tsx` |
| A dashboard page | `frontend/src/routes/` |
| Nav / shared UI | `frontend/src/components/` |
| API client / TS types | `frontend/src/lib/api.ts` |
| Public/AI-facing docs | `docs/` (served at `/api/v1/docs-md`, UI at `/docs`) |
| Design/plans | `plans/` |
| Any dev command | `Makefile` |
