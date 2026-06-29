#!/bin/sh
# Restore a paw Postgres dump produced by backup.sh.
# Usage: restore.sh <dump-path>
# Restores into RESTORE_DB (default PGDATABASE). Uses pg_restore --clean --if-exists
# so it overwrites the existing schema/data in place. Connection from libpq env.
set -eu

dump="${1:-}"
if [ -z "$dump" ]; then
  echo "usage: restore.sh <dump-path>" >&2
  exit 2
fi
if [ ! -f "$dump" ]; then
  echo "[restore] dump not found: ${dump}" >&2
  exit 2
fi

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-paw}"
PGDATABASE="${PGDATABASE:-paw}"
RESTORE_DB="${RESTORE_DB:-$PGDATABASE}"
export PGHOST PGPORT PGUSER

echo "[restore] restoring ${dump} -> ${RESTORE_DB}@${PGHOST}:${PGPORT}"
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname "$RESTORE_DB" "$dump"
echo "[restore] done"
