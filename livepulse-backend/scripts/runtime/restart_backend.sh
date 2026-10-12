#!/usr/bin/env bash
# Deterministic backend restart — runtime gatekeeper (Mac/Linux).
#
# The ONLY supported way to restart the backend during local dev. Solves
# the class of problem that caused the 2026-05-27 composite_score
# incident on Windows, where multiple uvicorn workers (some via system
# Python, some via venv, several holding stale bytecode) were all bound
# to port 8004 and serving requests from inconsistent code versions.
#
# Guarantees on successful exit (mirrors restart_backend.ps1):
#   1. No remaining process listening on port 8004, AND no orphan
#      python process matching `uvicorn.*app\.main` (other Python work
#      — Jupyter, IDE LSP, formatters — is NOT touched).
#   2. All __pycache__ / *.pyc files under backend/app are removed.
#   3. The new backend is launched via $PROJECT_ROOT/.venv/bin/python
#      explicitly — never `python` / `python3` from PATH.
#   4. By default --reload is NOT used (reload causes multi-worker
#      process trees that drift). Pass --dev-reload to opt in.
#   5. /api/v1/health is polled until runtime.boot_id differs from the
#      one captured before the restart. Until that happens the script
#      does not return successfully. This proves the new code is
#      actually serving traffic — not just "uvicorn says ready".
#
# If the new boot fingerprint doesn't appear within $TIMEOUT seconds,
# the script exits non-zero and prints the tail of backend.err.log.
#
# Usage:
#   ./scripts/runtime/restart_backend.sh                # deterministic
#   ./scripts/runtime/restart_backend.sh --dev-reload   # uvicorn --reload
#   ./scripts/runtime/restart_backend.sh --timeout 60   # slow model load

set -euo pipefail

# -------------------------------------------------------------------------
# Arg parsing — keep flags compatible with the PowerShell version's intent
# -------------------------------------------------------------------------
DEV_RELOAD=false
TIMEOUT=30
PORT=8004
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev-reload|-r) DEV_RELOAD=true; shift ;;
    --timeout|-t)    TIMEOUT="$2"; shift 2 ;;
    --port|-p)       PORT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown arg: $1 (try --help)" >&2; exit 1 ;;
  esac
done

# -------------------------------------------------------------------------
# Resolve paths from script location, not cwd — operators don't always
# invoke from the project root.
# -------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="$BACKEND_DIR/.env"
LOG_FILE="$PROJECT_ROOT/backend.log"
ERR_FILE="$PROJECT_ROOT/backend.err.log"

# Colors (only when stdout is a TTY)
if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_BLUE=$'\033[0;34m'
  C_RED=$'\033[0;31m';   C_GRAY=$'\033[0;90m';   C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RED=""; C_GRAY=""; C_RESET=""
fi
step() { printf "\n%s▶ %s%s\n" "$C_BLUE" "$1" "$C_RESET"; }
ok()   { printf "%s✅ %s%s\n" "$C_GREEN" "$1" "$C_RESET"; }
info() { printf "%sℹ️  %s%s\n" "$C_GRAY" "$1" "$C_RESET"; }
warn() { printf "%s⚠ %s%s\n" "$C_YELLOW" "$1" "$C_RESET"; }
fail() { printf "%s❌ %s%s\n" "$C_RED" "$1" "$C_RESET"; exit "${2:-1}"; }

# -------------------------------------------------------------------------
# Pre-flight: venv python and API_KEY must be present, else give up early.
# -------------------------------------------------------------------------
if [[ ! -x "$VENV_PY" ]]; then
  fail "venv python not found at $VENV_PY — run 'make venv' first" 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  fail "missing $ENV_FILE" 1
fi
API_KEY=$(grep -E '^API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]' || true)
if [[ -z "$API_KEY" ]]; then
  fail "API_KEY not set in $ENV_FILE" 1
fi

