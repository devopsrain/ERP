#!/bin/bash
# Steps 17-20 of DELL_R430_FULL_DEPLOYMENT_GUIDE.md — run ONCE with:  sudo bash deploy/setup-tls.sh
# Gets the Let's Encrypt certificate via the Route 53 DNS challenge, installs the
# renewal hook, enables secure cookies, and restarts the stack with the HTTPS nginx config.
set -euo pipefail

DOMAIN="ebms.devopsrain.com"
EMAIL="info@devopsrain.com"
APP_DIR="/opt/ebms/Accounting"

echo "── 1/5 Installing certbot + Route 53 plugin ──"
apt-get update -qq
apt-get install -y certbot python3-certbot-dns-route53

echo "── 2/5 AWS credentials for the DNS challenge ──"
if [ -f /root/.aws/credentials ]; then
    echo "   /root/.aws/credentials already exists — keeping it."
else
    read -rp  "   AWS Access Key ID (certbot-dns IAM user): " AWS_ID
    read -rsp "   AWS Secret Access Key: " AWS_SECRET; echo
    mkdir -p /root/.aws
    cat > /root/.aws/credentials <<CRED
[default]
aws_access_key_id = ${AWS_ID}
aws_secret_access_key = ${AWS_SECRET}
CRED
    chmod 700 /root/.aws
    chmod 600 /root/.aws/credentials
fi

echo "── 3/5 Requesting certificate for ${DOMAIN} ──"
certbot certonly --dns-route53 -d "${DOMAIN}" \
    --non-interactive --agree-tos -m "${EMAIL}" --keep-until-expiring

echo "── 4/5 Installing renewal deploy hook ──"
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/sh
docker exec ebms-nginx nginx -s reload
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

echo "── 5/5 Enabling secure cookies and applying everything ──"
sed -i 's/^SESSION_COOKIE_SECURE=0/SESSION_COOKIE_SECURE=1/' "${APP_DIR}/.env"
cd "${APP_DIR}"
docker compose up -d          # recreates app containers with the new env
docker compose restart nginx  # loads the HTTPS server block + certificate

echo ""
echo "Done. Verify:  https://${DOMAIN}  (padlock, and http:// must redirect)"
echo "Renewal is automatic (systemd certbot.timer). Dry-run test:  sudo certbot renew --dry-run"
