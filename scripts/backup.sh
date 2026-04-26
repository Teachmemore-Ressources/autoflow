#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Autoflow – Backup Script
#
# Two modes, selected automatically:
#
#   Restic mode  (when BACKUP_RESTIC_PASSWORD is set)
#     • Deduplication + AES-256 encryption via Restic
#     • Backends: local, sftp, s3 (AWS/MinIO/Wasabi/Scaleway/OVH), b2
#     • GFS retention: daily/weekly/monthly/yearly
#     • Suitable for DR to a remote destination
#
#   Legacy mode  (fallback when BACKUP_RESTIC_PASSWORD is not set)
#     • Timestamped local directory
#     • pg_dump (custom format, gzip-9) + optional AES-256-CBC encryption
#     • Redis BGSAVE + docker cp
#
# Usage: ./scripts/backup.sh [legacy_backup_dir]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Load .env ─────────────────────────────────────────────────────────────────
ENV_FILE="${ROOT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -o allexport && source "${ENV_FILE}" && set +o allexport
fi

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-autoflow_postgres}"
REDIS_CONTAINER="${REDIS_CONTAINER:-autoflow_redis}"
POSTGRES_USER="${POSTGRES_USER:-awx}"
POSTGRES_DB="${POSTGRES_DB:-awx}"

