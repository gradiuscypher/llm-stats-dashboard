# AGENTS.md

Workflow and conventions for AI agents working in this repository.

## Start here

Before making changes, read **[OVERVIEW.md](OVERVIEW.md)** — it maps the entire project
(backend, frontend, features, and where everything lives). It is the fastest way to
understand the codebase.

For a human-facing summary and quick start, see [README.md](README.md).

## Planning

When the user asks you to plan something, **output the plan in chat by default**. Only
write a plan to a file when the user explicitly asks you to **write a plan file** or
**create a plan** — and only then, create a Markdown file in the `plans/` directory
(e.g. `plans/CONVERSATIONS_PAGE_PLAN.md`).

- Use a descriptive, uppercase-with-underscores filename ending in `_PLAN.md`.
- A plan (whether in chat or file) should be detailed enough that another agent can
  implement it without re-discovering the codebase: list the exact files to change,
  new schemas/types, endpoints, tests, and verification steps.
- Keep existing plans (`plans/PLAN.md`, `plans/PROXY_PLAN.md`, etc.) as historical
  context; add new files rather than overwriting unrelated ones.

## Conventions

- **Dev commands go through the `Makefile`** — run `make help` to list them. Prefer
  `make` targets over ad-hoc commands so behavior stays consistent.
- **Backend**: FastAPI + SQLModel + Postgres, Python ≥ 3.12, managed with `uv`.
  Lint/type-check with `make lint-backend` (ruff + ty); format with `make fmt-backend`.
  Schema changes require an Alembic migration (`make migration m="..."`).
- **Frontend**: React 19 + Vite + TanStack Router/Query + Tailwind v4, package manager
  **pnpm**. Lint/type-check with `make lint-frontend` (eslint + prettier + tsc).
  Route all HTTP through `frontend/src/lib/api.ts`.
- **Tests**: add/adjust tests alongside changes. `make test` runs backend (pytest) +
  frontend (vitest). Backend tests live in `backend/tests/{unit,api,proxy}/`.
- **Before declaring done**: run `make check` (lint + test) and fix anything you broke.

## Docs

- Public/AI-facing API docs live in `docs/` and are served at `/api/v1/docs-md`
  (rendered in the UI at `/docs`). Update them when you change API behavior.
- Keep `OVERVIEW.md` and `README.md` accurate when project structure or features change.
