# Dell R430 — EBMS Full Deployment Guide

> Ethiopian Business Management System — self-hosted on Dell PowerEdge R430
>
> Verified against `docker-compose.yml` and `nginx.conf` in this repository.

---

## Prerequisites: What You Need

- Dell PowerEdge R430 with at least **16 GB RAM** (32 GB recommended) and **2 drives** (RAID 1 strongly advised)
- A USB stick with **Ubuntu Desktop 26.04 LTS** ISO burned onto it (Ubuntu **Server** also works and is lighter — it skips the GUI; the installer prompts differ slightly)
- A keyboard + monitor (or iDRAC access) for the initial OS install
- Your git repository URL for the EBMS codebase
- A domain name (optional — required only for HTTPS; this guide uses one hosted in AWS Route 53)

---

## Phase 1: Prepare the Hardware

**1. Configure BIOS before installing the OS**

- Power on → press **F2** at the Dell splash screen to enter System Setup
- **System BIOS → System Profile Settings** → set to **Performance**
- Save and exit

**2. Configure RAID 1 (if you have 2 drives)**

- On boot, press **Ctrl+R** to enter the PERC RAID controller
- Create a new Virtual Disk → **RAID 1** → select both drives
- Initialize the virtual disk and exit

**3. Configure iDRAC (recommended — remote management)**

- Press **F2** → **iDRAC Settings → Network**
- Assign a static IP on your management network (e.g. `192.168.1.20`)
- Set the iDRAC password under **iDRAC Settings → User Configuration**
- You can now reboot / open a virtual console from a browser without physical access

---

## Phase 2: Install Ubuntu Desktop 26.04

**4. Boot from your USB stick**

- Insert USB → press **F11** at boot → select the USB drive
- Choose **Try or Install Ubuntu**, then **Install Ubuntu** from the welcome screen

**5. Follow the installer, then set up SSH and a static IP**

The Desktop installer choices:

- **Interactive installation** → **Default selection** of apps (the EBMS stack runs in Docker; nothing extra is needed)
- Storage: **Erase disk and install Ubuntu** on the RAID virtual disk (default layout is fine)
- Profile: create a user (e.g. `devopsrain`) and a strong password
- Let the install finish and reboot

> ⚠️ Unlike Ubuntu **Server**, the Desktop installer has **no OpenSSH prompt and no static-IP step** — both are required by the rest of this guide, so do them now from the server's own terminal (log in on the console, open **Terminal**).

Install and enable the SSH server:

```bash
sudo apt update
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
```

Set a **static LAN IP**. Choose an unused IP in your router's subnet — run `ipconfig` on your Windows PC first to find your network range (e.g. if your PC is `192.168.1.50` and router is `192.168.1.1`, pick something like `192.168.1.10`). Desktop uses NetworkManager, so configure it with `nmcli` (or via **Settings → Network → ⚙ → IPv4 → Manual**):

```bash
nmcli -f NAME,DEVICE con show        # note your wired connection name
sudo nmcli con mod "Wired connection 1" \
  ipv4.method manual \
  ipv4.addresses 192.168.1.10/24 \
  ipv4.gateway 192.168.1.1 \
  ipv4.dns 8.8.8.8
sudo nmcli con up "Wired connection 1"
```

Finally, stop the Desktop OS from ever suspending — critical on a server:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

*Optional (recommended):* boot to console instead of the GNOME desktop to free ~1 GB RAM — the GUI serves no purpose on a headless server. Reversible anytime with `set-default graphical.target`:

```bash
sudo systemctl set-default multi-user.target
```

**6. First login via SSH from your Windows machine**

Replace `<server-ip>` with the static IP you assigned in step 5:

```bash
ssh devopsrain@<server-ip>
```

---

## Phase 3: OS Hardening & Base Packages

**7. Update the system and install essentials**

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw fail2ban
```

**8. Configure the firewall**

```bash
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

> ⚠️ **Important:** Docker publishes container ports via iptables directly and **bypasses UFW**. UFW alone will NOT protect ports published by Docker. Step 12 below fixes this by binding internal services to `127.0.0.1` so only nginx (80/443) is reachable from the network.

**9. Install Docker Engine and Docker Compose**

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in (so the group change takes effect), or apply it to the current shell without re-logging:

```bash
newgrp docker
```

Then verify:

```bash
docker --version
docker compose version
# Should print: Docker version 28.x.x
#               Docker Compose version v2.x.x
```

---

## Phase 4: Deploy the Application

**10. Clone the repository**

