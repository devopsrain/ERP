#!/bin/bash
# ============================================================
#  Ethiopian Business Management System — Server Diagnostics
# ============================================================
# Run on the EC2 instance:  bash /opt/ethiopian-business/diagnose.sh
# Output goes to stdout AND is appended to /var/log/eb-diagnose.log
#
# Sections:
#   1  Process status
#   2  Port bindings
#   3  Health checks  (/health  +  /api/v1/health)
#   4  App log — last 30 lines
#   5  App error log — last 20 lines
#   6  Nginx error log — last 15 lines
#   7  Supervisor log — last 15 lines
#   8  Disk & memory
#   9  Env-var presence check (values hidden)
#  10  Database connectivity
#  11  Recent auth activity
#  12  Fail2ban status

LOG_FILE="/var/log/eb-diagnose.log"
APP_URL="http://127.0.0.1:5000"
ENV_FILE="/opt/ethiopian-business/.env"
VENV_PY="/opt/ethiopian-business/venv/bin/python3"
TS=$(date '+%Y-%m-%d %H:%M:%S')

# Tee all output to the log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Ethiopian Business System — Diagnostics @ $TS"
echo "════════════════════════════════════════════════════════════"


# ── 1. Process status ─────────────────────────────────────────────────
echo ""
echo "── 1. PROCESS STATUS ────────────────────────────────────────"
supervisorctl status 2>/dev/null \
    || systemctl status ethiopian-business --no-pager 2>/dev/null \
    || echo "  Could not determine process status"
echo ""
echo "  Running uvicorn processes:"
pgrep -a -f uvicorn 2>/dev/null || echo "  none found"


# ── 2. Port bindings ──────────────────────────────────────────────────
echo ""
echo "── 2. PORT BINDINGS ─────────────────────────────────────────"
ss -tlnp 2>/dev/null | grep -E ":5000|:80|:443" \
    || netstat -tlnp 2>/dev/null | grep -E ":5000|:80|:443" \
    || echo "  (unable to check — ss/netstat not found)"


# ── 3. Health checks ──────────────────────────────────────────────────
echo ""
echo "── 3. HEALTH CHECKS ─────────────────────────────────────────"
echo -n "  /health (ALB liveness):     "
curl -sf -o /dev/null -w "HTTP %{http_code}  (%{time_total}s)\n" \
    "$APP_URL/health" 2>/dev/null || echo "FAILED (connection refused or timeout)"

echo -n "  /api/v1/health (detailed):  "
DETAIL=$(curl -sf -w "\nHTTP %{http_code}  (%{time_total}s)" \
    "$APP_URL/api/v1/health" 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "$DETAIL"
else
    echo "FAILED (connection refused or timeout)"
fi


# ── 4. App log ────────────────────────────────────────────────────────
echo ""
echo "── 4. LAST 30 LINES — APP LOG ───────────────────────────────"
tail -30 /var/log/ethiopian-business.log 2>/dev/null \
    || echo "  Log file not found"


# ── 5. App error log ──────────────────────────────────────────────────
echo ""
echo "── 5. LAST 20 LINES — APP ERROR LOG ─────────────────────────"
tail -20 /var/log/ethiopian-business-error.log 2>/dev/null \
    || echo "  Error log file not found"


# ── 6. Nginx error log ────────────────────────────────────────────────
echo ""
echo "── 6. LAST 15 LINES — NGINX ERROR LOG ───────────────────────"
tail -15 /var/log/nginx/error.log 2>/dev/null \
    || echo "  Nginx error log not found"


# ── 7. Supervisor log ─────────────────────────────────────────────────
echo ""
echo "── 7. LAST 15 LINES — SUPERVISOR LOG ────────────────────────"
tail -15 /var/log/supervisor/supervisord.log 2>/dev/null \
    || echo "  Supervisor log not found"


# ── 8. Disk & memory ──────────────────────────────────────────────────
echo ""
echo "── 8. DISK AND MEMORY ───────────────────────────────────────"
df -h / /opt 2>/dev/null
echo ""
free -h 2>/dev/null || { echo "  (free not available)"; vmstat -s 2>/dev/null | head -4; }
echo ""
echo "  Load averages: $(cat /proc/loadavg 2>/dev/null || uptime)"


# ── 9. Env-var presence check ─────────────────────────────────────────
echo ""
echo "── 9. ENV VAR PRESENCE (values hidden) ──────────────────────"
for var in FLASK_SECRET_KEY DATABASE_URL REDIS_URL SESSION_COOKIE_SECURE \
           LOG_LEVEL STATIC_CDN_URL; do
    if grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
        echo "  ${var}: SET"
    else
        echo "  ${var}: MISSING"
    fi
done


# ── 10. Database connectivity ─────────────────────────────────────────
echo ""
echo "── 10. DATABASE CONNECTIVITY ────────────────────────────────"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    export $(grep -E '^DATABASE_URL=' "$ENV_FILE" 2>/dev/null | xargs)
fi

if [ -n "$DATABASE_URL" ] && [ -x "$VENV_PY" ]; then
    "$VENV_PY" - <<'PYEOF' 2>&1
import os, sys, time
try:
    import psycopg2
    t = time.time()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur  = conn.cursor()
    cur.execute("SELECT version()")
    ver = cur.fetchone()[0]
    conn.close()
    print("  Connected in %.3fs -- %s" % (time.time() - t, ver[:70]))
except ImportError:
    print("  psycopg2 not installed in venv")
except Exception as e:
    print("  ERROR: %s" % e)
    sys.exit(1)
PYEOF
elif [ -z "$DATABASE_URL" ]; then
    echo "  DATABASE_URL not set — skipping"
else
    echo "  venv python not found at $VENV_PY — skipping"
fi


# ── 11. Recent auth activity ──────────────────────────────────────────
echo ""
echo "── 11. RECENT AUTH LOG ENTRIES ──────────────────────────────"
grep -iE "failed|invalid|refused|accepted|session" /var/log/auth.log 2>/dev/null \
    | tail -10 \
    || echo "  auth.log not accessible (may need root)"


# ── 12. Fail2ban ──────────────────────────────────────────────────────
echo ""
echo "── 12. FAIL2BAN STATUS ──────────────────────────────────────"
fail2ban-client status 2>/dev/null | head -8 \
    || echo "  fail2ban not running or not installed"


echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Diagnosis complete. Output also written to: $LOG_FILE"
echo "════════════════════════════════════════════════════════════"
echo ""
