#!/bin/sh
set -eu

: "${AI_DATABASE_URL:?AI_DATABASE_URL is required}"
: "${BACKUP_ACCESS_KEY_ID:?BACKUP_ACCESS_KEY_ID is required}"
: "${BACKUP_SECRET_ACCESS_KEY:?BACKUP_SECRET_ACCESS_KEY is required}"
: "${BACKUP_ENDPOINT:?BACKUP_ENDPOINT is required}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET is required}"

BACKUP_REGION="${BACKUP_REGION:-us-east-1}"
BACKUP_PREFIX="${BACKUP_PREFIX:-plane-ai/postgres}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
ENCRYPTION_KEY_FILE="${ENCRYPTION_KEY_FILE:-/run/secrets/ai_backup_encryption_key}"

if [ ! -s "$ENCRYPTION_KEY_FILE" ]; then
  echo "Backup encryption key is unavailable" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="/tmp/plane-ai-${timestamp}.dump.gpg"
remote="idrive:${BACKUP_BUCKET}/${BACKUP_PREFIX}"

cleanup() {
  rm -f "$archive"
}
trap cleanup EXIT INT TERM

pg_dump "$AI_DATABASE_URL" --format=custom --no-owner --no-acl \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
      --passphrase-file "$ENCRYPTION_KEY_FILE" --output "$archive"

rclone copyto "$archive" "${remote}/$(basename "$archive")" --s3-no-check-bucket
rclone delete "$remote" --min-age "${BACKUP_RETENTION_DAYS}d" --include "*.dump.gpg" --s3-no-check-bucket