```bash
cd /opt
sudo mkdir ebms && sudo chown $USER:$USER ebms
cd /opt/ebms
git clone <your-repo-url>      # creates /opt/ebms/Accounting (named after the repo)
cd /opt/ebms/Accounting
```

> **Note:** without a destination argument, `git clone` creates a folder named after the repository — here `Accounting`. So the **project directory for every step from here on is `/opt/ebms/Accounting`** (that's where `docker-compose.yml` lives). All `docker compose` commands must be run from this directory, and `.env` / `docker-compose.override.yml` must be created *inside it* — compose only loads them from the same folder as `docker-compose.yml`.

**11. Create your production `.env` file**

This block generates all secrets automatically and writes the file — no copy-pasting needed:

```bash
DB_PASS=$(python3 -c "import secrets; print(secrets.token_hex(16))")
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

cat > /opt/ebms/Accounting/.env << EOF
FLASK_SECRET_KEY=${SECRET}
SESSION_COOKIE_SECURE=0
LOG_LEVEL=INFO
POSTGRES_PASSWORD=${DB_PASS}
EOF

cat /opt/ebms/Accounting/.env   # verify it looks correct
```

The output should look like:

```text
FLASK_SECRET_KEY=<64 hex characters>
SESSION_COOKIE_SECURE=0
LOG_LEVEL=INFO
POSTGRES_PASSWORD=<32 hex characters>
```

> **Note:** `SESSION_COOKIE_SECURE=0` is deliberate at this point — HTTPS is not set up yet, and `1` would break login over plain HTTP. Step 19 flips it to `1` once TLS is active. Do not skip that step.

**12. Create `docker-compose.override.yml`**

> **Why this step exists:** the repository's `docker-compose.yml` **hardcodes** the database password (`ebms`) and the `DATABASE_URL` — putting `POSTGRES_PASSWORD` in `.env` alone has **no effect**. It also publishes Postgres (5432), Redis (6379) and the app ports (8000/8001) on all network interfaces, and Docker bypasses UFW. This override file fixes both problems without editing the tracked `docker-compose.yml`.

Requires Docker Compose **v2.24+** (the `!override` tag) — a fresh install from step 9 satisfies this.

```bash
cat > /opt/ebms/Accounting/docker-compose.override.yml << 'EOF'
services:
  web:
    environment:
      DATABASE_URL: postgresql://ebms:${POSTGRES_PASSWORD}@postgres:5432/ebms
    ports: !override
      - "127.0.0.1:8000:8000"

  api:
    environment:
      DATABASE_URL: postgresql://ebms:${POSTGRES_PASSWORD}@postgres:5432/ebms
    ports: !override
      - "127.0.0.1:8001:8001"

  event-worker:
    environment:
      DATABASE_URL: postgresql://ebms:${POSTGRES_PASSWORD}@postgres:5432/ebms

  postgres:
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports: !override
      - "127.0.0.1:5432:5432"

  redis:
    ports: !override
      - "127.0.0.1:6379:6379"

  nginx:
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
EOF
```

Docker Compose automatically merges this file with `docker-compose.yml` — no extra flags needed.

> **Note:** `POSTGRES_PASSWORD` only takes effect on the **first** startup, when the database volume is initialized. If you ever ran `docker compose up` before this step, wipe the volume first: `docker compose down -v` (destroys all data — only safe before go-live).

**13. Build and start all 6 services**

```bash
cd /opt/ebms/Accounting
docker compose up -d --build
```

The first run takes 3–5 minutes. It starts:

| Container | Role | Reachable from |
|---|---|---|
| `ebms-nginx` | Reverse proxy | LAN + Tailscale VPN (Phase 7) — ports 80/443 |
| `ebms-web` | Web UI (SSR, port 8000) | localhost only |
| `ebms-api` | REST API (port 8001) | localhost only |
| `ebms-event-worker` | Background event processor | internal only |
| `ebms-postgres` | Database (port 5432) | localhost only |
| `ebms-redis` | Cache + event bus (port 6379) | localhost only |

**14. Run database migrations**

```bash
docker compose exec web alembic upgrade head
```

**15. Verify everything is running**

```bash
docker compose ps        # all services should show "Up" / "healthy"

curl -s http://localhost/nginx-health
# Expected: ok

curl -s -o /dev/null -w "%{http_code}" http://localhost/auth/login
# Expected: 200
```

Open a browser on any machine on the same network and go to `http://<server-ip>` — you should see the login page.

---

## Phase 5: TLS / HTTPS (using your Route 53 domain)

> This phase uses a domain already hosted in AWS Route 53. A dedicated **subdomain** (`ebms.yourdomain.com`) is added for the R430 — your existing website (root A record) and AWS WorkMail (MX records) are not touched. Certificate issuance uses the **DNS challenge**, so the server never needs to be reachable from the internet and nginx keeps running throughout.

**16. Make `ebms.yourdomain.com` resolve to the server**

Pick the variant that matches how the app will be reached:

- **Public A record with the LAN IP (used in this deployment — our router has no local-DNS feature):** Route 53 console → **Hosted zones** → your domain → **Create record**: name `ebms`, type `A`, value `192.168.68.121`, TTL default. The server stays unreachable from the internet (the IP is private); the record merely publishes your internal addressing.

  Then **test from an office PC** — some routers' **DNS-rebind protection** silently blocks public names that resolve to private IPs:

  ```powershell
  nslookup ebms.yourdomain.com          # via the router — must return 192.168.68.121
  nslookup ebms.yourdomain.com 8.8.8.8  # bypassing the router — control check
  ```

  If only the second one works, rebind protection is interfering: look for a "rebind exception"/whitelist setting on the router, add `hosts`-file entries on the office PCs, or skip ahead and use the Phase 7 approach (Tailscale on every machine, record pointed at the Tailscale IP — see the note in step 26).

- **Router local DNS (preferred when available):** don't create any record in Route 53. Map `ebms.yourdomain.com → 192.168.68.121` in your router's DNS settings ("DNS host mapping" / "static DNS entries") or a local resolver (Pi-hole, AdGuard Home, dnsmasq). The name stays entirely private — certificate validation doesn't need an A record at all (certbot only writes a temporary TXT record).
- **Internet-facing (not recommended):** A record with your office's *public* IP plus router port-forwarding of 80/443. This exposes the login page to the entire internet — for staff working remotely, **Phase 7 (Tailscale VPN)** gives the same access with nothing exposed.

Either way, open the hosted zone in the Route 53 console and note the **Hosted zone ID** (e.g. `Z0123456789ABC`) — needed next.

**17. Get a free TLS certificate via the Route 53 DNS challenge**

Certbot proves domain ownership by writing a temporary TXT record through the Route 53 API, so it needs AWS credentials. In **IAM**, create a user (e.g. `certbot-dns`) with *programmatic access only* and this minimal policy (substitute your zone ID):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["route53:ListHostedZones", "route53:GetChange"],
      "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["route53:ChangeResourceRecordSets"],
      "Resource": "arn:aws:route53:::hostedzone/<YOUR_ZONE_ID>" }
  ]
}
```

On the server, store the access key for **root** (certbot runs under sudo):

```bash
sudo mkdir -p /root/.aws
sudo tee /root/.aws/credentials > /dev/null << 'EOF'
[default]
aws_access_key_id = <ACCESS_KEY_ID>
aws_secret_access_key = <SECRET_ACCESS_KEY>
EOF
sudo chmod 700 /root/.aws
sudo chmod 600 /root/.aws/credentials
```

Install certbot with the Route 53 plugin and issue the certificate — no nginx stop, no port 80 needed:

```bash
sudo apt install -y certbot python3-certbot-dns-route53
sudo certbot certonly --dns-route53 -d ebms.yourdomain.com
```

Certificates land in `/etc/letsencrypt/live/ebms.yourdomain.com/`. The override file from step 12 already mounts `/etc/letsencrypt` (read-only) into the nginx container — **no copying needed**, and renewals are picked up automatically on nginx restart.

**18. Enable the HTTPS server block in nginx.conf**

The HTTPS block shipped in `nginx.conf` is a commented stub (its `location` lines contain `...` placeholders and are not valid config). Replace the commented block at the bottom of `nginx.conf` with this complete version (substitute your domain):

```nginx
    server {
        listen 443 ssl;
        http2 on;
        server_name ebms.yourdomain.com;

        ssl_certificate     /etc/letsencrypt/live/ebms.yourdomain.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/ebms.yourdomain.com/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_session_cache   shared:SSL:10m;
        ssl_session_timeout 10m;

        add_header Strict-Transport-Security "max-age=63072000" always;
        add_header X-Frame-Options SAMEORIGIN;
        add_header X-Content-Type-Options nosniff;

        location /api/ {
            proxy_pass         http://api_pool;
            proxy_set_header   Host              $host;
            proxy_set_header   X-Real-IP         $remote_addr;
            proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header   X-Forwarded-Proto $scheme;
            proxy_set_header   X-Request-ID      $request_id;
            proxy_http_version 1.1;
            proxy_set_header   Connection        "";
            proxy_read_timeout 30s;
        }

        location / {
            proxy_pass         http://web_pool;
            proxy_set_header   Host              $host;
            proxy_set_header   X-Real-IP         $remote_addr;
            proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header   X-Forwarded-Proto $scheme;
            proxy_set_header   X-Request-ID      $request_id;
            proxy_http_version 1.1;
            proxy_set_header   Connection        "";
            proxy_read_timeout 60s;
        }
    }
