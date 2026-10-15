#!/bin/sh
# Daily PostgreSQL backup with retention. Used by the `backup` service in docker-compose.yml,
# or from cron on a plain server:  15 1 * * *  /opt/ctdms/deploy/backup.sh
set -eu
BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
: "${PGHOST:?PGHOST required}" "${PGUSER:?}" "${PGDATABASE:?}"
FILE="$BACKUP_DIR/ctdms-$STAMP.dump"
pg_dump --format=custom --no-owner --file="$FILE"
echo "backup written: $FILE ($(du -h "$FILE" | cut -f1))"
find "$BACKUP_DIR" -name 'ctdms-*.dump' -mtime +"$KEEP_DAYS" -delete
# Optional off-site copy (uncomment one):
# rclone copy "$FILE" remote:ctdms-backups/
# aws s3 cp "$FILE" "s3://your-bucket/ctdms/"
