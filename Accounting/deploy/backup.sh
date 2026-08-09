#!/bin/bash
# Nightly backup: PostgreSQL dump + application data volume (bid PDFs,
# letters, runtime JSON). Registered in cron by deploy/setup-cron.sh.
set -euo pipefail
cd /opt/ebms/Accounting   # so docker compose finds docker-compose.yml, the override, and .env

# Load environment (e.g. S3_BACKUP_BUCKET for optional off-site sync)
set -a; . .env 2>/dev/null || true; set +a

BACKUP_DIR=/opt/ebms/Accounting/backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%H%M)

# 1. Database dump
docker compose exec -T postgres pg_dump -U ebms ebms | gzip > "$BACKUP_DIR/ebms_${STAMP}.sql.gz"

# 2. Application data volume — pg_dump does NOT cover uploaded files
#    (bid documents, letters/signatures JSON, exports)
docker compose exec -T web tar czf - -C /app/web data > "$BACKUP_DIR/appdata_${STAMP}.tar.gz"

# 3. Retention: remove files older than 30 days, then clean empty date folders
find /opt/ebms/Accounting/backups -type f -mtime +30 -delete
find /opt/ebms/Accounting/backups -mindepth 1 -type d -empty -delete

# 4. Optional off-site sync to S3 (non-fatal — local backup must never break)
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
    if command -v aws >/dev/null 2>&1; then
        if aws s3 sync /opt/ebms/Accounting/backups "s3://${S3_BACKUP_BUCKET}/$(hostname)/" --storage-class STANDARD_IA --only-show-errors; then
            echo "$(date -Is) INFO: off-site sync to s3://${S3_BACKUP_BUCKET}/$(hostname)/ completed"
        else
            rc=$?
            echo "$(date -Is) ERROR: off-site S3 sync failed (exit ${rc}) — local backup unaffected" >&2
        fi
    else
        echo "$(date -Is) WARNING: S3_BACKUP_BUCKET is set but 'aws' CLI not found — skipping off-site sync" >&2
    fi
else
    echo "$(date -Is) INFO: S3_BACKUP_BUCKET not set — off-site sync disabled"
fi

# 5. Disk-space guard — a full disk takes PostgreSQL down with it
USAGE=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "${USAGE:-0}" -ge 85 ]; then
    echo "$(date -Is) WARNING: root filesystem at ${USAGE}% — clean up or add storage" >&2
fi