BACKUP_RESTIC_PASSWORD="${BACKUP_RESTIC_PASSWORD:-}"
BACKUP_BACKEND="${BACKUP_BACKEND:-local}"
BACKUP_LOCAL_PATH="${BACKUP_LOCAL_PATH:-${ROOT_DIR}/backups/restic}"
BACKUP_S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-autoflow-backup}"
BACKUP_S3_ACCESS_KEY="${BACKUP_S3_ACCESS_KEY:-}"
BACKUP_S3_SECRET_KEY="${BACKUP_S3_SECRET_KEY:-}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-7}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-4}"
BACKUP_RETENTION_MONTHLY="${BACKUP_RETENTION_MONTHLY:-12}"
BACKUP_RETENTION_YEARLY="${BACKUP_RETENTION_YEARLY:-3}"
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date +%T)] $*"; }
warn() { echo "[WARN]  $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

check_container() {
  docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null | grep -q true \
    || die "Container '$1' is not running. Start the stack first."
}

# ─────────────────────────────────────────────────────────────────────────────
# RESTIC MODE
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "${BACKUP_RESTIC_PASSWORD}" ]]; then
  command -v restic >/dev/null 2>&1 \
    || die "restic is not installed. Install: https://restic.net or 'sudo apt install restic'"
  command -v docker >/dev/null 2>&1 \
    || die "docker is not installed."

  # ── Build Restic repo URL ──────────────────────────────────────────────────
  export RESTIC_PASSWORD="${BACKUP_RESTIC_PASSWORD}"

  case "${BACKUP_BACKEND}" in
    local)
      export RESTIC_REPOSITORY="${BACKUP_LOCAL_PATH}"
      ;;
    sftp)
      if [[ "${BACKUP_LOCAL_PATH}" == sftp:* ]]; then
        export RESTIC_REPOSITORY="${BACKUP_LOCAL_PATH}"
      else
        export RESTIC_REPOSITORY="sftp:${BACKUP_LOCAL_PATH}"
      fi
      ;;
    s3)
      export AWS_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY}"
      export AWS_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY}"
      if [[ -n "${BACKUP_S3_ENDPOINT}" ]]; then
        export RESTIC_REPOSITORY="s3:${BACKUP_S3_ENDPOINT%/}/${BACKUP_S3_BUCKET}"
      else
        export RESTIC_REPOSITORY="s3:s3.amazonaws.com/${BACKUP_S3_BUCKET}"
      fi
      ;;
    b2)
      export B2_ACCOUNT_ID="${BACKUP_S3_ACCESS_KEY}"
      export B2_ACCOUNT_KEY="${BACKUP_S3_SECRET_KEY}"
      export RESTIC_REPOSITORY="b2:${BACKUP_S3_BUCKET}:restic"
      ;;
    *)
      die "Unknown BACKUP_BACKEND: ${BACKUP_BACKEND}. Valid: local, sftp, s3, b2"
      ;;
  esac

  log "Backend  : ${BACKUP_BACKEND}"
  log "Repo     : ${RESTIC_REPOSITORY}"

  # ── Init repo if needed ────────────────────────────────────────────────────
  if ! restic snapshots --no-lock >/dev/null 2>&1; then
    log "Initializing Restic repository…"
    restic init
  fi

  # ── Stage data in temp dir ─────────────────────────────────────────────────
  STAGING="$(mktemp -d /tmp/autoflow-backup-XXXXXX)"
  trap 'rm -rf "${STAGING}"' EXIT

  # PostgreSQL
  log "Dumping PostgreSQL '${POSTGRES_DB}'…"
  check_container "${POSTGRES_CONTAINER}"
  docker exec "${POSTGRES_CONTAINER}" \
    pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --format=custom --compress=9 \
    > "${STAGING}/postgres_${POSTGRES_DB}.dump"
  PG_SIZE=$(du -sh "${STAGING}/postgres_${POSTGRES_DB}.dump" | cut -f1)
  log "  PostgreSQL dump: ${PG_SIZE}"

  # Redis
  if docker inspect --format '{{.State.Running}}' "${REDIS_CONTAINER}" 2>/dev/null | grep -q true; then
    log "Triggering Redis BGSAVE…"
    docker exec "${REDIS_CONTAINER}" \
      redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning BGSAVE >/dev/null 2>&1 || true
    for i in $(seq 1 30); do
      SAVING=$(docker exec "${REDIS_CONTAINER}" \
        redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning INFO persistence 2>/dev/null \
        | grep rdb_bgsave_in_progress | tr -d '[:space:]' | cut -d: -f2)
      [[ "${SAVING}" == "0" ]] && break
      sleep 1
    done
    mkdir -p "${STAGING}/redis"
    docker cp "${REDIS_CONTAINER}:/data/." "${STAGING}/redis/"
    REDIS_SIZE=$(du -sh "${STAGING}/redis/" | cut -f1)
    log "  Redis data: ${REDIS_SIZE}"
  else
    warn "Redis container not running — skipping Redis backup."
  fi

  # Config & certs
  [[ -f "${ENV_FILE}" ]] && cp "${ENV_FILE}" "${STAGING}/config.env"
  [[ -d "${ROOT_DIR}/traefik/certs" ]] && \
    cp -r "${ROOT_DIR}/traefik/certs" "${STAGING}/traefik-certs"
  [[ -d "${ROOT_DIR}/monitoring/grafana/provisioning" ]] && \
    cp -r "${ROOT_DIR}/monitoring/grafana/provisioning" "${STAGING}/grafana-provisioning"

  # ── Restic backup ──────────────────────────────────────────────────────────
  log "Running restic backup…"
  restic backup "${STAGING}" \
    --tag "autoflow" \
    --hostname "$(hostname)" \
    --verbose=1

  # ── Forget + prune with GFS retention ─────────────────────────────────────
  log "Applying retention policy (daily=${BACKUP_RETENTION_DAILY} weekly=${BACKUP_RETENTION_WEEKLY} monthly=${BACKUP_RETENTION_MONTHLY} yearly=${BACKUP_RETENTION_YEARLY})…"
  restic forget --prune \
    --keep-daily   "${BACKUP_RETENTION_DAILY}" \
    --keep-weekly  "${BACKUP_RETENTION_WEEKLY}" \
    --keep-monthly "${BACKUP_RETENTION_MONTHLY}" \
    --keep-yearly  "${BACKUP_RETENTION_YEARLY}" \
    --tag "autoflow"

  # ── Stats ──────────────────────────────────────────────────────────────────
  log "Repository stats:"
  restic stats --no-lock 2>/dev/null || true

  SNAP_ID=$(restic snapshots --json --last --no-lock 2>/dev/null \
    | python3 -c "import sys,json; s=json.load(sys.stdin); print(s[-1]['id'][:8] if s else 'n/a')" 2>/dev/null || echo "n/a")
  log "──────────────────────────────────────────────"
  log "Backup complete — snapshot: ${SNAP_ID}"
  log "To verify: restic check"
  log "To restore: restic restore latest --target /tmp/restore-test"
  exit 0
