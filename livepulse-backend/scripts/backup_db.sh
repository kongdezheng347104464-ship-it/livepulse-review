#!/usr/bin/env bash
# Dump PostgreSQL + MinIO into _data/ for archival or cross-machine transport.
#
# Why pg_dump -Fc (custom binary) instead of -F p (plain SQL):
#   The 2026-05-28 Mac restore failed because the Windows-side dump was
#   produced by PowerShell redirection, which UTF-16-LE encodes the SQL
#   text. psql refuses UTF-16. Custom binary dumps have no such ambiguity,
#   are smaller, and allow selective restore via pg_restore --table.
#
# Layout produced under _data/:
#   workbuddy_db.dump           pg_dump -Fc archive
#   minio_export/*.mp4          object store contents

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

if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_BLUE=$'\033[0;34m'; C_RED=$'\033[0;31m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_BLUE=""; C_RED=""; C_RESET=""
fi
info() { printf "%s[backup]%s %s\n" "$C_BLUE" "$C_RESET" "$1"; }
ok()   { printf "%s[backup]%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
fail() { printf "%s[backup]%s %s\n" "$C_RED" "$C_RESET" "$1"; exit 1; }

mkdir -p "$DATA_DIR/minio_export"

# --- PG dump ---
if ! docker exec "$PG_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
  fail "Postgres container '$PG_CONTAINER' not ready. Start it with 'make up'."
fi

DUMP_PATH="$DATA_DIR/workbuddy_db.dump"
info "Dumping $PG_DB to $DUMP_PATH (pg_dump -Fc) ..."
# Run pg_dump *inside* the container so the binary version matches the
# server. -Fc = custom binary archive, the only format that's safe across
# OS / shell / encoding combinations.
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc -Z 6 > "$DUMP_PATH"
ok "PG dump: $(du -h "$DUMP_PATH" | cut -f1)"

# --- MinIO mirror ---
if [[ -z "$MC_BIN" ]]; then
  fail "mc not found. Run 'make tools' first."
fi
info "Mirroring $MC_ALIAS/$MC_BUCKET to $DATA_DIR/minio_export ..."
"$MC_BIN" mirror --overwrite --remove "$MC_ALIAS/$MC_BUCKET" "$DATA_DIR/minio_export" \
  | tail -5
OBJECT_COUNT=$(find "$DATA_DIR/minio_export" -type f | wc -l | tr -d ' ')
TOTAL_SIZE=$(du -sh "$DATA_DIR/minio_export" | cut -f1)
ok "MinIO mirror: $OBJECT_COUNT objects, $TOTAL_SIZE"

ok "Backup complete in $DATA_DIR/"
