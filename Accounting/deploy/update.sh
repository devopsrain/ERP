#!/bin/bash
# One-command server update — enforces the correct deployment sequence:
#   pull → migrate (hard gate) → rebuild → verify
# Run on the server:  bash deploy/update.sh
# Sub-stacks (risk-sim/, nextcloud/) are rebuilt only when their files changed.
set -euo pipefail
cd /opt/ebms/Accounting

# --all: force-rebuild every stack regardless of what this pull changed.
# Use when a previous manual pull made the change-detection below see nothing.
FORCE_ALL=0
[ "${1:-}" = "--all" ] && FORCE_ALL=1

echo "══ 1/5 git pull ════════════════════════════════════════"
BEFORE=$(git rev-parse HEAD)
git pull --ff-only
AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ] && [ "$FORCE_ALL" = "0" ]; then
    echo "No new commits — sub-stacks will be SKIPPED unless you run: bash deploy/update.sh --all"
fi
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER" || true)

echo "══ 2/5 schema migration (must PASS) ════════════════════"
bash deploy/migrate.sh   # exits nonzero on any SQL error → aborts the update

echo "══ 3/5 rebuild EBMS containers ═════════════════════════"
docker compose up -d --build web api
# nginx only needs recreating when its config/compose entry changed
if echo "$CHANGED" | grep -qE '^(nginx\.conf|docker-compose\.yml)'; then
    docker compose up -d nginx
fi

echo "══ 4/5 sub-stacks (changed or --all) ═══════════════════"
if [ "$FORCE_ALL" = "1" ] || echo "$CHANGED" | grep -q '^risk-sim/'; then
    ( cd risk-sim && docker compose up -d --build )
fi
if [ "$FORCE_ALL" = "1" ] || echo "$CHANGED" | grep -q '^nextcloud/'; then
    ( cd nextcloud && [ -f .env ] && docker compose up -d || echo "nextcloud: no .env yet — skipped" )
fi

echo "══ 5/5 verify ══════════════════════════════════════════"
sleep 5
docker compose ps --format "table {{.Name}}\t{{.Status}}"
printf "health: "; curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/health || true
echo ""
echo "Done. Hard-refresh the browser (Ctrl+Shift+R)."
echo "Full report: bash deploy/status.sh"
