# LLM Stats Dashboard — Documentation

Welcome to the LLM Stats Dashboard API documentation. This index links every
available doc page. All pages are served as raw Markdown by the API at
`GET /api/v1/docs-md/{path}` and rendered in the dashboard UI at `/docs`.

---

## Quick start

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
