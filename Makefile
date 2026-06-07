# LLM Stats Dashboard — root Makefile
# All dev actions are here. Run `make help` for a summary.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PG_USER      := lsd_user
PG_PASS      := lsd_pass
PG_DB_DEV    := lsd_dev
PG_DB_TEST   := lsd_test
PG_VERSION   := 16

BACKEND_DIR  := backend
FRONTEND_DIR := frontend

# Detect OS for package manager
UNAME := $(shell uname -s)

# ─── Help ─────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' | sort

# ─── Setup ────────────────────────────────────────────────────────────────────

.PHONY: setup
setup: setup-backend setup-frontend ## Install all dependencies (backend + frontend)

.PHONY: setup-backend
setup-backend: ## Install backend Python deps with uv
	cd $(BACKEND_DIR) && uv sync --extra dev
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		cp .env.example $(BACKEND_DIR)/.env; \
		echo "  → Copied .env.example to backend/.env — edit it before running"; \
	fi

.PHONY: setup-frontend
setup-frontend: ## Install frontend JS deps with pnpm
	cd $(FRONTEND_DIR) && pnpm install

# ─── PostgreSQL ───────────────────────────────────────────────────────────────

.PHONY: pg-install
pg-install: ## Install PostgreSQL $(PG_VERSION) on the host (Ubuntu/apt only)
ifeq ($(UNAME), Linux)
	@echo "→ Installing postgresql-$(PG_VERSION)..."
	sudo apt-get update -qq
	sudo apt-get install -y postgresql-$(PG_VERSION) postgresql-client-$(PG_VERSION)
	@echo "→ PostgreSQL $(PG_VERSION) installed."
else
	@echo "→ Non-Linux detected. Install PostgreSQL manually, then re-run make db-create."
endif

.PHONY: pg-start
pg-start: ## Start the PostgreSQL service
	sudo systemctl start postgresql || sudo service postgresql start

.PHONY: pg-stop
pg-stop: ## Stop the PostgreSQL service
	sudo systemctl stop postgresql || sudo service postgresql stop

.PHONY: pg-status
pg-status: ## Show PostgreSQL service status
	sudo systemctl status postgresql 2>/dev/null || sudo service postgresql status

# ─── Database ─────────────────────────────────────────────────────────────────

.PHONY: db-create
db-create: ## Create dev + test databases and the app role (idempotent)
	bash scripts/db-create.sh

.PHONY: db-reset
db-reset: ## Drop + recreate + migrate + seed databases (destructive!)
	bash scripts/db-reset.sh
	$(MAKE) migrate
	$(MAKE) seed
	@echo "→ Reset complete."

.PHONY: migrate
migrate: ## Run Alembic migrations (upgrade head)
	cd $(BACKEND_DIR) && uv run alembic upgrade head

.PHONY: migration
migration: ## Generate a new migration: make migration m="describe change"
	@test -n "$(m)" || (echo "Usage: make migration m='describe change'" && exit 1)
	cd $(BACKEND_DIR) && uv run alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Seed the model pricing table
	cd $(BACKEND_DIR) && uv run python scripts/seed.py
	@echo "→ Seed complete."

# ─── Dev servers ──────────────────────────────────────────────────────────────

.PHONY: dev
dev: ## Run backend + frontend dev servers (requires two terminals or tmux)
	@echo "→ Starting backend and frontend in parallel..."
	@$(MAKE) -j2 dev-backend dev-frontend

.PHONY: dev-backend
dev-backend: ## Run FastAPI backend with hot reload
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000

.PHONY: dev-frontend
dev-frontend: ## Run Vite frontend dev server
	cd $(FRONTEND_DIR) && pnpm dev --host 0.0.0.0

# ─── Testing ──────────────────────────────────────────────────────────────────

.PHONY: test
test: test-backend test-frontend ## Run all tests

.PHONY: test-backend
test-backend: ## Run pytest (backend unit + API tests)
	cd $(BACKEND_DIR) && uv run pytest -v --tb=short

.PHONY: test-backend-unit
test-backend-unit: ## Run only unit tests
	cd $(BACKEND_DIR) && uv run pytest tests/unit -v

.PHONY: test-backend-api
test-backend-api: ## Run only API integration tests
	cd $(BACKEND_DIR) && uv run pytest tests/api -v

.PHONY: test-frontend
test-frontend: ## Run Vitest (frontend component tests)
	cd $(FRONTEND_DIR) && pnpm test

.PHONY: test-frontend-watch
test-frontend-watch: ## Run Vitest in watch mode
	cd $(FRONTEND_DIR) && pnpm test:watch

# ─── Lint + Format ────────────────────────────────────────────────────────────

.PHONY: lint
lint: lint-backend lint-frontend ## Run all linters

.PHONY: lint-backend
lint-backend: ## ruff check + ty (type check)
	cd $(BACKEND_DIR) && uv run ruff check .
	cd $(BACKEND_DIR) && uv run ty check app

.PHONY: lint-frontend
lint-frontend: ## eslint + prettier check + tsc
	cd $(FRONTEND_DIR) && pnpm lint
	cd $(FRONTEND_DIR) && pnpm fmt:check
	cd $(FRONTEND_DIR) && pnpm typecheck

.PHONY: fmt
fmt: fmt-backend fmt-frontend ## Format all code

.PHONY: fmt-backend
fmt-backend: ## ruff format (backend)
	cd $(BACKEND_DIR) && uv run ruff format .
	cd $(BACKEND_DIR) && uv run ruff check --fix .

.PHONY: fmt-frontend
fmt-frontend: ## prettier --write (frontend)
	cd $(FRONTEND_DIR) && pnpm fmt

# ─── CI check ─────────────────────────────────────────────────────────────────

.PHONY: check
check: lint test ## Full CI-equivalent: lint + test
