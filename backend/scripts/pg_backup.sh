#!/bin/sh
# One backup run: a custom-format pg_dump into $BACKUP_DIR, then delete dumps older than $BACKUP_RETENTION_DAYS (default 14).
# The compose `backup` service calls this once a day; the CI workflow calls it once and checks the result. Same code in both.
#   env: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE  BACKUP_DIR (default /backups)  BACKUP_RETENTION_DAYS (default 14)
set -eu
dir="${BACKUP_DIR:-/backups}"
keep="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$dir"
file="$dir/tripai-$(date +%Y%m%d-%H%M%S).dump"
if pg_dump -Fc -f "$file" "${PGDATABASE:-tripai}"; then
  echo "backup written: $file ($(wc -c < "$file") bytes)"
else
  echo "BACKUP FAILED" >&2
  exit 1
fi
find "$dir" -name 'tripai-*.dump' -mtime +"$keep" -print -delete | sed 's/^/pruned (older than retention): /'
