#!/bin/bash
# Update ONLY the Nextcloud stack — never touches the EBMS containers.
# Run on the server:  bash deploy/update-nextcloud.sh
set -euo pipefail
cd /opt/ebms/Accounting

echo "══ git pull ════════════════════════════════════════════"
git pull --ff-only

cd nextcloud
if [ ! -f .env ]; then
    echo "nextcloud/.env missing — run 'bash setup.sh' first. Nothing changed."
    exit 1
fi
echo "══ refresh nextcloud ═══════════════════════════════════"
docker compose up -d
docker compose ps --format "table {{.Name}}\t{{.Status}}"
echo "Done — EBMS was not touched."
