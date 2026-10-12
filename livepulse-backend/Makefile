# LivePulse — dev workflow shortcuts.
#
# Conventions:
#   - All paths are relative to this Makefile (project/).
#   - Static tools (ffmpeg / ffprobe / mc) live in .tools/ and are prepended
#     to PATH so a fresh shell doesn't pick up brew's broken versions.
#   - `make help` lists every target with its docstring.
#
# Run `make help` if unsure what to do.

SHELL          := /usr/bin/env bash
.SHELLFLAGS    := -eu -o pipefail -c
.DEFAULT_GOAL  := help

PROJECT_ROOT   := $(CURDIR)
VENV           := $(PROJECT_ROOT)/.venv
VENV_PY        := $(VENV)/bin/python
VENV_PIP       := $(VENV)/bin/pip
VENV_ALEMBIC   := $(VENV)/bin/alembic
TOOLS_DIR      := $(PROJECT_ROOT)/.tools
BACKEND_PORT   := 8004
FRONTEND_PORT  := 3001
PG_CONTAINER     := workbuddy_postgres
REDIS_CONTAINER  := workbuddy_redis
MINIO_HEALTH     := http://localhost:9002/minio/health/live
API_KEY        := $(shell grep -E '^API_KEY=' backend/.env 2>/dev/null | head -1 | cut -d= -f2-)

# Prepend .tools/ so ffmpeg/ffprobe/mc resolve to our static binaries first.
export PATH := $(TOOLS_DIR):$(PATH)

## help: list available targets
.PHONY: help
help:
	@printf "LivePulse dev targets:\n\n"
	@grep -E '^## [a-z_-]+:' $(MAKEFILE_LIST) | sed -e 's/^## /  make /' -e 's/: / — /'
	@printf "\nCommon flows:\n  make bootstrap       # fresh-machine setup\n  make dev             # backend + frontend together\n  make backup          # snapshot DB + MinIO to _data/\n"

# ----- one-shot setup -----

## bootstrap: full environment setup (idempotent — safe to re-run)
.PHONY: bootstrap
bootstrap:
	@bash scripts/bootstrap_dev.sh

## tools: download static ffmpeg/ffprobe/mc into .tools/
.PHONY: tools
tools:
	@bash scripts/install_tools.sh

# ----- containers -----

## up: start postgres + minio + redis containers
.PHONY: up
up: redis-up
	@docker ps --format '{{.Names}}' | grep -q '^$(PG_CONTAINER)$$' \
	  || docker start $(PG_CONTAINER) 2>/dev/null \
	  || docker run -d --name $(PG_CONTAINER) \
	       -e POSTGRES_USER=trader \
	       -e POSTGRES_PASSWORD=dev_trading_pw_2026 \
	       -e POSTGRES_DB=livestream_analyzer \
	       -p 5432:5432 postgres:15 >/dev/null
	@docker compose up -d >/dev/null
	@echo "Containers up: $(PG_CONTAINER), minio_livestream, $(REDIS_CONTAINER)"

## down: stop postgres + minio + redis containers (data preserved)
.PHONY: down
down:
	@docker stop $(PG_CONTAINER) >/dev/null 2>&1 || true
	@docker stop $(REDIS_CONTAINER) >/dev/null 2>&1 || true
	@docker compose stop >/dev/null
	@echo "Containers stopped"

## ps: show docker container status
.PHONY: ps
ps:
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'workbuddy|minio|NAMES' || true

# ----- redis (A6 Celery broker + result backend) -----
# 2026-06-03: added for A6 concurrency rollout. Redis is broker (queue)
# AND result backend (idempotent re-fetch of task state). No volume mount
# — Celery tasks are re-trigger-able by the upload endpoint, so losing
# in-flight queue on a Redis restart costs at most a re-trigger. M2 may
# add appendonly persistence if we move credits/billing through Redis.
## redis-up: start redis container (idempotent)
.PHONY: redis-up
redis-up:
	@docker ps --format '{{.Names}}' | grep -q '^$(REDIS_CONTAINER)$$' && exit 0 \
	  || docker start $(REDIS_CONTAINER) 2>/dev/null \
	  || docker run -d --name $(REDIS_CONTAINER) \
	       -p 6379:6379 redis:7-alpine >/dev/null
	@echo "redis up on :6379"

