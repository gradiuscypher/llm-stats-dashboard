# LLM Stats Dashboard — Documentation

Welcome to the LLM Stats Dashboard API documentation. This index links every
available doc page. All pages are served as raw Markdown by the API at
`GET /api/v1/docs-md/{path}` and rendered in the dashboard UI at `/docs`.

---

## Quick start

**Using the proxy (automatic logging)**:
1. [Register an account](endpoints/auth.md) via `POST /api/v1/users`
2. Log in to get a session cookie — `POST /api/v1/auth/login`
3. [Create an API key](endpoints/api-keys.md) with `proxy:use` scope
4. Point your OpenRouter client at our base URL — see **[LLM Proxy](proxy.md)**

**Using the push API (manual logging)**:
1. [Register an account](endpoints/auth.md) via `POST /api/v1/users`
2. Log in to get a session cookie — `POST /api/v1/auth/login`
3. [Create an API key](endpoints/api-keys.md) with `logs:write` scope
4. [Send your first log entry](endpoints/logs.md) via `POST /api/v1/logs`

---

## Documentation pages

| Page | Description |
|------|-------------|
| [Authentication](endpoints/auth.md) | Login, logout, session cookies, CSRF |
| [Users](endpoints/users.md) | Registration and profile management |
| [API Keys](endpoints/api-keys.md) | Create, list, and revoke scoped API keys |
| [Log Ingestion & Retrieval](endpoints/logs.md) | `POST /logs` ingest + read endpoints |
| [Canonical Schema Reference](schemas.md) | Full log payload schema with field docs |
| [AI Client Guide](ai-client-guide.md) | **Hand this to an AI to auto-generate a client** |
| [LLM Proxy](proxy.md) | **Drop-in OpenRouter proxy with automatic logging** |

---

## Documentation endpoints (no auth required)

The docs endpoints are **publicly accessible** — no login or API key needed.
A coding agent can fetch the full documentation set before even registering:

```bash
# List all doc pages
curl http://localhost:8000/api/v1/docs-md

# Fetch a specific page (raw Markdown)
curl http://localhost:8000/api/v1/docs-md/ai-client-guide.md
curl http://localhost:8000/api/v1/docs-md/schemas.md
curl http://localhost:8000/api/v1/docs-md/endpoints/logs.md
```

**Recommended starting point for a coding agent**: fetch `ai-client-guide.md` — it is
self-contained and covers everything needed to register, create an API key, and start
sending log entries.

---

## Base URL

All API endpoints are under `/api/v1`. The base URL for a local dev server is:

```
http://localhost:8000/api/v1
```

## OpenAPI

Machine-readable OpenAPI spec: `GET /api/openapi.json`  
Swagger UI: `/api/docs`  
ReDoc: `/api/redoc`

## Authentication overview

- **Browser / dashboard**: session cookie (`lsd_session`) + CSRF token
  (`lsd_csrf`). Use the `/auth/login` endpoint.
- **Programmatic / client logging**: API key in `X-API-Key` header.
  Keys are scoped (`logs:write`, `logs:read`) and revocable.

See [Authentication](endpoints/auth.md) for details.
