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
# Uploaded files (signature photos, bill photos) are not in the database: back them up too.
if [ -n "${UPLOADS_DIR:-}" ] && [ -d "$UPLOADS_DIR" ]; then
  tar -czf "$BACKUP_DIR/ctdms-uploads-$STAMP.tar.gz" -C "$UPLOADS_DIR" .
  echo "uploads written: $BACKUP_DIR/ctdms-uploads-$STAMP.tar.gz"
fi
find "$BACKUP_DIR" \( -name 'ctdms-*.dump' -o -name 'ctdms-uploads-*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
# Optional off-site copy (uncomment one):
# rclone copy "$FILE" remote:ctdms-backups/
# aws s3 cp "$FILE" "s3://your-bucket/ctdms/"