## redis-down: stop redis container
.PHONY: redis-down
redis-down:
	@docker stop $(REDIS_CONTAINER) >/dev/null 2>&1 || true
	@echo "redis stopped"

## redis-cli: open redis-cli shell in the redis container
.PHONY: redis-cli
redis-cli:
	@docker exec -it $(REDIS_CONTAINER) redis-cli

## redis-ping: verify redis is reachable
.PHONY: redis-ping
redis-ping:
	@docker exec $(REDIS_CONTAINER) redis-cli ping

# ----- data -----

## backup: dump PG (pg_dump -Fc) + mirror MinIO into _data/
.PHONY: backup
backup:
	@bash scripts/backup_db.sh

## restore: restore PG + MinIO from _data/ (skipped if already populated)
.PHONY: restore
restore:
	@bash scripts/restore_db.sh

## restore-force: drop DB and re-import from _data/ (destructive!)
.PHONY: restore-force
restore-force:
	@bash scripts/restore_db.sh --force

## psql: open psql shell in the postgres container
.PHONY: psql
psql:
	@docker exec -it $(PG_CONTAINER) psql -U trader -d livestream_analyzer

# ----- python -----

## venv: create .venv and install backend requirements
.PHONY: venv
venv:
	@test -d $(VENV) || python3.11 -m venv $(VENV)
	@$(VENV_PIP) install --quiet --upgrade pip
	@$(VENV_PIP) install --quiet -r backend/requirements.txt
	@echo "venv ready"

## migrate: alembic upgrade head
.PHONY: migrate
migrate:
	@cd backend && $(VENV_ALEMBIC) upgrade head

## test: run backend pytest (all suites)
.PHONY: test
test:
	@cd backend && $(VENV)/bin/pytest -x

## test-invariants: run the invariant harness (backend must be running)
.PHONY: test-invariants
test-invariants:
	@cd backend && $(VENV)/bin/pytest tests/api/test_invariants.py -v

# ----- run -----

