#!/usr/bin/env bash
# One-shot bootstrap for a fresh dev machine.
#
# Idempotent: safe to re-run; each step skips if already done. The goal is
# that any new machine (or new collaborator) can go from `git clone` to a
# working backend serving /health=ok by running this single script.
#
# Steps (each fully self-contained, see scripts/ for the heavy lifting):
#   1. System-dependency check (python3.11, node, docker)
#   2. Static tools (ffmpeg, ffprobe, mc) into .tools/
#   3. Start docker containers (postgres + minio)
#   4. Restore DB + MinIO from _data/
#   5. Python venv + pip install
#   6. Alembic upgrade
#   7. Frontend npm install
#   8. Smoke test (start backend, hit /health, stop backend)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export PATH="$PROJECT_ROOT/.tools:$PATH"

if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_BLUE=$'\033[0;34m'; C_RED=$'\033[0;31m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RED=""; C_BOLD=""; C_RESET=""
fi
step() { printf "\n%s━━━ [%s] %s%s\n" "$C_BOLD" "$1" "$2" "$C_RESET"; }
ok()   { printf "%s✓%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
warn() { printf "%s!%s %s\n" "$C_YELLOW" "$C_RESET" "$1"; }
fail() { printf "%s✗%s %s\n" "$C_RED" "$C_RESET" "$1"; exit 1; }

# ============================================================
step "1/8" "System dependencies"
# ============================================================
require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "$1 not found. $2"
  fi
  ok "$1: $($1 --version 2>&1 | head -1 | cut -c1-70)"
}
require_cmd python3.11 "Install with: brew install python@3.11"
require_cmd node       "Install with: brew install node"
require_cmd docker     "Install Docker Desktop from https://docker.com"
if ! docker info >/dev/null 2>&1; then
  fail "Docker daemon not running. Start Docker Desktop."
fi
ok "docker daemon running"

# ============================================================
step "2/8" "Static tools (ffmpeg, ffprobe, mc)"
# ============================================================
bash "$PROJECT_ROOT/scripts/install_tools.sh"

# ============================================================
step "3/8" "Docker containers (postgres + minio)"
# ============================================================
# Postgres: started outside docker-compose so it can be reused independently
# (compose only manages minio per docker-compose.yml comment).
if docker ps --format '{{.Names}}' | grep -q '^workbuddy_postgres$'; then
  ok "workbuddy_postgres already running"
elif docker ps -a --format '{{.Names}}' | grep -q '^workbuddy_postgres$'; then
  docker start workbuddy_postgres >/dev/null
  ok "workbuddy_postgres restarted"
else
  docker run -d --name workbuddy_postgres \
    -e POSTGRES_USER=trader \
    -e POSTGRES_PASSWORD=dev_trading_pw_2026 \
    -e POSTGRES_DB=livestream_analyzer \
    -p 5432:5432 \
    postgres:15 >/dev/null
  ok "workbuddy_postgres created"
fi

# MinIO via docker-compose
if docker ps --format '{{.Names}}' | grep -q '^minio_livestream$'; then
  ok "minio_livestream already running"
else
  (cd "$PROJECT_ROOT" && docker compose up -d) >/dev/null
  ok "minio_livestream started via docker compose"
fi

# ============================================================
step "4/8" "Restore DB + MinIO from _data/"
# ============================================================
bash "$PROJECT_ROOT/scripts/restore_db.sh"

# ============================================================
step "5/8" "Python venv + dependencies"
# ============================================================
if [[ ! -d "$PROJECT_ROOT/.venv" ]]; then
  python3.11 -m venv "$PROJECT_ROOT/.venv"
  ok ".venv created"
fi
PIP="$PROJECT_ROOT/.venv/bin/pip"
"$PIP" install --quiet --upgrade pip
# Use pip's built-in idempotency — it'll skip already-satisfied packages.
"$PIP" install --quiet -r "$PROJECT_ROOT/backend/requirements.txt"
ok "Python dependencies installed"

# ============================================================
step "6/8" "Alembic upgrade"
# ============================================================
(cd "$PROJECT_ROOT/backend" && "$PROJECT_ROOT/.venv/bin/alembic" upgrade head 2>&1 | tail -3)
ok "Database schema at head"

# ============================================================
step "7/8" "Frontend dependencies"
# ============================================================
if [[ ! -d "$PROJECT_ROOT/frontend_v2/node_modules" ]]; then
  (cd "$PROJECT_ROOT/frontend_v2" && npm install --silent --legacy-peer-deps)
  ok "node_modules installed"
else
  ok "node_modules already present"
fi

# ============================================================
step "8/8" "Smoke test"
# ============================================================
# Kill any leftover uvicorn so we don't spin up a duplicate that fights for
# port 8004 — and so the smoke test's PID is the one we actually probe.
pkill -f "uvicorn app.main" 2>/dev/null || true
sleep 1

UVICORN_LOG="/tmp/workbuddy_bootstrap_uvicorn.log"
(cd "$PROJECT_ROOT/backend" && nohup "$PROJECT_ROOT/.venv/bin/python" \
  -m uvicorn app.main:app --host 127.0.0.1 --port 8004 \
  > "$UVICORN_LOG" 2>&1 &)
# No `disown` here — the subshell exits immediately after launching the
# background process, which already detaches it from this script's job
# table. A literal `disown` in the outer shell would error with
# "current: no such job" since there's nothing to disown at this level.

# Read API_KEY from backend/.env so the smoke probe can authenticate without
# us hardcoding (and leaking) secrets in this script.
API_KEY=$(grep -E '^API_KEY=' "$PROJECT_ROOT/backend/.env" | head -1 | cut -d= -f2-)
if [[ -z "$API_KEY" ]]; then
  fail "API_KEY missing from backend/.env"
fi

# Wait for boot, but bound it — uvicorn lifespan does session recovery on
# startup which can take a few seconds.
ATTEMPTS=0
until curl -sf -H "X-API-Key: $API_KEY" http://127.0.0.1:8004/api/v1/health >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [[ $ATTEMPTS -gt 30 ]]; then
    warn "Backend didn't respond in 30s. Tail of $UVICORN_LOG:"
    tail -20 "$UVICORN_LOG"
    fail "Smoke test failed at /health"
  fi
  sleep 1
done

HEALTH=$(curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8004/api/v1/health)
SESS_COUNT=$(curl -s -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8004/api/v1/sessions/?limit=1" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('data',{}).get('total','?'))")
ok "/health → $HEALTH"
ok "/sessions total = $SESS_COUNT"

echo
printf "%s━━━ Bootstrap complete ━━━%s\n" "$C_BOLD" "$C_RESET"
echo "Backend is running on http://127.0.0.1:8004 (log: $UVICORN_LOG)"
echo "Start the frontend with: make frontend"
echo "Stop the backend with:   make stop"
