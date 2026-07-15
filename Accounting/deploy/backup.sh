#!/bin/bash
# Step 21 — nightly PostgreSQL backup. Registered in cron by deploy/setup-cron.sh.
set -euo pipefail
cd /opt/ebms/Accounting   # so docker compose finds docker-compose.yml, the override, and .env
BACKUP_DIR=/opt/ebms/Accounting/backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
docker compose exec -T postgres pg_dump -U ebms ebms | gzip > "$BACKUP_DIR/ebms_$(date +%H%M).sql.gz"
# Remove backup files older than 30 days, then clean up empty date folders
find /opt/ebms/Accounting/backups -type f -mtime +30 -delete
find /opt/ebms/Accounting/backups -mindepth 1 -type d -empty -delete
