#!/bin/bash
# Nightly backup: PostgreSQL dump + application data volume (bid PDFs,
# letters, runtime JSON). Registered in cron by deploy/setup-cron.sh.
set -euo pipefail
cd /opt/ebms/Accounting   # so docker compose finds docker-compose.yml, the override, and .env
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

# 4. Disk-space guard — a full disk takes PostgreSQL down with it
USAGE=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "${USAGE:-0}" -ge 85 ]; then
    echo "$(date -Is) WARNING: root filesystem at ${USAGE}% — clean up or add storage" >&2
fi
