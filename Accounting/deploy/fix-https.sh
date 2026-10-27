#!/bin/bash
# One-shot HTTPS recovery for the R430 — safe to re-run any time.
#   1. pins nginx's 443 to the LAN IP (HTTPS_BIND_IP in .env) so it can never
#      collide with tailscaled's <tailscale-ip>:443 listener after a reboot
#   2. recreates nginx and checks it answers on the LAN address
#   3. points the Tailscale Funnel at that LAN address (needs operator rights:
#      `sudo tailscale set --operator=$USER` once, or run this with sudo)
#   4. renews the Let's Encrypt certificate if due/expired and reloads nginx
# Usage:  bash deploy/fix-https.sh [LAN_IP]      (default 192.168.68.121)
set -euo pipefail
cd /opt/ebms/Accounting
LAN_IP="${1:-192.168.68.121}"

echo "══ 1/4 pin nginx HTTPS to ${LAN_IP} ════════════════════"
touch .env
if grep -q '^HTTPS_BIND_IP=' .env; then
    sed -i "s/^HTTPS_BIND_IP=.*/HTTPS_BIND_IP=${LAN_IP}/" .env
else
    echo "HTTPS_BIND_IP=${LAN_IP}" >> .env
fi
grep '^HTTPS_BIND_IP=' .env

echo "══ 2/4 recreate nginx ══════════════════════════════════"
docker compose up -d nginx
sleep 3
echo "listeners on 443:"
sudo ss -tlnp | grep ':443 ' || echo "  (none!)"
code=$(curl -sk -o /dev/null -w "%{http_code}" "https://${LAN_IP}/health" || true)
echo "https://${LAN_IP}/health -> ${code}"
if [ "$code" != "200" ]; then
    echo "nginx is not answering on ${LAN_IP}:443 — check: docker compose logs nginx --tail 30" >&2
    exit 1
fi

echo "══ 3/4 funnel → https+insecure://${LAN_IP}:443 ════════"
TS=tailscale
$TS funnel status >/dev/null 2>&1 || TS="sudo tailscale"
$TS funnel reset || true
$TS funnel --bg --https=443 "https+insecure://${LAN_IP}:443"
$TS funnel status

echo "══ 4/4 certificate ═════════════════════════════════════"
if command -v certbot >/dev/null 2>&1; then
    sudo certbot renew --quiet || echo "certbot renew reported a problem — run 'sudo certbot renew' to see why" >&2
    docker exec ebms-nginx nginx -s reload || true
    sudo certbot certificates 2>/dev/null | grep -E 'Domains|Expiry' || true
else
    echo "certbot not installed on host — skipping"
fi

echo
echo "Done. Test from a phone on mobile data (Tailscale off):"
echo "  https://devopsrain-poweredge-r430.tail65f932.ts.net/health   -> 200"
echo "LAN users keep using https://ebms.devopsrain.com"