# Probe helpers — single source of truth so old / new boot_id reads stay
# consistent in shape.
fetch_health_field() {
  curl -sf -H "X-API-Key: $API_KEY" --max-time 3 \
    "http://127.0.0.1:$PORT/api/v1/health" 2>/dev/null \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('data',{}).get('runtime',{}).get('$1',''))" 2>/dev/null || true
}

# -------------------------------------------------------------------------
# Step 0: Capture current boot_id BEFORE killing anything. We compare the
# polled boot_id to this; absence (backend was down) is fine.
# -------------------------------------------------------------------------
OLD_BOOT_ID=$(fetch_health_field boot_id)
if [[ -n "$OLD_BOOT_ID" ]]; then
  info "Current backend boot_id=$OLD_BOOT_ID (will wait for change)"
else
  info "No backend currently responding — fresh start"
fi

# -------------------------------------------------------------------------
# Step 1: Precise kill — anything holding the target port, plus any
# orphan uvicorn whose port bind failed (so it isn't visible via lsof).
# We never kill unrelated Python processes.
# -------------------------------------------------------------------------
step "Stopping previous uvicorn workers on port $PORT..."

# 1a) Port holders. lsof -ti = "terse, IDs only".
PORT_PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
for pid in $PORT_PIDS; do
  cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "<gone>")
  if [[ "$cmd" == *"uvicorn"*"app.main"* ]]; then
    # NOTE: on macOS, `.venv/bin/python3.11` is a symlink to the Python.org
    # framework binary, so `ps` reports the *resolved* framework path and
    # the venv vs system distinction we made on Windows can't be inferred
    # from the command line alone. The authoritative is_venv check happens
    # post-restart via /_debug/runtime (Python knows itself accurately).
    printf "   kill PID=%s cmd=%.80s...\n" "$pid" "$cmd"
    kill "$pid" 2>/dev/null || true
  else
    warn "port $PORT held by non-uvicorn PID=$pid cmd=$cmd — leaving alone"
  fi
done

# 1b) Orphan uvicorn processes not currently bound (e.g. bind failed and
#     they're in a retry loop, or about to crash). pgrep -f matches the
#     full command line so we catch both venv and system pythons running
#     our app.main entrypoint.
for pid in $(pgrep -f "uvicorn.*app\.main" 2>/dev/null || true); do
  if ! grep -qw "$pid" <<<"$PORT_PIDS"; then
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "<gone>")
    printf "   kill PID=%s (orphan uvicorn) cmd=%.80s...\n" "$pid" "$cmd"
    kill "$pid" 2>/dev/null || true
  fi
done

# Give the kernel a beat to release the port + reap children.
sleep 2

# Belt-and-suspenders: if anything is still alive after SIGTERM, escalate.
STILL=$(lsof -ti :"$PORT" 2>/dev/null || true)
if [[ -n "$STILL" ]]; then
  warn "still bound after 2s — SIGKILL stragglers: $STILL"
  kill -9 $STILL 2>/dev/null || true
  sleep 1
fi

if [[ -z "$PORT_PIDS" ]] && [[ -z "$(pgrep -f 'uvicorn.*app\.main' 2>/dev/null || true)" ]]; then
  printf "   (no matching processes found)\n"
fi

# -------------------------------------------------------------------------
# Step 2: Clear .pyc caches under backend/app. Same rationale as Windows:
# stale .pyc shadowing on mtime jitter is rare but lethal to debug.
# -------------------------------------------------------------------------
step "Clearing .pyc caches..."
PYC_COUNT=$(find "$BACKEND_DIR/app" -type f -name "*.pyc" 2>/dev/null | wc -l | tr -d ' ')
PYCACHE_COUNT=$(find "$BACKEND_DIR/app" -type d -name __pycache__ 2>/dev/null | wc -l | tr -d ' ')
find "$BACKEND_DIR/app" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$BACKEND_DIR/app" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
printf "   removed %s .pyc files (%s __pycache__ dirs)\n" "$PYC_COUNT" "$PYCACHE_COUNT"

