#!/bin/sh
# Scheduled logical backup of the paw Postgres database.
# Writes a custom-format dump and prunes dumps older than the retention window.
# Connection comes from libpq env (PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD).
set -eu

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-paw}"
PGDATABASE="${PGDATABASE:-paw}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
export PGHOST PGPORT PGUSER PGDATABASE

mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="${BACKUP_DIR}/paw-${ts}.dump"

echo "[backup] dumping ${PGDATABASE}@${PGHOST}:${PGPORT} -> ${out}"
pg_dump --format=custom --file="$out"
echo "[backup] wrote ${out} ($(wc -c < "$out") bytes)"

# Prune: delete custom-format dumps older than the retention window.
echo "[backup] pruning dumps older than ${BACKUP_RETENTION_DAYS} day(s) in ${BACKUP_DIR}"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'paw-*.dump' \
  -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete

echo "[backup] done"
