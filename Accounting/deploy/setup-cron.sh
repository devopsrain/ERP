#!/bin/bash
# Steps 21-22 — register the nightly backup and the 5-minute health watchdog.
# Run ONCE as the normal user (no sudo):  bash deploy/setup-cron.sh
# Idempotent: safe to re-run; it replaces its own entries and touches nothing else.
set -euo pipefail

APP_DIR="/opt/ebms/Accounting"
chmod +x "${APP_DIR}/deploy/backup.sh"
mkdir -p "${APP_DIR}/backups"

( crontab -l 2>/dev/null | grep -v '# ebms-' || true
  echo "0 2 * * * ${APP_DIR}/deploy/backup.sh >> ${APP_DIR}/backups/backup.log 2>&1  # ebms-backup"
  echo "*/5 * * * * curl -sf --max-time 10 http://localhost/nginx-health > /dev/null || (cd ${APP_DIR} && /usr/bin/docker compose restart)  # ebms-watchdog"
) | crontab -

echo "Installed cron entries:"
crontab -l | grep '# ebms-'
echo ""
echo "Testing backup once now..."
"${APP_DIR}/deploy/backup.sh"
ls -lh "${APP_DIR}"/backups/*/ | tail -3
