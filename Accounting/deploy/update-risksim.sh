#!/bin/bash
# Update ONLY the risk-sim stack — never touches the EBMS containers.
# Run on the server:  bash deploy/update-risksim.sh [--run-job]
#   --run-job : also run the correlation/screener job once after the rebuild.
set -euo pipefail
cd /opt/ebms/Accounting

echo "══ git pull ════════════════════════════════════════════"
git pull --ff-only

echo "══ rebuild risk-sim ════════════════════════════════════"
cd risk-sim
docker compose up -d --build
docker compose ps --format "table {{.Name}}\t{{.Status}}"

if [ "${1:-}" = "--run-job" ]; then
    echo "══ running job once ════════════════════════════════"
    docker compose run --rm correlation-job python -m app.daily_correlation
fi

printf "dashboard: "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/ || true
echo "Done — EBMS was not touched. Hard-refresh the dashboard (Ctrl+Shift+R)."
