#!/bin/bash
# Steps 21-22 — register the nightly backup and the 5-minute health watchdog.
# Run ONCE as the normal user (no sudo):  bash deploy/setup-cron.sh
# Idempotent: safe to re-run; it replaces its own entries and touches nothing else.
set -euo pipefail

APP_DIR="/opt/ebms/Accounting"
chmod +x "${APP_DIR}/deploy/backup.sh"
chmod +x "${APP_DIR}/deploy/watchdog.sh"
mkdir -p "${APP_DIR}/backups"

( crontab -l 2>/dev/null | grep -v '# ebms-' || true
  echo "0 1 * * * ${APP_DIR}/deploy/backup.sh >> ${APP_DIR}/backups/backup.log 2>&1  # ebms-backup"
  echo "*/5 * * * * ${APP_DIR}/deploy/watchdog.sh >> ${APP_DIR}/backups/watchdog.log 2>&1  # ebms-watchdog"
) | crontab -

echo "Installed cron entries:"
crontab -l | grep '# ebms-'
echo ""
echo "Testing backup once now..."
"${APP_DIR}/deploy/backup.sh"
ls -lh "${APP_DIR}"/backups/*/ | tail -3