```

Also uncomment the redirect line in the port-80 server block:

```nginx
        return 301 https://$host$request_uri;
```

**19. Turn on secure cookies and apply everything**

`SESSION_COOKIE_SECURE` is an environment variable of the **app containers** — restarting nginx alone will not apply it. Recreate the stack:

```bash
sed -i 's/SESSION_COOKIE_SECURE=0/SESSION_COOKIE_SECURE=1/' /opt/ebms/Accounting/.env
cd /opt/ebms/Accounting
docker compose up -d      # recreates containers whose env changed
docker compose restart nginx   # reloads the edited nginx.conf
```

Verify: `https://ebms.yourdomain.com` loads with a padlock, and `http://` redirects to `https://`.

**20. Auto-renew certificates**

No cron job needed: Ubuntu's certbot package ships a **systemd timer** that checks for due renewals twice a day, and the DNS challenge means nginx keeps running throughout. Verify the timer is active:

```bash
systemctl list-timers | grep certbot
```

nginx doesn't notice a changed certificate file on its own, so add a deploy hook — certbot runs everything in this directory *only after a certificate was actually renewed*:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh > /dev/null << 'EOF'
#!/bin/sh
docker exec ebms-nginx nginx -s reload
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

(`nginx -s reload` re-reads the certificate with zero downtime — no container restart needed. The container name `ebms-nginx` comes from `docker-compose.yml`.)

