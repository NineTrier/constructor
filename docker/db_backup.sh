#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_BASE_DIR="${BACKUP_BASE_DIR:-/backups/database}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_RUN_ON_START="${BACKUP_RUN_ON_START:-1}"

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [db_backup] $*"
}

wait_for_db() {
  log "Waiting for Postgres at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
  until PGPASSWORD="${POSTGRES_PASSWORD}" pg_isready -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" >/dev/null 2>&1; do
    sleep 5
  done
  log "Postgres is ready."
}

cleanup_old() {
  if [ "${BACKUP_RETENTION_DAYS}" -lt 0 ] 2>/dev/null; then
    log "Retention disabled (BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS})."
    return
  fi
  find "${BACKUP_BASE_DIR}" -mindepth 1 -maxdepth 1 -type d -mtime +"${BACKUP_RETENTION_DAYS}" -print -exec rm -rf {} + 2>/dev/null || true
}

run_backup() {
  day_dir="${BACKUP_BASE_DIR}/$(date +%Y-%m-%d)"
  ts="$(date +%Y%m%d_%H%M%S)"
  file="${day_dir}/${POSTGRES_DB}_${ts}.dump"

  mkdir -p "${day_dir}"
  PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -F c \
    -f "${file}"
  log "Backup created: ${file}"
}

mkdir -p "${BACKUP_BASE_DIR}"
wait_for_db

if [ "${BACKUP_RUN_ON_START}" = "1" ]; then
  run_backup
  cleanup_old
fi

while true; do
  sleep "${BACKUP_INTERVAL_SECONDS}"
  run_backup
  cleanup_old
done

