# path: Makefile
#
# Convenience wrappers. Everything here is a plain command you can also run by hand —
# nothing is hidden behind the Makefile.

SHELL := /bin/bash
PY    ?= python3
BACKEND := backend
FRONTEND := frontend

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ─── setup ────────────────────────────────────────────────────────────────────

.PHONY: install
install: install-backend install-frontend ## Install all dependencies

.PHONY: install-backend
install-backend: ## Create the venv and install Python dependencies
	cd $(BACKEND) && $(PY) -m venv .venv && . .venv/bin/activate && \
		pip install --upgrade pip && pip install -r requirements.txt

.PHONY: install-frontend
install-frontend: ## Install client dependencies
	cd $(FRONTEND) && npm install

# ─── development ──────────────────────────────────────────────────────────────

.PHONY: dev
dev: ## Run backend and frontend together (Ctrl-C stops both)
	@echo "backend  → http://localhost:8000"
	@echo "frontend → http://localhost:5173"
	@trap 'kill 0' EXIT INT TERM; \
	(cd $(BACKEND) && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) & \
	(cd $(FRONTEND) && npm run dev) & \
	wait

.PHONY: dev-backend
dev-backend: ## Run only the backend, with autoreload
	cd $(BACKEND) && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

.PHONY: dev-frontend
dev-frontend: ## Run only the Vite dev server
	cd $(FRONTEND) && npm run dev

# ─── assets ───────────────────────────────────────────────────────────────────

.PHONY: assets
assets: map nav ## Regenerate the map and its nav graph

.PHONY: map
map: ## Regenerate assets/maps/alley.json from code
	cd $(BACKEND) && $(PY) -m app.scripts.gen_map

.PHONY: nav
nav: ## Regenerate the bot waypoint graph
	cd $(BACKEND) && $(PY) -m app.scripts.gen_nav alley

# ─── quality ──────────────────────────────────────────────────────────────────

.PHONY: test
test: ## Run the backend test suite
	cd $(BACKEND) && $(PY) -m pytest

.PHONY: smoke
smoke: ## Run a headless bot match as an end-to-end smoke test
	cd $(BACKEND) && $(PY) -m app.scripts.run_headless_match --bots 10 --ticks 5000

.PHONY: check-parity
check-parity: ## Verify the client and server agree on protocol and movement
	$(PY) scripts/check-parity.py

.PHONY: typecheck
typecheck: ## Type-check the client
	cd $(FRONTEND) && npm run typecheck

.PHONY: lint
lint: ## Lint the client
	cd $(FRONTEND) && npm run lint

.PHONY: format
format: ## Format the client sources
	cd $(FRONTEND) && npm run format

.PHONY: check
check: test typecheck lint check-parity smoke ## Everything CI would run

# ─── build and deploy ─────────────────────────────────────────────────────────

.PHONY: build
build: ## Build the production client bundle
	cd $(FRONTEND) && npm run build

.PHONY: up
up: ## Start the full stack with Docker Compose
	docker compose up -d --build

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: logs
logs: ## Follow container logs
	docker compose logs -f

.PHONY: deploy
deploy: ## Pull, rebuild and health-check on a server
	./scripts/deploy.sh

.PHONY: clean
clean: ## Remove build artefacts and caches
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite
	find $(BACKEND) -type d -name __pycache__ -prune -exec rm -rf {} +
	find $(BACKEND) -type d -name .pytest_cache -prune -exec rm -rf {} +