# -------------------------------------------------------------------------
# Step 3: Launch with venv python explicitly. WB_RELOAD_MODE is set BEFORE
# the spawn so the child inherits it and the fingerprint endpoint can
# surface non-deterministic mode.
# -------------------------------------------------------------------------
if $DEV_RELOAD; then mode_str="DEV-RELOAD (non-deterministic)"; else mode_str="DETERMINISTIC (no reload)"; fi
step "Starting backend in $mode_str mode..."
printf "   python = %s\n" "$VENV_PY"

# Reset log files so the boot fingerprint is the first thing in them when
# something later goes wrong and we need to grep.
: > "$LOG_FILE"
: > "$ERR_FILE"

WB_MODE="0"; $DEV_RELOAD && WB_MODE="1"
UVICORN_ARGS=(-m uvicorn app.main:app --host 127.0.0.1 --port "$PORT")
$DEV_RELOAD && UVICORN_ARGS+=(--reload)

# Subshell + & detaches cleanly; nohup keeps it alive after the script
# exits. The subshell exits immediately, so there's no job in the outer
# script to `disown` — see scripts/bootstrap_dev.sh for the same pattern.
(cd "$BACKEND_DIR" && \
  WB_RELOAD_MODE="$WB_MODE" nohup "$VENV_PY" "${UVICORN_ARGS[@]}" \
    > "$LOG_FILE" 2> "$ERR_FILE" &)

# Best-effort: grab the new PID for diagnostics. We can't use $! across
# the subshell boundary, so query lsof a moment later.
sleep 1
NEW_PID=$(lsof -ti :"$PORT" 2>/dev/null | head -1 || true)
printf "   launched PID=%s\n" "${NEW_PID:-<starting>}"

# -------------------------------------------------------------------------
# Step 4: Poll /health until boot_id changes (or appears, on cold start).
# This is the only signal that proves the new code is serving traffic.
# -------------------------------------------------------------------------
step "Polling /api/v1/health until new boot_id appears (timeout ${TIMEOUT}s)..."

start_ts=$(date +%s)
new_boot_id=""
attempts=0
while (( $(date +%s) - start_ts < TIMEOUT )); do
  attempts=$((attempts + 1))
  sleep 0.8
  candidate=$(fetch_health_field boot_id)
  if [[ -n "$candidate" && "$candidate" != "$OLD_BOOT_ID" ]]; then
    new_boot_id="$candidate"
    break
  fi
done

if [[ -z "$new_boot_id" ]]; then
  echo
  fail "Timed out waiting for new boot_id after $attempts attempts" 2
  echo
  echo "=== backend.err.log tail ==="
  tail -20 "$ERR_FILE" 2>/dev/null || echo "(empty)"
fi

# -------------------------------------------------------------------------
# Step 5: Pull the full fingerprint via the debug endpoint and print every
# field operators would want to verify. Same field set as the PowerShell
# version so anyone alternating machines sees a familiar report.
# -------------------------------------------------------------------------
elapsed=$(( $(date +%s) - start_ts ))
ok "New worker confirmed serving traffic (${elapsed}s, $attempts polls)"
echo

curl -sf -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:$PORT/api/v1/_debug/runtime" 2>/dev/null \
  | python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', {})
fields = ['boot_id', 'pid', 'python_executable', 'is_venv', 'python_version',
          'reload_mode', 'pipeline_hash', 'git_sha', 'started_at']
for k in fields:
    print(f'   {k:<15}: {d.get(k, \"?\")}')
is_venv = d.get('is_venv', False)
sys.exit(0 if is_venv else 3)
" || {
  rc=$?
  if [[ $rc -eq 3 ]]; then
    echo
    fail "New worker is NOT running from venv — runtime identity lock FAILED" 3
  fi
  fail "could not read /_debug/runtime after restart" 4
}
