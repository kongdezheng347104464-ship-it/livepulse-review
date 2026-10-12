#!/usr/bin/env bash
# Restore PostgreSQL + MinIO from _data/.
#
# Prefers the canonical pg_dump -Fc archive (_data/workbuddy_db.dump). Falls
# back to the legacy text dump (_data/workbuddy_db.sql), auto-converting
# UTF-16-LE → UTF-8 if necessary (Windows pg_dump via PowerShell emits
# UTF-16-LE BOM, which psql cannot read). The legacy path exists only for
# the one-shot Windows → Mac migration; new dumps should always be .dump.
#
# Idempotent-ish: if the target DB already has the `live_sessions` table
# with rows, restore is skipped unless --force is passed.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$(cd "$PROJECT_ROOT/.." && pwd)/_data"
PG_CONTAINER="${PG_CONTAINER:-workbuddy_postgres}"
PG_USER="${PG_USER:-trader}"
PG_DB="${PG_DB:-livestream_analyzer}"
MC_ALIAS="${MC_ALIAS:-local}"
MC_BUCKET="${MC_BUCKET:-livestream-videos}"
MC_BIN="$PROJECT_ROOT/.tools/mc"
[[ -x "$MC_BIN" ]] || MC_BIN="$(command -v mc || true)"

FORCE=false
[[ "${1:-}" == "--force" ]] && FORCE=true

if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_BLUE=$'\033[0;34m'; C_RED=$'\033[0;31m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RED=""; C_RESET=""
fi
info() { printf "%s[restore]%s %s\n" "$C_BLUE" "$C_RESET" "$1"; }
ok()   { printf "%s[restore]%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
skip() { printf "%s[restore]%s %s\n" "$C_YELLOW" "$C_RESET" "$1"; }
fail() { printf "%s[restore]%s %s\n" "$C_RED" "$C_RESET" "$1"; exit 1; }

# --- Wait for PG ---
info "Waiting for $PG_CONTAINER ..."
ATTEMPTS=0
until docker exec "$PG_CONTAINER" pg_isready -U "$PG_USER" >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [[ $ATTEMPTS -gt 60 ]]; then
    fail "Postgres '$PG_CONTAINER' didn't become ready in 60s. Is it running?"
  fi
  sleep 1
done
ok "Postgres ready"

# --- Check if already populated ---
EXISTING_ROWS=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tA -c \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='live_sessions';" 2>/dev/null || echo 0)
if [[ "$EXISTING_ROWS" -gt 0 ]] && ! $FORCE; then
  SESSION_CNT=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tA -c \
    "SELECT COUNT(*) FROM live_sessions;" 2>/dev/null | tr -d ' ')
  skip "live_sessions already exists with $SESSION_CNT rows — skipping PG restore (use --force to override)"
else
  if $FORCE; then
    info "Dropping & recreating database $PG_DB (--force) ..."
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c \
      "DROP DATABASE IF EXISTS $PG_DB;" >/dev/null
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c \
      "CREATE DATABASE $PG_DB;" >/dev/null
  fi

  # Pick source: prefer .dump (binary), fall back to .sql (text/UTF-16).
  if [[ -f "$DATA_DIR/workbuddy_db.dump" ]]; then
    info "Restoring from $DATA_DIR/workbuddy_db.dump (pg_restore) ..."
    docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$PG_DB" \
      --no-owner --no-privileges < "$DATA_DIR/workbuddy_db.dump"
  elif [[ -f "$DATA_DIR/workbuddy_db.sql" ]]; then
    info "No .dump found — falling back to legacy .sql (will auto-convert encoding)"
    TMP_SQL=$(mktemp)
    # Detect UTF-16-LE BOM (FF FE at offset 0). If present, transcode to UTF-8
    # and strip CRLF; otherwise pass through unchanged.
    if head -c 2 "$DATA_DIR/workbuddy_db.sql" | xxd -p | grep -qi '^fffe'; then
      info "Detected UTF-16-LE BOM — converting to UTF-8"
      iconv -f UTF-16LE -t UTF-8 "$DATA_DIR/workbuddy_db.sql" | tr -d '\r' \
        | sed '1s/^\xEF\xBB\xBF//' > "$TMP_SQL"
    else
      cp "$DATA_DIR/workbuddy_db.sql" "$TMP_SQL"
    fi
    docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" \
      --quiet --set ON_ERROR_STOP=on < "$TMP_SQL" >/dev/null
    rm -f "$TMP_SQL"
  else
    fail "Neither $DATA_DIR/workbuddy_db.dump nor _data/workbuddy_db.sql found"
  fi

  SESSION_CNT=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tA -c \
    "SELECT COUNT(*) FROM live_sessions;" | tr -d ' ')
  ok "PG restore: $SESSION_CNT sessions loaded"
fi

# --- MinIO restore ---
if [[ -z "$MC_BIN" ]]; then
  fail "mc not found. Run 'make tools' first."
fi
info "Waiting for MinIO ..."
ATTEMPTS=0
until curl -sf http://localhost:9002/minio/health/live >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [[ $ATTEMPTS -gt 60 ]]; then
    fail "MinIO didn't become ready in 60s"
  fi
  sleep 1
done
ok "MinIO ready"

"$MC_BIN" alias set "$MC_ALIAS" http://localhost:9002 minioadmin minioadmin123 >/dev/null 2>&1
"$MC_BIN" mb --ignore-existing "$MC_ALIAS/$MC_BUCKET" >/dev/null

EXISTING=$("$MC_BIN" ls "$MC_ALIAS/$MC_BUCKET" 2>/dev/null | wc -l | tr -d ' ')
SOURCE_COUNT=$(find "$DATA_DIR/minio_export" -type f 2>/dev/null | wc -l | tr -d ' ')
if [[ "$EXISTING" -ge "$SOURCE_COUNT" ]] && ! $FORCE && [[ "$SOURCE_COUNT" -gt 0 ]]; then
  skip "MinIO bucket already has $EXISTING objects (source has $SOURCE_COUNT) — skipping (use --force to re-upload)"
else
  if [[ "$SOURCE_COUNT" -eq 0 ]]; then
    skip "No files in $DATA_DIR/minio_export — nothing to upload"
  else
    info "Uploading $SOURCE_COUNT objects from $DATA_DIR/minio_export ..."
    "$MC_BIN" mirror --overwrite "$DATA_DIR/minio_export" "$MC_ALIAS/$MC_BUCKET" \
      | tail -3
    ok "MinIO mirror complete"
  fi
fi
