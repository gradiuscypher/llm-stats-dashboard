# LLM Stats Dashboard — Project Plan

> A web dashboard that tracks LLM usage by ingesting full conversation logs
> (every message to/from the client) plus metadata (token counts, cost, tool
> calls) from a user's LLM clients, enabling deep session debugging.

Status: **planning** (no implementation yet). This document is the source of
truth for the MVP scope and architecture.

---

## 1. Decisions (locked)

| Area | Decision |
|------|----------|
| Backend | Python 3.12, FastAPI |
| Data layer | SQLModel (Pydantic + SQLAlchemy) |
| Database | PostgreSQL (dev **and** prod) |
| Migrations | Alembic |
| Python toolchain | `uv`, `ruff`, `ty` (type checker) |
| Backend tests | `pytest` (unit + API integration against a test DB) |
| Frontend | Vite + React + TypeScript |
| Routing/data | TanStack Router + TanStack Query |
| Styling | Tailwind CSS + custom design tokens (Berkeley Mono aesthetic) |
| Frontend tests | Vitest + React Testing Library |
| Frontend toolchain | `pnpm`, ESLint, Prettier, `tsc` |
| Tenancy | Multi-user; data isolated per user |
| Browser auth | Server session cookies (httpOnly, SameSite), CSRF tokens |
| Login | Basic Auth (username + password) with OAuth-ready scaffolding |
| API auth | Multiple named, revocable, **scoped** API keys per user |
| Ingest model | Per-call (per-message) logging endpoint |
| Log schema | Normalized canonical schema |
| Cost calc | Both: accept client cost, else compute from a server pricing table |
| Docs | Markdown in `/docs` + served raw by API + rendered in frontend + OpenAPI |
| Repo layout | `/backend` + `/frontend`, root `Makefile` orchestrates |
| Deployment | Local dev only for MVP (no Docker yet) |

---

## 2. Repository layout

```
llm-stats-dashboard/
├── PLAN.md                  # this file
├── Makefile                 # all dev actions (see §9)
├── README.md
├── .env.example             # documented env vars
├── docs/                    # AI-first Markdown docs (source of truth)
│   ├── index.md             # entry point: overview + links to every page
│   ├── authentication.md
│   ├── api-keys.md
│   ├── endpoints/
│   │   ├── logs.md
│   │   ├── auth.md
│   │   └── ...
│   ├── schemas.md           # canonical log schema reference
│   └── ai-client-guide.md   # single self-contained page for an AI to build a client
├── backend/
│   ├── pyproject.toml       # uv-managed; ruff + ty config
│   ├── alembic.ini
│   ├── alembic/
│   ├── app/
│   │   ├── main.py          # FastAPI app factory, middleware, routers
│   │   ├── config.py        # pydantic-settings
│   │   ├── db.py            # engine/session
│   │   ├── security/        # password hashing, sessions, CSRF, api-key auth
│   │   ├── models/          # SQLModel tables (User, ApiKey, LogEntry, ...)
│   │   ├── schemas/         # request/response Pydantic models
│   │   ├── routers/         # auth, users, api_keys, logs, docs, health
│   │   ├── services/        # business logic (cost calc, ingest, pricing)
│   │   └── docs_loader.py   # serves /docs markdown
│   └── tests/
│       ├── conftest.py      # test DB, client fixtures
│       ├── unit/
│       └── api/
└── frontend/
    ├── package.json         # pnpm
    ├── vite.config.ts
    ├── tailwind.config.ts
    ├── index.html
    ├── public/fonts/        # Berkeley Mono woff2 (copied from ~/berkeley-mono-web)
    └── src/
        ├── main.tsx
        ├── router.tsx
        ├── styles/          # tokens.css, fonts.css, global.css
        ├── lib/             # api client, csrf, query setup
        ├── components/      # primitives (Button, Table, Field, ...)
        ├── routes/          # login, dashboard, logs, log-detail, api-keys, docs
        └── test/            # setup + component tests
```

---

## 3. Data model

All user data is scoped by `user_id`. Tables (SQLModel):

- **User**
  - `id` (uuid), `username` (unique), `email` (unique, nullable),
    `password_hash` (argon2/bcrypt), `is_active`, `created_at`, `updated_at`
  - OAuth-ready: `auth_provider` ('local' default), nullable
    `oauth_subject` / `oauth_provider` columns reserved for future use.

- **ApiKey**
  - `id`, `user_id` (fk), `name` (e.g. "laptop"), `prefix` (shown in UI),
    `key_hash` (we store only a hash; raw key shown once on creation),
    `scopes` (list: `logs:write`, `logs:read`), `last_used_at`,
    `revoked_at` (nullable), `created_at`
  - Key format: `lsd_<prefix>_<secret>`; lookup by prefix then constant-time
    hash compare.