fi


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY MODE (no BACKUP_RESTIC_PASSWORD set)
# ─────────────────────────────────────────────────────────────────────────────
warn "BACKUP_RESTIC_PASSWORD not set — using legacy local backup mode."
warn "For production DR, set BACKUP_RESTIC_PASSWORD and a remote BACKUP_BACKEND."

BACKUP_BASE="${1:-${ROOT_DIR}/backups}"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
BACKUP_DIR="${BACKUP_BASE}/${TIMESTAMP}"

command -v docker  >/dev/null 2>&1 || die "docker is not installed."
command -v openssl >/dev/null 2>&1 || die "openssl is not installed."

ENCRYPTED=false
if [[ -n "${BACKUP_ENCRYPTION_KEY}" ]]; then
  ENCRYPTED=true
fi

if [[ "${ENCRYPTED}" == false ]]; then
  warn "BACKUP_ENCRYPTION_KEY not set — backup will be unencrypted."
fi

encrypt_or_write() {
  local dest="$1"
  if [[ "${ENCRYPTED}" == true ]]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \
      -pass "env:BACKUP_ENCRYPTION_KEY" \
      -out "${dest}.enc"
    echo "${dest}.enc"
  else
    cat > "${dest}"
    echo "${dest}"
  fi
}

log "Checking containers…"
check_container "${POSTGRES_CONTAINER}"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
log "Backup destination: ${BACKUP_DIR}"

# PostgreSQL
log "Dumping PostgreSQL '${POSTGRES_DB}'…"
PG_DEST="${BACKUP_DIR}/postgres_${POSTGRES_DB}.dump"
docker exec "${POSTGRES_CONTAINER}" \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --format=custom --compress=9 \
  | encrypt_or_write "${PG_DEST}" > /dev/null
FINAL_FILE="${PG_DEST}$([[ ${ENCRYPTED} == true ]] && echo '.enc')"
chmod 600 "${FINAL_FILE}"
PG_SIZE=$(du -sh "${FINAL_FILE}" | cut -f1)
log "PostgreSQL dump complete (${PG_SIZE}): $(basename "${FINAL_FILE}")"

# Redis
if docker inspect --format '{{.State.Running}}' "${REDIS_CONTAINER}" 2>/dev/null | grep -q true; then
  log "Triggering Redis BGSAVE…"
  docker exec "${REDIS_CONTAINER}" \
    redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning BGSAVE >/dev/null 2>&1 || true
  for i in $(seq 1 30); do
    SAVING=$(docker exec "${REDIS_CONTAINER}" \
      redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning INFO persistence 2>/dev/null \
      | grep rdb_bgsave_in_progress | tr -d '[:space:]' | cut -d: -f2)
    [[ "${SAVING}" == "0" ]] && break
    sleep 1
  done
  log "Copying Redis data directory…"
  docker cp "${REDIS_CONTAINER}:/data/." "${BACKUP_DIR}/redis/"
  chmod -R 600 "${BACKUP_DIR}/redis/"
  REDIS_SIZE=$(du -sh "${BACKUP_DIR}/redis/" | cut -f1)
  log "Redis backup complete (${REDIS_SIZE}): redis/"
else
  log "Redis container not running — skipping Redis backup."
fi

cat > "${BACKUP_DIR}/MANIFEST.txt" <<EOF
Autoflow Backup (legacy mode)
==============================
Timestamp  : ${TIMESTAMP}
Host       : $(hostname)
Stack dir  : ${ROOT_DIR}
Encrypted  : ${ENCRYPTED}

Upgrade to Restic mode: set BACKUP_RESTIC_PASSWORD + BACKUP_BACKEND in .env
  https://restic.net

Contents
--------
$(basename "${FINAL_FILE}")  – PostgreSQL custom-format dump$([ "${ENCRYPTED}" = true ] && echo " (AES-256-CBC)")
redis/                        – Redis data directory snapshot

Restore
-------
  ./scripts/restore.sh ${BACKUP_DIR}
$([ "${ENCRYPTED}" = true ] && echo "  Requires BACKUP_ENCRYPTION_KEY in environment.")
EOF

log "──────────────────────────────────────"
log "Backup complete: ${BACKUP_DIR}"
log "To restore: ./scripts/restore.sh ${BACKUP_DIR}"
