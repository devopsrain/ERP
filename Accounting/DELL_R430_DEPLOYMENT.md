# Dell R430 — Fresh Deployment Guide

> Ethiopian Business Suite — self-hosted on Dell PowerEdge R430

---

## Phase 1: OS & Base Setup

### 1. Install Ubuntu Server 22.04 LTS (or 24.04)

- Boot from USB, minimal install, enable OpenSSH server
- Set static IP for your LAN (or the public IP you'll use)

### 2. Initial hardening

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw fail2ban
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

### 3. Install Docker Engine + Compose

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out/in, then verify:
docker compose version
```

---

## Phase 2: Deploy the App

### 4. Clone your repo

```bash
cd /opt
sudo mkdir ebms && sudo chown $USER:$USER ebms
git clone <your-repo-url> ebms
cd ebms
```

### 5. Create `.env` file (production secrets)

```bash
cat > .env << 'EOF'
FLASK_SECRET_KEY=<generate-with: python3 -c "import secrets; print(secrets.token_hex(32))">
SESSION_COOKIE_SECURE=1
LOG_LEVEL=INFO
POSTGRES_USER=ebms
POSTGRES_PASSWORD=<strong-password-here>
POSTGRES_DB=ebms
DATABASE_URL=postgresql://ebms:<same-password>@postgres:5432/ebms
REDIS_URL=redis://redis:6379/0
EOF
```

### 6. Launch everything

```bash
docker compose up -d --build
```

This starts all 6 services: **postgres → redis → web → api → event-worker → nginx**

### 7. Run database migrations

```bash
docker compose exec web alembic upgrade head
```

### 8. Verify

```bash
docker compose ps                    # all "Up"
curl -s http://localhost/auth/login   # should return HTML
curl -s http://localhost/nginx-health # "OK"
```

---

## Phase 3: Networking & TLS

### 9. Point your domain

Set an A record to the R430's public IP.

### 10. TLS with Let's Encrypt (free)

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d yourdomain.com
# Copy certs into the nginx volume:
docker compose cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx:/etc/nginx/ssl/
docker compose cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx:/etc/nginx/ssl/
```

Then uncomment the HTTPS server block in `nginx.conf` and restart:

```bash
docker compose restart nginx
```

### 11. Auto-renew certs

Add to crontab:

```
0 3 * * * certbot renew --quiet && docker compose restart nginx
```

---

## Phase 4: Backups & Monitoring

### 12. Automated PostgreSQL backups

```bash
cat > /opt/ebms/backup.sh << 'SCRIPT'
#!/bin/bash
BACKUP_DIR=/opt/ebms/backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
docker compose exec -T postgres pg_dump -U ebms ebms | gzip > "$BACKUP_DIR/ebms_$(date +%H%M).sql.gz"
find /opt/ebms/backups -mtime +30 -delete  # keep 30 days
SCRIPT
chmod +x /opt/ebms/backup.sh
```

Crontab entry:

```
0 2 * * * /opt/ebms/backup.sh
```

### 13. Health monitoring (simple watchdog)

Add to crontab (every 5 min):

```
*/5 * * * * curl -sf http://localhost/nginx-health || docker compose restart
```

---

## R430 Hardware Notes

| Spec | Recommendation |
|------|---------------|
| **RAM** | 32 GB is ideal; 16 GB minimum — Postgres is the biggest consumer |
| **Storage** | RAID 1 on the 2 drive bays for redundancy; consider SSD over spinning disks |
| **iDRAC** | Configure iDRAC for remote management — reboot/console without physical access |
| **BIOS** | Set power profile to "Performance" mode |
| **NIC** | Use the onboard 1GbE; bond both ports if you need redundancy |

---

## What You No Longer Need (vs AWS)

| AWS Service | Replaced By |
|-------------|-------------|
| RDS | Postgres in Docker locally |
| ALB | Nginx handles reverse proxy locally |
| S3 | Docker volume or local NAS mount |
| Route 53 | Any DNS provider (Cloudflare free tier works) |
| EC2 | Your own hardware |
| CloudWatch | Cron watchdog + Docker logs |

`S3_BUCKET` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `.env` can be left empty unless you want off-site file storage.

---

## Environment Variables Reference

| Variable | Default | Notes |
|----------|---------|-------|
| `FLASK_SECRET_KEY` | `change-me-in-production` | **Must change** — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `postgresql://ebms:ebms@postgres:5432/ebms` | Match `POSTGRES_PASSWORD` |
| `REDIS_URL` | `redis://redis:6379/0` | Default is fine for single-host |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` once HTTPS is active |
| `LOG_LEVEL` | `INFO` | `DEBUG` for troubleshooting |
| `CORS_ORIGINS` | `http://localhost:3000,...` | Only needed if running a separate frontend |
| `DB_SYNC_POOL_MAX` | `10` | Increase if you have >50 concurrent users |
| `DB_POOL_MAX_SIZE` | `15` | Async pool — increase with concurrency |

---

## Quick-Start One-Liner

After OS + Docker are installed:

```bash
cd /opt/ebms && cp .env.example .env && nano .env && docker compose up -d --build && docker compose exec web alembic upgrade head
```