## backend: start backend on port 8004 in the foreground
.PHONY: backend
backend: _stop-backend
	@cd backend && $(VENV_PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(BACKEND_PORT) --reload

## backend-bg: start backend in the background (logs to /tmp/uvicorn.log)
.PHONY: backend-bg
backend-bg: _stop-backend
	@cd backend && nohup $(VENV_PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(BACKEND_PORT) \
	  > /tmp/uvicorn.log 2>&1 & disown
	@echo "backend started (PID $$!), log: /tmp/uvicorn.log"

## backend-restart: deterministic restart (lsof kill + clear pyc + boot_id verify)
.PHONY: backend-restart
backend-restart:
	@bash scripts/runtime/restart_backend.sh

## frontend: start vite dev server on port 3001
.PHONY: frontend
frontend:
	@cd frontend && npm run dev

## dev: run backend in background, frontend in foreground (Ctrl-C kills both)
.PHONY: dev
dev: backend-bg
	@trap 'echo; echo "Stopping backend..."; pkill -f "uvicorn app.main" 2>/dev/null || true' EXIT; \
	  cd frontend && npm run dev

## stop: kill the running backend (frontend stops with Ctrl-C in its own tab)
.PHONY: stop
stop: _stop-backend
	@echo "Backend stopped"

# Internal — used by backend / backend-bg / stop to guarantee a single
# uvicorn instance. The 2026-05-27 incident showed that overlapping
# uvicorn workers serving from different code versions produce silently
# wrong analyses. Kill before start, every time.
.PHONY: _stop-backend
_stop-backend:
	@pkill -f "uvicorn app.main" 2>/dev/null || true
	@sleep 1

# ----- celery worker (A6 — 2026-06-03) -----
# Concurrency=2 chosen for laptop dev (each worker loads whisper +
# sentence-transformers ≈ 2 GB RAM). Bump to 4 in A3 prod (2c4g ECS still
# tight; would want larger box for >2). worker_prefetch_multiplier=1 is
# set in pipeline._get_celery_app so long tasks don't starve short ones.
WORKER_CONCURRENCY ?= 2

# macOS fork-safety workaround: ML libraries (paddle, sentence-transformers)
# load Objective-C runtimes that abort with SIGABRT when fork()ed in
# multi-process Celery pool. Setting OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
# tells the runtime to allow it. Harmless on Linux (env var is ignored).
# This is a known issue documented by Apple / Celery / PyTorch — not specific
# to our code.
WORKER_ENV := OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

## worker: start celery worker in foreground (Ctrl-C to stop)
.PHONY: worker
worker: _stop-worker
	@cd backend && $(WORKER_ENV) $(VENV)/bin/celery -A app.tasks worker \
	  --loglevel=info --concurrency=$(WORKER_CONCURRENCY)

## worker-bg: start celery worker in background (logs to /tmp/celery.log)
.PHONY: worker-bg
worker-bg: _stop-worker
	@cd backend && $(WORKER_ENV) nohup $(VENV)/bin/celery -A app.tasks worker \
	  --loglevel=info --concurrency=$(WORKER_CONCURRENCY) \
	  > /tmp/celery.log 2>&1 & disown
	@echo "celery worker started, log: /tmp/celery.log (concurrency=$(WORKER_CONCURRENCY))"

## worker-stop: kill the running celery worker
.PHONY: worker-stop
worker-stop: _stop-worker
	@echo "celery worker stopped"

## worker-logs: tail celery worker log
.PHONY: worker-logs
worker-logs:
	@tail -f /tmp/celery.log

# Internal — kill any running celery worker before start.
# Same rationale as _stop-backend: overlapping workers serving
# from different code versions = silently wrong analyses.
.PHONY: _stop-worker
_stop-worker:
	@pkill -f "celery -A app.tasks worker" 2>/dev/null || true
	@sleep 1

# ----- diagnostics -----

## smoke: hit /health and /sessions to verify backend works end-to-end
.PHONY: smoke
smoke:
	@test -n "$(API_KEY)" || (echo "API_KEY missing from backend/.env" && exit 1)
	@printf "/health:\n  "; curl -sf -H "X-API-Key: $(API_KEY)" http://127.0.0.1:$(BACKEND_PORT)/api/v1/health \
	  || (echo "  FAIL — is the backend running? Try 'make backend-bg'" && exit 1)
	@printf "\n/sessions total: "; curl -sf -H "X-API-Key: $(API_KEY)" \
	  "http://127.0.0.1:$(BACKEND_PORT)/api/v1/sessions/?limit=1" \
	  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('data',{}).get('total','?'))"

## logs: tail backend uvicorn log
.PHONY: logs
logs:
	@tail -f /tmp/uvicorn.log

## pipeline-logs: tail business pipeline log (analysis tracing)
.PHONY: pipeline-logs
pipeline-logs:
	@tail -f backend/logs/pipeline.log

# ----- cleanup -----

## clean-pyc: remove __pycache__ + .pyc files
.PHONY: clean-pyc
clean-pyc:
	@find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find backend -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "pyc cleaned"

## clean-venv: nuke .venv (next `make venv` rebuilds it)
.PHONY: clean-venv
clean-venv:
	@rm -rf $(VENV)
	@echo ".venv removed"

## clean-frontend: nuke node_modules
.PHONY: clean-frontend
clean-frontend:
	@rm -rf frontend/node_modules
	@echo "node_modules removed"
