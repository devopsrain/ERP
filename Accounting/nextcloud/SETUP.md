# Nextcloud on the R430 — isolated stack

Runs as its own compose project next to EBMS and risk-sim. Own network, own
Postgres/Redis, no shared anything. Host port **8081** (8080 = risk-sim,
80/443/8000/8001/5432/6379 = EBMS).

## 1. One-time setup (on the server)

```bash
cd /opt/ebms/Accounting/nextcloud

# Pick where user files live — must be the big disk, not the OS root:
df -h                      # find your large mount
sudo mkdir -p /srv/nextcloud-data
sudo chown 33:33 /srv/nextcloud-data      # uid 33 = www-data in the image

# Create the (gitignored) .env:
cat > .env <<'EOF'
NEXTCLOUD_DB_PASSWORD=REPLACE-with-a-long-random-string
NEXTCLOUD_DATA_DIR=/srv/nextcloud-data
EOF

docker compose up -d
```

First visit: create the admin account in the web UI.

## 2. Background jobs (after first login)

Admin settings → Basic settings → Background jobs → select **Cron**. Nothing
else to do — the `nextcloud-cron` sidecar container runs cron.php every
5 minutes (no host crontab entry needed).

## 3. Access

**LAN / Tailscale (private — recommended):** `http://192.168.68.121:8081`
works immediately for anyone on the LAN or the tailnet (subnet route).
For proper HTTPS inside the tailnet without touching the EBMS funnel:

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8081
# → https://devopsrain-poweredge-r430.tail65f932.ts.net:8443  (tailnet only)
```

**Public (optional):** promote that same listener to a funnel — this does NOT
touch the existing 443 funnel that serves EBMS:

```bash
sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8081
# → https://devopsrain-poweredge-r430.tail65f932.ts.net:8443  (public internet)
```

⚠️ Never run `tailscale funnel 8080/8081/...` without `--https=8443` — the
bare form replaces the port-443 funnel and takes down public EBMS access.

**Custom domain (cloud.devopsrain.com):** possible later via the EBMS nginx
container + a certbot dns-route53 cert, but note nginx runs in a container —
it must reach Nextcloud via the host gateway (`172.17.0.1:8081` or an
`extra_hosts: host.docker.internal:host-gateway` entry), NOT `127.0.0.1`.
Ask for this when wanted; it needs a Route 53 record + nginx.conf change.

## 3b. How to upload files

- **Browser:** open the URL → drag-and-drop files/folders anywhere in the Files
  view, or the + button → Upload. Large files are chunked automatically
  (limit set to 10 GB per file).
- **Desktop sync (recommended for bulk):** install the Nextcloud desktop client
  (Windows/macOS/Linux), point it at the URL — a chosen local folder then syncs
  both ways like OneDrive/Dropbox. Best method for large media libraries:
  copy files into the folder and let it sync in the background.
- **Phone:** Nextcloud app (iOS/Android) — enables **automatic photo/video
  upload** from the camera roll. Works over Tailscale when the phone is
  enrolled in the tailnet.
- **Network drive (no client):** WebDAV — map
  `https://<host>/remote.php/dav/files/<username>/` as a drive in Windows
  Explorer or macOS Finder and copy files natively.
- **From other people:** create a share link on any folder with
  **"File drop"** permission — outsiders can upload into it via browser
  without an account (needs the funnel listener if they're off your network).

## 4. Backups

Nextcloud is NOT covered by the EBMS backup script. Minimum viable:

```bash
docker exec nextcloud-postgres pg_dump -U nextcloud nextcloud | gzip > /srv/nextcloud-data/../nextcloud-db-$(date +%F).sql.gz
```

User files live in `NEXTCLOUD_DATA_DIR` — include that path (and the DB dump)
in whatever off-site sync you enable (see deploy/S3_BACKUP_SETUP.md pattern).

## 5. Updates

```bash
cd /opt/ebms/Accounting/nextcloud
docker compose pull && docker compose up -d
```

## 6. Connecting EBMS to Nextcloud

EBMS has a **Documents** module (`/documents`) that stores uploads in this
Nextcloud instance over WebDAV and creates public share links via the OCS API.
Until it is configured, uploads fall back to local disk inside the `web`
container (`DOCUMENTS_LOCAL_DIR`, default `/tmp/ebms_documents`).

### 6.1 Create a dedicated Nextcloud user

Nextcloud → (admin) → **Users** → **New user**: username `ebms`, a strong
password, optional quota. Never point EBMS at the admin account.

### 6.2 Generate an App Password

Log in **as `ebms`** → avatar → **Settings → Security → Devices & sessions**
→ App name `EBMS` → **Create new app password**. Copy the
`xxxxx-xxxxx-xxxxx-xxxxx-xxxxx` token now — it is shown only once. (App
passwords keep working when 2FA is enabled and can be revoked individually.)

### 6.3 Configure EBMS

Add the four variables to the **EBMS** env file (not the Nextcloud one):

```bash
cat >> /opt/ebms/Accounting/.env <<'EOF2'
NEXTCLOUD_URL=http://host.docker.internal:8081
NEXTCLOUD_USER=ebms
NEXTCLOUD_APP_PASSWORD=xxxxx-xxxxx-xxxxx-xxxxx-xxxxx
NEXTCLOUD_ROOT=EBMS
EOF2
```

`host.docker.internal` works because `docker-compose.yml` sets
`extra_hosts: host.docker.internal:host-gateway` on the `web` and `api`
services. If it does not resolve on your Docker version, use the LAN IP
instead: `NEXTCLOUD_URL=http://192.168.68.121:8081` (it is in
`NEXTCLOUD_TRUSTED_DOMAINS` already). Do **not** use `127.0.0.1` — that is the
container itself.

Optional: `DOCUMENTS_LOCAL_DIR=/app/web/data/documents` to keep the local
fallback on the persistent `app_data` volume; `DOCUMENTS_MAX_MB` (default 100).

### 6.4 Restart and test

```bash
cd /opt/ebms/Accounting
docker compose up -d web api
```

Log in to EBMS as an admin and open **`/documents/settings`**:

- **Test connection** — shows the Nextcloud version, latency and whether the
  `/EBMS` root folder exists.
- **Create folder structure** — creates `/EBMS/<company>/<module>` folders
  (uploads also create folders on demand, so this is optional).

Files land in `/EBMS/<company_id>/<module>/<record_id>/<uuid>_<filename>` in
the `ebms` account and are visible in the Nextcloud web UI, desktop client
and phone app. Files dropped there by other means show up on
`/documents/browse` as **new** with an *Import into EBMS* button.

If Nextcloud is down, EBMS keeps working: pages show "Nextcloud unreachable",
uploads are stored locally (flagged on the document), downloads of
Nextcloud-backed files report an error instead of crashing.
