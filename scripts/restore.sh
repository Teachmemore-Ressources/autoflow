#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Autoflow – Restore Script
# Usage: ./scripts/restore.sh <backup_dir>
#
# Restores PostgreSQL (and optionally Redis) from a backup
# created by scripts/backup.sh.
#
# WARNING: This will DROP and recreate the target database.
#          All current data will be lost.
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Args ──────────────────────────────────────────────────────
BACKUP_DIR="${1:-}"
if [[ -z "${BACKUP_DIR}" ]]; then
  echo "Usage: $0 <backup_dir>"
  echo ""
  echo "Available backups:"
  ls -1t "${ROOT_DIR}/backups/" 2>/dev/null | head -20 | sed 's/^/  /'
  exit 1
fi

# Resolve relative paths
BACKUP_DIR="$(cd "${BACKUP_DIR}" && pwd)"

# ── Config ────────────────────────────────────────────────────
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-autoflow_postgres}"
REDIS_CONTAINER="${REDIS_CONTAINER:-autoflow_redis}"

# Load .env
ENV_FILE="${ROOT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -o allexport && source "${ENV_FILE}" && set +o allexport
fi

POSTGRES_USER="${POSTGRES_USER:-awx}"
POSTGRES_DB="${POSTGRES_DB:-awx}"

# ── Helpers ───────────────────────────────────────────────────
log()  { echo "[$(date +%T)] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

check_container() {
  docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null | grep -q true \
    || die "Container '$1' is not running."
}

# ── Pre-flight ────────────────────────────────────────────────
command -v docker >/dev/null 2>&1  || die "docker is not installed."
[[ -d "${BACKUP_DIR}" ]]           || die "Backup directory not found: ${BACKUP_DIR}"

PG_DUMP="${BACKUP_DIR}/postgres_${POSTGRES_DB}.dump"
[[ -f "${PG_DUMP}" ]] || die "PostgreSQL dump not found: ${PG_DUMP}"

log "Backup directory : ${BACKUP_DIR}"
if [[ -f "${BACKUP_DIR}/MANIFEST.txt" ]]; then
  echo ""
  cat "${BACKUP_DIR}/MANIFEST.txt"
  echo ""
fi

# ── Confirmation ──────────────────────────────────────────────
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  WARNING: This will DROP the '${POSTGRES_DB}' database  │"
echo "  │  and replace it with the backup above.              │"
echo "  │  ALL CURRENT DATA WILL BE LOST.                     │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
read -r -p "  Type 'yes' to continue: " CONFIRM
[[ "${CONFIRM}" == "yes" ]] || { log "Aborted."; exit 0; }

# ── Stop dependent services ───────────────────────────────────
log "Stopping services that use the database..."
cd "${ROOT_DIR}"
docker compose stop api awx_task awx_web 2>/dev/null || true
log "Waiting for containers to stop..."
sleep 3

# ── Restore PostgreSQL ────────────────────────────────────────
check_container "${POSTGRES_CONTAINER}"

log "Dropping and recreating database '${POSTGRES_DB}'..."
docker exec "${POSTGRES_CONTAINER}" \
  psql -U "${POSTGRES_USER}" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB}' AND pid <> pg_backend_pid();" \
  >/dev/null 2>&1 || true

docker exec "${POSTGRES_CONTAINER}" \
  psql -U "${POSTGRES_USER}" -d postgres \
  -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\";" \
  >/dev/null

docker exec "${POSTGRES_CONTAINER}" \
  psql -U "${POSTGRES_USER}" -d postgres \
  -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";" \
  >/dev/null

log "Restoring PostgreSQL from dump..."
docker exec -i "${POSTGRES_CONTAINER}" \
  pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --no-owner --role="${POSTGRES_USER}" \
  < "${PG_DUMP}"

log "PostgreSQL restore complete."

# ── Restore Redis (optional) ──────────────────────────────────
REDIS_BACKUP="${BACKUP_DIR}/redis"
if [[ -d "${REDIS_BACKUP}" ]] && \
   docker inspect --format '{{.State.Running}}' "${REDIS_CONTAINER}" 2>/dev/null | grep -q true; then
  log "Restoring Redis data..."
  docker compose stop redis 2>/dev/null || true
  sleep 2
  # Copy backup files into the volume via a temporary container
  docker run --rm \
    -v autoflow_redis_data:/data \
    -v "${REDIS_BACKUP}":/backup:ro \
    alpine sh -c "rm -rf /data/* && cp -a /backup/. /data/"
  docker compose start redis
  log "Redis restore complete."
else
  log "No Redis backup found or container not running – skipping Redis restore."
fi

# ── Restart services ──────────────────────────────────────────
log "Restarting services..."
docker compose start awx_web awx_task api 2>/dev/null || \
  log "Note: some services may need a full 'make start' if they were not running before."

log "──────────────────────────────────────"
log "Restore complete from: ${BACKUP_DIR}"
log "Run 'make status' to verify all services are healthy."
