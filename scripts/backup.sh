#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Autoflow – Backup Script
# Usage: ./scripts/backup.sh [backup_dir]
#
# Creates a timestamped backup of:
#   • PostgreSQL database (pg_dump → compressed SQL)
#   • Redis AOF/RDB data (volume snapshot via container copy)
#
# Output: ./backups/YYYY-MM-DD_HH-MM-SS/
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Config ────────────────────────────────────────────────────
BACKUP_BASE="${1:-${ROOT_DIR}/backups}"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
BACKUP_DIR="${BACKUP_BASE}/${TIMESTAMP}"

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-autoflow_postgres}"
REDIS_CONTAINER="${REDIS_CONTAINER:-autoflow_redis}"

# Load .env if it exists (for POSTGRES_* and REDIS_* vars)
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
    || die "Container '$1' is not running. Start the stack with: make start"
}

# ── Pre-flight ────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker is not installed."

log "Checking containers..."
check_container "${POSTGRES_CONTAINER}"

mkdir -p "${BACKUP_DIR}"
log "Backup destination: ${BACKUP_DIR}"

# ── PostgreSQL ────────────────────────────────────────────────
log "Dumping PostgreSQL database '${POSTGRES_DB}'..."
docker exec "${POSTGRES_CONTAINER}" \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --format=custom --compress=9 \
  > "${BACKUP_DIR}/postgres_${POSTGRES_DB}.dump"

PG_SIZE=$(du -sh "${BACKUP_DIR}/postgres_${POSTGRES_DB}.dump" | cut -f1)
log "PostgreSQL dump complete (${PG_SIZE}): postgres_${POSTGRES_DB}.dump"

# ── Redis ─────────────────────────────────────────────────────
if docker inspect --format '{{.State.Running}}' "${REDIS_CONTAINER}" 2>/dev/null | grep -q true; then
  log "Triggering Redis BGSAVE..."
  docker exec "${REDIS_CONTAINER}" \
    redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning BGSAVE >/dev/null 2>&1 || true

  # Wait for save to finish (up to 30s)
  for i in $(seq 1 30); do
    SAVING=$(docker exec "${REDIS_CONTAINER}" \
      redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning INFO persistence 2>/dev/null \
      | grep rdb_bgsave_in_progress | tr -d '[:space:]' | cut -d: -f2)
    [[ "${SAVING}" == "0" ]] && break
    sleep 1
  done

  log "Copying Redis data directory..."
  docker cp "${REDIS_CONTAINER}:/data/." "${BACKUP_DIR}/redis/"
  REDIS_SIZE=$(du -sh "${BACKUP_DIR}/redis/" | cut -f1)
  log "Redis backup complete (${REDIS_SIZE}): redis/"
else
  log "Redis container not running – skipping Redis backup."
fi

# ── Manifest ──────────────────────────────────────────────────
cat > "${BACKUP_DIR}/MANIFEST.txt" <<EOF
Autoflow Backup
===============
Timestamp : ${TIMESTAMP}
Host      : $(hostname)
Stack dir : ${ROOT_DIR}

Contents
--------
postgres_${POSTGRES_DB}.dump  – PostgreSQL custom-format dump
redis/                        – Redis data directory snapshot (AOF + RDB)

Restore
-------
  ./scripts/restore.sh ${BACKUP_DIR}
EOF

log "──────────────────────────────────────"
log "Backup complete: ${BACKUP_DIR}"
log "To restore: ./scripts/restore.sh ${BACKUP_DIR}"