Test the renewal path without making changes:

```bash
sudo certbot renew --dry-run
```

---

## Phase 6: Backups & Monitoring

**21. Automated PostgreSQL backups**

```bash
cat > /opt/ebms/Accounting/backup.sh << 'SCRIPT'
#!/bin/bash
set -euo pipefail
cd /opt/ebms/Accounting   # so docker compose finds docker-compose.yml, the override, and .env
BACKUP_DIR=/opt/ebms/Accounting/backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
docker compose exec -T postgres pg_dump -U ebms ebms | gzip > "$BACKUP_DIR/ebms_$(date +%H%M).sql.gz"
# Remove backup files older than 30 days, then clean up empty date folders
find /opt/ebms/Accounting/backups -type f -mtime +30 -delete
find /opt/ebms/Accounting/backups -mindepth 1 -type d -empty -delete
SCRIPT
chmod +x /opt/ebms/Accounting/backup.sh
```

Run it once manually and check a `.sql.gz` file appears:

```bash
/opt/ebms/Accounting/backup.sh && ls -lh /opt/ebms/Accounting/backups/*/
```

Add to your user's crontab (`crontab -e`) — nightly at 02:00:

```
0 2 * * * /opt/ebms/Accounting/backup.sh >> /opt/ebms/Accounting/backups/backup.log 2>&1
```

> Off-site copies: a backup on the same RAID array does not survive fire/theft. Periodically copy `/opt/ebms/Accounting/backups` to a NAS, external disk, or cloud storage.

**22. Health watchdog (auto-restart if the app goes down)**

Add to your user's crontab:

```
*/5 * * * * curl -sf --max-time 10 http://localhost/nginx-health > /dev/null || (cd /opt/ebms/Accounting && /usr/bin/docker compose restart)
```

**23. View logs at any time**

```bash
cd /opt/ebms/Accounting
docker compose logs -f web          # web app logs
docker compose logs -f api          # API logs
docker compose logs -f event-worker # background event worker
docker compose logs -f postgres     # database logs
```

---

## Phase 7: Remote Access with Tailscale (optional)

> The server stays LAN-only and invisible to the public internet — no port forwarding, no public DNS record. Tailscale creates an encrypted private network ("tailnet") between the server and the devices you enroll; only machines signed in to *your* tailnet can reach the app. The free plan covers 3 users / 100 devices.

