#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Autoflow – Backup Script
# Usage: ./scripts/backup.sh [backup_dir]
#
# Creates a timestamped backup of:
#   • PostgreSQL database (pg_dump → AES-256-CBC chiffré si BACKUP_ENCRYPTION_KEY défini)
#   • Redis AOF/RDB data (volume snapshot via container copy)
#
# Chiffrement : définir BACKUP_ENCRYPTION_KEY dans .env (min 32 chars)
#   Si absent → backup non chiffré avec avertissement.
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
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"

# ── Helpers ───────────────────────────────────────────────────
log()  { echo "[$(date +%T)] $*"; }
warn() { echo "[WARN]  $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

check_container() {
  docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null | grep -q true \
    || die "Container '$1' is not running. Start the stack with: make start"
}

# Chiffre stdin vers un fichier .enc si BACKUP_ENCRYPTION_KEY est défini,
# sinon écrit le flux tel quel vers le fichier cible.
encrypt_or_write() {
  local dest="$1"
  if [[ -n "${BACKUP_ENCRYPTION_KEY}" ]]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \
      -pass "env:BACKUP_ENCRYPTION_KEY" \
      -out "${dest}.enc"
    echo "${dest}.enc"
  else
    cat > "${dest}"
    echo "${dest}"
  fi
}

# ── Pre-flight ────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker is not installed."
command -v openssl >/dev/null 2>&1 || die "openssl is not installed."

if [[ -z "${BACKUP_ENCRYPTION_KEY}" ]]; then
  warn "BACKUP_ENCRYPTION_KEY non défini — backup non chiffré."
  warn "Définir dans .env: BACKUP_ENCRYPTION_KEY=\$(python3 -c \"import secrets; print(secrets.token_hex(32))\")"
  ENCRYPTED=false
else
  ENCRYPTED=true
fi

log "Checking containers..."
check_container "${POSTGRES_CONTAINER}"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
log "Backup destination: ${BACKUP_DIR}"

# ── PostgreSQL ────────────────────────────────────────────────
log "Dumping PostgreSQL database '${POSTGRES_DB}'..."
PG_DEST="${BACKUP_DIR}/postgres_${POSTGRES_DB}.dump"

docker exec "${POSTGRES_CONTAINER}" \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --format=custom --compress=9 \
  | encrypt_or_write "${PG_DEST}" > /dev/null

if [[ "${ENCRYPTED}" == true ]]; then
  FINAL_FILE="${PG_DEST}.enc"
else
  FINAL_FILE="${PG_DEST}"
fi
chmod 600 "${FINAL_FILE}"
PG_SIZE=$(du -sh "${FINAL_FILE}" | cut -f1)
log "PostgreSQL dump complete (${PG_SIZE}): $(basename "${FINAL_FILE}")"

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
  chmod -R 600 "${BACKUP_DIR}/redis/"
  REDIS_SIZE=$(du -sh "${BACKUP_DIR}/redis/" | cut -f1)
  log "Redis backup complete (${REDIS_SIZE}): redis/"
else
  log "Redis container not running – skipping Redis backup."
fi

# ── Manifest ──────────────────────────────────────────────────
cat > "${BACKUP_DIR}/MANIFEST.txt" <<EOF
Autoflow Backup
===============
Timestamp  : ${TIMESTAMP}
Host       : $(hostname)
Stack dir  : ${ROOT_DIR}
Encrypted  : ${ENCRYPTED}

Contents
--------
$(basename "${FINAL_FILE}")  – PostgreSQL custom-format dump$([ "${ENCRYPTED}" = true ] && echo " (AES-256-CBC)")
redis/                       – Redis data directory snapshot (AOF + RDB)

Restore
-------
  ./scripts/restore.sh ${BACKUP_DIR}
$([ "${ENCRYPTED}" = true ] && echo "  BACKUP_ENCRYPTION_KEY requis dans l'environnement pour déchiffrer.")
EOF

log "──────────────────────────────────────"
log "Backup complete: ${BACKUP_DIR}"
log "To restore: ./scripts/restore.sh ${BACKUP_DIR}"