- **LogEntry** (one row per LLM call)
  - `id`, `user_id` (fk), `conversation_id` (client-supplied, indexed),
    `created_at`, `client_timestamp`
  - `provider`, `model`
  - `request` (JSONB) and `response` (JSONB) in canonical schema (see §4)
  - `tool_calls` (JSONB array)
  - Usage: `prompt_tokens`, `completion_tokens`, `total_tokens`
  - Cost: `cost_total`, `cost_currency`, `cost_source` ('client' | 'computed')
  - `latency_ms` (nullable), `status`, `error` (nullable)
  - Indexes: `(user_id, created_at)`, `(user_id, conversation_id)`,
    `(user_id, model)`.

- **ModelPrice** (server pricing table for cost computation)
  - `id`, `provider`, `model`, `input_price_per_1k`, `output_price_per_1k`,
    `currency`, `effective_at`. Seeded via Makefile target.

- **Session** (server-side sessions; or signed cookie store — decide in impl)
  - `id`, `user_id`, `csrf_secret`, `created_at`, `expires_at`.

---

## 4. Canonical log schema (normalized)

Clients map their provider-native payload to this shape before posting.
Documented fully in `docs/schemas.md`.

```jsonc
{
  "conversation_id": "string",            // groups calls into a session
  "provider": "openai" | "anthropic" | "...",
  "model": "gpt-4o",
  "client_timestamp": "ISO-8601",
  "request": {
    "messages": [
      { "role": "system|user|assistant|tool", "content": "string|parts[]" }
    ],
    "params": { "temperature": 0.7, "max_tokens": 1024 }   // optional
  },
  "response": {
    "message": { "role": "assistant", "content": "string|parts[]" },
    "finish_reason": "stop|length|tool_calls|..."
  },
  "tool_calls": [
    { "id": "string", "name": "string", "arguments": {}, "result": {} }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "cost": {                                // optional; computed if omitted
    "total": 0.0123,
    "currency": "USD"
  },
  "latency_ms": 0,                         // optional
  "status": "ok" | "error",
  "error": null
}
```

Validation: Pydantic models with permissive `content` (string or structured
parts) so multimodal/tool content survives. Unknown extra keys preserved in a
`metadata` passthrough field.

---

## 5. API surface (MVP)

Base path `/api/v1`. FastAPI auto-generates OpenAPI at `/api/openapi.json`
and Swagger UI at `/api/docs`.

### Auth (session-cookie, browser)
- `POST /auth/login` — username+password → sets httpOnly session cookie + CSRF
- `POST /auth/logout`
- `GET  /auth/me` — current user
- `GET  /auth/csrf` — issue/rotate CSRF token
- (scaffold) `GET /auth/oauth/{provider}/authorize`, `/callback` — stubs/disabled

### Users
- `POST /users` — register (gated/configurable for single-operator use)
- `PATCH /users/me` — update profile/password

### API keys (session-authed)
- `GET    /api-keys` — list (prefix + metadata, never the secret)
- `POST   /api-keys` — create (returns raw secret **once**); body: name, scopes
- `DELETE /api-keys/{id}` — revoke

### Logs (the core)
- `POST /logs` — ingest one LLM call. **Auth: API key with `logs:write`.**
  Validates canonical schema, computes cost if absent, persists.
- `GET  /logs` — list/filter (cursor pagination; filter by conversation_id,
  model, date range). Auth: session **or** API key with `logs:read`.
- `GET  /logs/{id}` — full call detail (raw request/response).
- `GET  /conversations/{conversation_id}` — all calls in a conversation, ordered.
- `GET  /stats/summary` — aggregates: total tokens, total cost, calls,
  breakdown by model/day. Powers the dashboard.

### Docs
- `GET /docs-md` — index of available markdown docs (JSON list).
- `GET /docs-md/{path}` — raw markdown content (for AI fetch + frontend render).

### Health
- `GET /healthz`, `GET /readyz`.

---

## 6. Security

- **Passwords**: argon2id (via `passlib`/`argon2-cffi`).
- **Sessions**: httpOnly, `Secure` (prod), `SameSite=Lax` cookies; server-side
  session records with expiry + rotation on login.
- **CSRF**: double-submit token + `SameSite`; required on all state-changing
  cookie-authed requests. API-key requests are exempt (no ambient cookie).
- **API keys**: stored hashed; constant-time compare; scoped; revocable;
  `last_used_at` tracked; raw key shown once.
- **CSP**: strict default-src 'self'; explicit font/style/script sources; no
  inline scripts (nonce/hash if needed). Set via FastAPI middleware.
- **Other headers**: HSTS (prod), X-Content-Type-Options, Referrer-Policy,
  X-Frame-Options/`frame-ancestors 'none'`, Permissions-Policy.
- **CORS**: locked to the frontend origin; credentials allowed only for that
  origin.
- **Rate limiting**: basic per-key/per-IP limiter on `/auth/login` and `/logs`
  (slowapi or in-app limiter) — MVP-light, documented for hardening later.
- **Input limits**: max body size on `/logs` to bound large payloads.

---

## 7. Frontend