**24. Install Tailscale on the server**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` prints a login URL — open it in any browser and sign in (Google, Microsoft, or GitHub account; the first sign-in creates your tailnet). Then get the server's permanent tailnet IP:

```bash
tailscale ip -4
# e.g. 100.101.102.103
```

No firewall changes are needed: UFW's existing 80/443 rules apply to the Tailscale interface too, and SSH also works over it (`ssh devopsrain@100.x.y.z`).

**25. Enroll each remote device**

Install the Tailscale app (Windows, macOS, iOS, Android) on each device that needs access and sign in with the **same account**. That device can now open:

```
http://100.x.y.z        (the server's tailnet IP from step 24)
```

To share with teammates beyond your own devices, invite them as users in the [Tailscale admin console](https://login.tailscale.com/admin) — each person signs in with their own account.

**26. Make HTTPS work for remote users (optional)**

Remote users hitting the raw IP get the HTTP site (or a certificate warning on HTTPS, since the certificate is for `ebms.yourdomain.com`). To give them the padlock, create the Route 53 A record after all — but pointed at the **Tailscale IP**:

- Route 53 → your hosted zone → Create record: name `ebms`, type `A`, value `100.x.y.z`

This is safe to publish: `100.64.0.0/10` addresses are unroutable on the public internet, so the record is useless to outsiders — only your tailnet members can actually connect. Office machines are unaffected: your router's local DNS mapping (step 16) overrides the public record on the LAN. Result: everyone, in the office or remote, uses the same `https://ebms.yourdomain.com`.

> **If you used the step 16 *fallback*** (public A record → LAN IP because the router can't do local DNS): the same record can't hold both IPs. Solution: enroll the **office machines in Tailscale as well**, then change the record's value from `192.168.68.121` to the Tailscale IP. Tailscale connects devices on the same LAN directly, so office users keep full local speed — and every device, everywhere, uses the one name.

---

## Phase 8: Keeping It Running

**After a server reboot** — nothing to do. The compose file uses `restart: unless-stopped`, so all containers come back automatically. Verify with `docker compose ps`.

**Deploy a code update:**

```bash
cd /opt/ebms/Accounting
git pull
docker compose up -d --build
docker compose exec web alembic upgrade head   # only if the DB schema changed
```

**Restore a backup:**

```bash
cd /opt/ebms/Accounting
gunzip -c backups/YYYYMMDD/ebms_HHMM.sql.gz | docker compose exec -T postgres psql -U ebms ebms
```

---

## Environment Variable Reference

| Variable | Where it takes effect | What to set |
|---|---|---|
| `FLASK_SECRET_KEY` | `.env` (read by `docker-compose.yml`) | **Required** — auto-generated in step 11 |
| `POSTGRES_PASSWORD` | `.env` **+ override file** (step 12) — `.env` alone is ignored because `docker-compose.yml` hardcodes it | Auto-generated in step 11 |
| `SESSION_COOKIE_SECURE` | `.env` | `1` once HTTPS is active (step 19) |
| `LOG_LEVEL` | `.env` | `INFO` for production, `DEBUG` for troubleshooting |
| `S3_BUCKET` / AWS keys | `.env` | Leave unset — local file storage is used on-prem |
| `CORS_ORIGINS` | `.env` (api service only) | Only needed if a separate frontend calls the API |

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Container stuck in `starting` | `docker compose logs postgres` — app services wait for the DB healthcheck |
| Login page does not load | `docker compose logs web` — look for Python errors |
| DB auth fails after changing password | Password only applies on first volume init — `docker compose down -v` wipes data and re-initializes (pre-go-live only) |
| Migration fails | `docker compose ps postgres` — must be healthy first |
| Port 80 refused | `sudo ufw status`, and `docker compose ps nginx` |
| App reachable on :5432/:8000 from LAN | Override file missing or not applied — check `docker compose config \| grep -A2 ports` shows `127.0.0.1` bindings |
| Cert issuance fails | DNS challenge needs valid AWS credentials in `/root/.aws/credentials` and the IAM policy scoped to the right hosted-zone ID (step 17) — no ports or nginx stop involved |
| Cert renewal fails | `sudo certbot renew --dry-run`; confirm `certbot.timer` is active (`systemctl list-timers`) and the deploy hook exists at `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` |

---

## Hardware Notes

| Spec | Recommendation |
|---|---|
| **RAM** | 32 GB ideal; 16 GB minimum — Postgres is the biggest consumer |
| **Storage** | RAID 1 across 2 drive bays; SSD strongly preferred over spinning disks |
| **iDRAC** | Configure for remote management — reboot/console without physical access |
| **BIOS** | System Profile → Performance |
| **NIC** | Onboard 1GbE is sufficient; bond both ports for redundancy |
