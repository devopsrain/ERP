#!/bin/bash
# One-shot Nextcloud setup — automates SETUP.md steps 1–3.
# Run on the server:  cd /opt/ebms/Accounting/nextcloud && bash setup.sh [data-dir]
#   data-dir (optional): where user files live. Default /srv/nextcloud-data.
#   Pick a path on the BIG disk (check: df -h), not the OS root drive.
set -euo pipefail
cd "$(dirname "$0")"

DATA_DIR="${1:-/srv/nextcloud-data}"

# 1. .env — create once, never overwrite (it holds the DB password)
if [ -f .env ]; then
    echo ".env already exists — keeping it (delete it manually to regenerate)."
else
    PASS=$(openssl rand -hex 24 2>/dev/null || head -c 48 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 48)
    {
        echo "NEXTCLOUD_DB_PASSWORD=${PASS}"
        echo "NEXTCLOUD_DATA_DIR=${DATA_DIR}"
    } > .env
    chmod 600 .env
    echo "Created .env (data dir: ${DATA_DIR})"
fi

# Re-read whatever .env actually says (covers the pre-existing case)
DATA_DIR=$(grep '^NEXTCLOUD_DATA_DIR=' .env | cut -d= -f2)

# 2. Data directory — uid 33 = www-data inside the nextcloud image
if [ ! -d "$DATA_DIR" ]; then
    echo "Creating ${DATA_DIR} (needs sudo)..."
    sudo mkdir -p "$DATA_DIR"
fi
sudo chown 33:33 "$DATA_DIR"

# Warn if the data dir sits on a nearly-full filesystem
USAGE=$(df --output=pcent "$DATA_DIR" | tail -1 | tr -dc '0-9')
if [ "${USAGE:-0}" -ge 80 ]; then
    echo "WARNING: filesystem holding ${DATA_DIR} is ${USAGE}% full — consider another mount (df -h)."
fi

# 3. Start the stack
docker compose up -d
echo ""
docker compose ps --format "table {{.Name}}\t{{.Status}}"
echo ""
echo "══ Next steps (manual, one-time) ═══════════════════════════════"
echo "1. Open http://192.168.68.121:8081 and create the ADMIN account."
echo "2. Admin settings → Basic settings → Background jobs → select 'Cron'."
echo "3. Optional HTTPS inside the tailnet (does NOT touch the EBMS funnel):"
echo "     sudo tailscale serve --bg --https=8443 http://127.0.0.1:8081"
echo "4. Uploads: see SETUP.md §3b (desktop sync client recommended for bulk)."
