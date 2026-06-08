# LLM Stats Dashboard (LSD)

Track LLM usage — token counts, costs, latency, and tool calls — and browse calls
grouped into conversations through a web dashboard.

> **Agents/contributors:** read [OVERVIEW.md](OVERVIEW.md) for a full project map and
> [AGENTS.md](AGENTS.md) for workflow conventions.

## Features

- **Two ingestion paths**
  - **Push API** — `POST /api/v1/logs` with a canonical log payload (you track and send
    your own calls).
  - **OpenRouter proxy** — point an OpenRouter-compatible client at LSD's base URL; calls
    are transparently forwarded and logged automatically (drop-in, supports streaming).
- **Dashboard (React SPA)** — stats overview with charts, a logs table, single-call
  detail, conversation transcript view (with retry/branch detection), API-key management,
  in-app docs, and theme/font settings.
- **Conversations** — calls grouped by `conversation_id`; the transcript view
  reconstructs a deduplicated, ordered thread with branches for retries/edits.
- **Cost tracking** — uses client-supplied cost or computes it from a model-pricing table.
- **Scoped API keys** — `logs:write`, `logs:read`, `proxy:use`; revocable, argon2-hashed.
- **Auth & security** — cookie sessions + CSRF for the browser, API keys for clients;
  rate limiting, security headers, CORS.
- **Self-describing docs** — Markdown docs served at `/api/v1/docs-md` (public) so a
  coding agent can learn the API before registering.

## Implementation overview

| Layer | Stack |
|-------|-------|
| **Backend** (`backend/`) | FastAPI · SQLModel · PostgreSQL · Alembic · `uv` (Python ≥ 3.12). App factory at `app.main:app`; routers under `/api/v1`. |
| **Frontend** (`frontend/`) | React 19 · Vite · TanStack Router + Query · Tailwind CSS v4 · Recharts · `pnpm`. |
| **Proxy** (`backend/app/proxy/`) | Plugin pipeline around an httpx forwarder to OpenRouter; a logging plugin persists each call. |

Key design points: messages are **interned/deduplicated** in a `messages` table and
referenced by `LogEntry.message_ids`; `parent_entry_id` rebuilds the conversation tree.
Canonical history stores **original** (pre-transform) messages — plugin transforms are
tracked as diffs (overlay) so the conversation tree is stable across toggles.
See [OVERVIEW.md](OVERVIEW.md) for the detailed map (models, routers, services, security,
and frontend routes).

## Quick start

Everything is driven by the `Makefile` — run `make help` for the full list.

```bash
# 1. Install backend + frontend dependencies
make setup

# 2. Configure environment (copies .env.example → backend/.env on first setup)
#    Edit backend/.env: SECRET_KEY, DATABASE_URL, OPENROUTER_API_KEY, etc.

# 3. Create databases, run migrations, seed model pricing
make db-create
make migrate
make seed

# 4. Run both dev servers (backend :8000, frontend :5173)
make dev
```

Then open the dashboard at <http://localhost:5173>. API docs:

- Markdown docs (public): <http://localhost:8000/api/v1/docs-md>
- Swagger UI: <http://localhost:8000/api/docs> · OpenAPI: `/api/openapi.json`

## Development

```bash
make test     # backend (pytest) + frontend (vitest)
make lint     # ruff + ty · eslint + prettier + tsc
make fmt      # auto-format backend + frontend
make check    # full CI-equivalent: lint + test
```

## Project structure

```
backend/    FastAPI app (models, schemas, routers, services, security, proxy) + tests
frontend/   React SPA (routes, components, lib/api client)
docs/        Markdown docs served by the API and rendered at /docs
plans/       Design/implementation plans (one file per feature)
scripts/     DB helper scripts
Makefile     All dev commands
```

## Documentation

- [OVERVIEW.md](OVERVIEW.md) — full project map (read this first).
- [AGENTS.md](AGENTS.md) — agent workflow & conventions.
- [`docs/`](docs/) — API reference, schema reference, AI client guide, proxy guide.
- [`plans/`](plans/) — design and implementation plans.
