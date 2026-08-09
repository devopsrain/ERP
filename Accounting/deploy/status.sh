#!/bin/bash
# One-shot server + app health report.  Run:  bash deploy/status.sh
cd /opt/ebms/Accounting

echo "══ SYSTEM ══════════════════════════════════════════════"
uptime
echo
df -h / | awk 'NR<=2'
echo
free -h | awk 'NR<=2'

echo
echo "══ CONTAINERS ══════════════════════════════════════════"
docker compose ps --format "table {{.Name}}\t{{.Status}}"

echo
echo "══ APP ═════════════════════════════════════════════════"
printf "health endpoint : "; curl -sk https://localhost/health -o /dev/null -w "%{http_code}\n" || true
printf "login page      : "; curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/auth/login || true
printf "cert expires    : "; echo | openssl s_client -connect localhost:443 -servername ebms.devopsrain.com 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2

echo
echo "══ SCHEMA INIT ═════════════════════════════════════════"
HEALTH_JSON=$(curl -sk https://localhost/health || true)
if [ -n "$HEALTH_JSON" ]; then
  if command -v jq >/dev/null 2>&1; then
    echo "$HEALTH_JSON" | jq '.checks.schema_init // "schema_init not reported"'
  else
    echo "$HEALTH_JSON" | grep -o '"schema_init":{[^}]*}' || echo "schema_init not reported"
  fi
else
  echo "health endpoint unreachable — cannot read schema_init status"
fi
echo "(if not ok: run  bash deploy/migrate.sh  to apply/repair the schema)"

echo
echo "══ DATABASE ════════════════════════════════════════════"
docker compose exec -T postgres psql -U ebms -d ebms -tAc \
  "SELECT 'tables: '||count(*) FROM pg_tables WHERE schemaname='public';
   SELECT 'db size: '||pg_size_pretty(pg_database_size('ebms'));
   SELECT 'users:   '||count(*) FROM users;" 2>/dev/null || echo "DB query failed"

echo
echo "══ BACKUPS (latest) ════════════════════════════════════"
ls -lht backups/*/ 2>/dev/null | head -4 || echo "no backups yet"

echo
echo "══ TAILSCALE ═══════════════════════════════════════════"
tailscale status 2>/dev/null | head -5 || echo "tailscale not running"

echo
echo "══ RECENT APP ERRORS (last 24h) ════════════════════════"
docker compose logs --since 24h web 2>/dev/null | grep -ciE "error|traceback" | \
  xargs -I{} echo "{} error lines in web log (docker compose logs web | grep -i error)"
