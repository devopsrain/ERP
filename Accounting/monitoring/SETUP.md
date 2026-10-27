# Uptime monitoring (Uptime Kuma)

Self-hosted monitor for every service on the R430, with alerts to Telegram and
e-mail. Runs as its own Compose stack in `monitoring/`; it never touches EBMS.

## 1. Start it

```bash
cd /opt/ebms/Accounting/monitoring
docker compose up -d
docker compose ps
```

Open `http://192.168.68.121:3001` (or the Tailscale IP) and create the admin
account on the first visit. Keep that password in your password manager; it is
not stored anywhere else.

Later updates: `bash deploy/update-monitoring.sh` from the repo root.

## 2. Notifications (do this before the monitors)

Settings → Notifications → Setup Notification.

**Telegram** (uses the EBMS bot you already have):
- Type *Telegram*, Bot Token = the value of `TELEGRAM_BOT_TOKEN` in `.env`.
- Chat ID: send any message to the bot, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
- Tick *Default enabled* and *Apply on all existing monitors*.

**E-mail through Resend** (same key as EBMS):
- Type *Email (SMTP)*, host `smtp.resend.com`, port `465`, security SSL/TLS,
  username `resend`, password = `RESEND_API_KEY`, from = `EMAIL_FROM`,
  to = `ADMIN_EMAIL`.

## 3. Monitors to create

Use *host.docker.internal* for anything published on the host. Interval 60 s,
retries 2, is a good default; the funnel check can be 5 min.

| Name | Type | Target | Notes |
|---|---|---|---|
| EBMS health | HTTP(s) – Keyword | `https://host.docker.internal/health` | keyword `"ok"`; **Ignore TLS error** on (cert is for the domain, not the IP) |
| EBMS login page | HTTP(s) | `https://host.docker.internal/auth/login` | expects 200 |
| EBMS public (funnel) | HTTP(s) | `https://devopsrain-poweredge-r430.tail65f932.ts.net/health` | only works if the server's Tailscale can reach itself; otherwise monitor this one externally (section 5) |
| EBMS API | HTTP(s) | `https://host.docker.internal/api/docs` | expects 200 |
| PostgreSQL | TCP Port | `host.docker.internal` : 5432 | the override binds Postgres to 127.0.0.1 — use *Docker-free* TCP via `host.docker.internal` only if reachable, else skip |
| Redis | TCP Port | `host.docker.internal` : 6379 | same caveat as Postgres |
| risk-sim liveness | HTTP(s) – Keyword | `http://host.docker.internal:8080/healthz` | keyword `ok` |
| risk-sim engine ready | HTTP(s) | `http://host.docker.internal:8080/readyz` | expects 200; fails if the numeric engine is broken |
| risk-sim latest snapshot | HTTP(s) | `http://host.docker.internal:8080/api/v1/screener/latest` | 404 until the first screener run; a persistent 404 later means the daily job stopped |
| Nextcloud | HTTP(s) | `http://host.docker.internal:8081/status.php` | expects 200 |
| TLS certificate expiry | built into the EBMS health monitor | — | Settings → *Certificate expiry notification*: 14 and 7 days |
| Disk space | Push | — | see section 4 |

Group them (Monitors → Add Group) as *EBMS*, *risk-sim*, *Nextcloud* so the
status page reads cleanly.

## 4. Disk and backup checks (push monitors)

Uptime Kuma *Push* monitors expect a ping; if none arrives inside the window
the monitor goes down. `deploy/backup.sh` already sends the pings when two
variables are present in `/opt/ebms/Accounting/.env`:

1. Create a Push monitor **Nightly backup**, heartbeat interval 25 h
   (the backup runs at 01:00). Copy its push URL into `.env` as
   `KUMA_PUSH_BACKUP=<url>`. The ping is sent only when the whole backup
   script completed, so a failed dump or S3 sync shows as a missed beat.
2. Create a Push monitor **Disk space**, heartbeat 25 h. Copy its URL as
   `KUMA_PUSH_DISK=<url>`. The script pings it only while the root disk is
   below 85 % full, so a filling disk surfaces as an alert the next night.

No cron change is needed; the next 01:00 run starts the heartbeats.

## 5. The blind spot and how to cover it

Everything above runs on the server. If the server loses power or the office
internet drops, this monitor goes down with it and cannot tell you. Cover that
with ONE external check against the public funnel address:

- Free tier of UptimeRobot, Better Stack or Hetrix: HTTP check on
  `https://devopsrain-poweredge-r430.tail65f932.ts.net/health`, 5-minute
  interval, alert to the same Telegram chat or e-mail.

That external check plus `deploy/watchdog.sh` (which already e-mails when a
container restarts) gives you both halves: outside-in and inside-out.

## 6. Security notes

- Port 3001 is reachable on the LAN and the tailnet only (the server is not
  internet-facing). To restrict it to the LAN address set
  `KUMA_BIND_IP=192.168.68.121` in `monitoring/.env`.
- The Docker socket is intentionally not mounted (see the compose file).
- Status pages (Status Pages → New) can be published without login; do not
  include internal hostnames on a page you share with clients.