- **Aesthetic**: utilitarian, dense, grid-aligned, monospace-first — modeled on
  US Graphics Company. Berkeley Mono Web as the primary typeface.
  - Copy `~/berkeley-mono-web/*.woff2` into `frontend/public/fonts/`.
  - `@font-face` for Regular / Bold / Oblique / Bold-Oblique.
  - Design tokens: tight spacing scale, hairline borders, high-contrast
    monochrome palette + one accent, boxy/tabular tables, no rounded corners.
- **Stack**: Vite + React + TS, TanStack Router (typed routes) + TanStack Query
  (server state/caching), Tailwind with the token theme.
- **API client**: thin typed fetch wrapper; sends credentials; attaches CSRF
  header from cookie; React Query hooks per endpoint.
- **Routes/pages**:
  - `/login` — basic auth form.
  - `/` (dashboard) — summary stats: total cost, tokens, calls, charts by
    model/day (lightweight chart lib, e.g. visx/recharts kept minimal).
  - `/logs` — filterable/paginated table of calls.
  - `/logs/:id` — full conversation-call detail: rendered messages, tool calls,
    raw JSON view, token/cost breakdown.
  - `/conversations/:id` — threaded view of a whole session for debugging.
  - `/api-keys` — manage keys (create shows secret once, revoke).
  - `/docs` — renders the Markdown docs (react-markdown) with the index + links.
- **Tests**: Vitest + RTL for components and key flows (login form, key
  creation, log table rendering); mock the API layer.

---

## 8. Documentation (AI-first)

- Single source of truth: `/docs/*.md` in the repo.
- Served raw by the API (`/docs-md/...`) so an AI agent can fetch them, and
  rendered in the frontend `/docs` page.
- `docs/index.md`: overview + linked table of contents to every page and
  endpoint group (discoverable, cross-linked "related endpoints" sections).
- Every endpoint doc includes: purpose, auth requirement, request/response
  schema, **curl + Python + JS code examples**, error cases, related links.
- `docs/ai-client-guide.md`: a single self-contained page an AI can be handed
  to generate a full client — covers auth flow, getting an API key, the
  canonical log schema, the `POST /logs` contract, retries/idempotency notes,
  and a complete end-to-end example.
- OpenAPI/Swagger remains available at `/api/docs` as the machine-readable
  complement.

---

## 9. Tooling & Makefile

Root `Makefile` orchestrates backend + frontend. Planned targets:

```
make setup            # install backend (uv sync) + frontend (pnpm install)
make pg-install       # install/setup PostgreSQL on the host (idempotent)
make pg-start         # start local postgres
make db-create        # create dev + test databases + roles
make db-reset         # drop + recreate + migrate + seed (incl. pricing table)
make migrate          # alembic upgrade head
make migration m=...  # alembic revision --autogenerate
make seed             # seed model pricing + optional demo user
make dev              # run backend (uvicorn --reload) + frontend (vite) together
make dev-backend
make dev-frontend
make test             # backend pytest + frontend vitest
make test-backend
make test-frontend
make lint             # ruff check + ty + eslint + prettier --check
make fmt              # ruff format + prettier --write
make check            # lint + test (CI-equivalent)
```

Notes:
- `pg-install`/`pg-start` detect the platform (apt/brew) and are safe to re-run.
- Backend uses `uv` for env + deps; `ruff` for lint+format; `ty` for types.
- Frontend uses `pnpm`; ESLint + Prettier + `tsc --noEmit` for types.

---

## 10. Build order (suggested milestones)

1. **Scaffolding**: repo layout, Makefile, uv/pnpm init, ruff/ty/eslint config,
   Postgres setup targets, FastAPI app factory + healthz, Vite app shell + fonts
   + design tokens, CI-equivalent `make check`.
2. **Auth core**: User model, password hashing, session cookies, CSRF, login/
   logout/me, security headers + CSP middleware. Backend tests.
3. **Frontend auth**: login page, session-aware API client, protected routes.
4. **API keys**: model, scoped creation/list/revoke, key auth dependency, UI.
5. **Log ingest**: canonical schema, `POST /logs`, cost service + pricing seed,
   validation, tests.
6. **Log read + dashboard**: list/detail/conversation/stats endpoints, dashboard
   + logs table + detail UI.
7. **Docs**: write `/docs` markdown set, docs-serving endpoints, frontend docs
   renderer, `ai-client-guide.md`.
8. **OAuth scaffolding**: stub provider config + disabled routes documented for
   future enablement.
9. **Hardening pass**: rate limiting, body-size limits, security header review,
   test coverage gaps.

---

## 11. Open items / future (post-MVP)

- Docker / docker-compose packaging and deployment target.
- OAuth provider enablement (Google/GitHub).
- E2E tests (Playwright).
- Batch / per-conversation ingest endpoint.
- Multi-tenant orgs/teams.
- Streaming-log ingestion and idempotency keys for retries.
- Richer analytics (cost anomaly alerts, per-tool breakdowns).
- Pricing table auto-updates from provider price lists.
