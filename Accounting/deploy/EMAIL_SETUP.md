# Email Notifications — Resend Setup

EBMS sends email through the [Resend](https://resend.com) HTTP API (no SDK — a
plain POST to `https://api.resend.com/emails`). Email is used for:

- **Bid deadline reminders** — daily 08:00 job (`web/reminder_job.py`) emails the
  bid's case handler (fallback: `ADMIN_EMAIL`) when a deadline enters its
  reminder window, then marks the bid `reminder_sent`.
- **Security alerts** — SIEM `critical`/`high` alerts email the admin
  (`web/email_service.py: alert_on_critical`).
- **Watchdog restarts** — `deploy/watchdog.sh` emails the admin when the cron
  health check restarts the compose stack.

When `RESEND_API_KEY` is unset, all of this is a silent no-op (a log line only)
— nothing breaks.

## 1. Create a Resend account and verify the domain

1. Sign up at https://resend.com (free tier: 100 emails/day).
2. **Domains → Add Domain** → `devopsrain.com`.
3. Resend shows DNS records (SPF/TXT, DKIM CNAME/TXT, optional DMARC). Add them
   in **AWS Route 53** → hosted zone `devopsrain.com` → *Create record* for each
   entry exactly as shown (name, type, value).
4. Wait for the domain status to become **Verified** (usually minutes).

Until the domain is verified you can test with the sandbox sender
`onboarding@resend.dev` (the built-in default), which can only deliver to your
own Resend account email.

## 2. Get an API key

**API Keys → Create API Key** (Sending access is enough). Copy the `re_...` key
— it is shown only once.

## 3. Configure the server

Append to `/opt/ebms/Accounting/.env`:

```dotenv
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxxxxxx
EMAIL_FROM=EBMS <noreply@devopsrain.com>
ADMIN_EMAIL=fde@banknorwegian.no
```

Then recreate the containers so the vars are injected (they are wired into the
`&common-env` block of `docker-compose.yml`):

```bash
cd /opt/ebms/Accounting && docker compose up -d
```

Re-run `bash deploy/setup-cron.sh` once so the watchdog cron entry points at
`deploy/watchdog.sh` (which contains the email step).

## 4. Test

```bash
docker compose exec web python -c "from email_service import send_email; print(send_email('you@example.com', 'EBMS test', '<p>It works.</p>'))"
```

`True` = sent (check the inbox and Resend dashboard → Emails). `False` = check
the container logs: `docker compose logs web | grep email_`.

Run the reminder job manually:

```bash
docker compose exec web python -c "from reminder_job import send_due_reminders; print(send_due_reminders())"
```
