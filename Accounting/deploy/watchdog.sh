#!/bin/bash
# 5-minute health watchdog (installed by deploy/setup-cron.sh).
# If the nginx health endpoint stops answering, restart the compose stack
# and email the admin via the Resend API (best-effort, only when
# RESEND_API_KEY and ADMIN_EMAIL are present in the app .env).
set -uo pipefail

APP_DIR="/opt/ebms/Accounting"
HEALTH_URL="http://localhost/nginx-health"

# Healthy → nothing to do.
if curl -sf --max-time 10 "${HEALTH_URL}" > /dev/null 2>&1; then
    exit 0
fi

echo "$(date -Is) watchdog: health check failed — restarting services"
cd "${APP_DIR}" && /usr/bin/docker compose restart

# ── Admin email via Resend (guarded by RESEND_API_KEY presence) ──────────
# Read single keys out of .env instead of sourcing it (values may contain
# characters that are not valid shell).
env_val() {
    grep -E "^${1}=" "${APP_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- \
        | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

RESEND_API_KEY="$(env_val RESEND_API_KEY)"
ADMIN_EMAIL="$(env_val ADMIN_EMAIL)"
EMAIL_FROM="$(env_val EMAIL_FROM)"
[ -n "${EMAIL_FROM}" ] || EMAIL_FROM="EBMS <onboarding@resend.dev>"

if [ -n "${RESEND_API_KEY}" ] && [ -n "${ADMIN_EMAIL}" ]; then
    HOST="$(hostname)"
    NOW="$(date -Is)"
    curl -s --max-time 10 -X POST "https://api.resend.com/emails" \
        -H "Authorization: Bearer ${RESEND_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"from\":\"${EMAIL_FROM}\",\"to\":[\"${ADMIN_EMAIL}\"],\"subject\":\"[EBMS] watchdog restarted services on ${HOST}\",\"html\":\"<p>The health check at ${HEALTH_URL} failed at ${NOW} on ${HOST}.</p><p><code>docker compose restart</code> was issued. Please verify the system recovered.</p>\"}" \
        > /dev/null \
        && echo "$(date -Is) watchdog: admin notified via Resend (${ADMIN_EMAIL})" \
        || echo "$(date -Is) watchdog: Resend notification failed"
else
    echo "$(date -Is) watchdog: RESEND_API_KEY/ADMIN_EMAIL not set — skipping email notification"
fi
