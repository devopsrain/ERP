#!/bin/bash
# Update ONLY the monitoring stack (Uptime Kuma) — never touches EBMS.
# Run on the server:  bash deploy/update-monitoring.sh
set -euo pipefail
cd /opt/ebms/Accounting

echo "══ git pull ════════════════════════════════════════════"
git pull --ff-only

echo "══ refresh monitoring ══════════════════════════════════"
cd monitoring
docker compose pull --quiet
docker compose up -d
docker compose ps --format "table {{.Name}}\t{{.Status}}"
printf "uptime-kuma: "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3001/ || true
echo "Done — EBMS was not touched. UI: http://<server-ip>:3001"
