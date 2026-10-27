"""
Security Operations — scheduled jobs.

``register_jobs(scheduler)`` is picked up by app.py's shared APScheduler
BackgroundScheduler.

Daily 06:00 (``run_daily``)
  * marks yesterday's scheduled-but-no-attendance duties as ``absent``
    (published rosters only);
  * licence / firearm permit / medical fitness reminders at 30 and 7 days
    before expiry, and on/after the expiry date;
  * today's uncovered-posts digest (required guards − rostered guards);
  * one Telegram digest per company (topic ``daily_digest``) and an e-mail to
    ``SECOPS_ALERT_EMAIL`` / ``ADMIN_EMAIL`` when configured — both best-effort.

Monthly, 1st 06:30 (``run_monthly``)
  * builds the attendance → payroll feed for the previous month for every
    company (draft; an already-exported period is left untouched).

Importing this module has no side effects; both runners can be called by
hand, e.g. ``python -c "import secops_jobs; print(secops_jobs.run_daily())"``.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DAILY_JOB_ID = "secops_daily"
MONTHLY_JOB_ID = "secops_monthly_payroll_feed"
REMINDER_HORIZONS = (30, 7)


def _notify(company_id: str, text: str) -> dict:
    sent = {"telegram": 0, "email": False}
    try:
        from telegram_bot import notify_topic
        sent["telegram"] = notify_topic(company_id, "daily_digest", text)
    except Exception as e:
        logger.debug("secops jobs: telegram unavailable: %s", e)
    to = os.environ.get("SECOPS_ALERT_EMAIL") or os.environ.get("ADMIN_EMAIL") or ""
    if to:
        try:
            from email_service import send_email
            html = "<pre style='font-family:inherit'>" + text.replace("<b>", "<strong>").replace("</b>", "</strong>") + "</pre>"
            sent["email"] = bool(send_email(to, f"EBMS security operations — {company_id}", html, category="secops"))
        except Exception as e:
            logger.debug("secops jobs: email unavailable: %s", e)
    return sent


def licence_reminders(guards: list, today: date) -> list[dict]:
    """Pure helper: which guard documents hit a reminder horizon today."""
    import secops_logic as L
    out = []
    for g in guards:
        if g.get("status") == "exited":
            continue
        for field, label in L.DOCUMENT_FIELDS:
            hit = L.reminder_due(g.get(field), today, REMINDER_HORIZONS)
            if hit is None:
                continue
            out.append({"guard_no": g.get("guard_no"), "name": g.get("name") or g.get("employee_id"), "document": label,
                        "expiry": L.parse_date(g.get(field)), "days": hit, "expired": hit == 0})
    return out


def _digest(company_id: str, today: date, absent: int, reminders: list, coverage: dict) -> str:
    lines = [f"🛡 <b>Security operations — {company_id}</b> ({today.isoformat()})"]
    if absent:
        lines.append(f"\n<b>No-shows marked absent for {(today - timedelta(days=1)).isoformat()}:</b> {absent}")
    uncovered = [s for s in coverage.get("sites", []) if s.get("uncovered")]
    if uncovered:
        lines.append(f"\n<b>Uncovered posts today ({coverage.get('posts_uncovered', 0)} guard-shifts)</b>")
        lines += [f" • {s['site_name']} — {s['uncovered']} missing (required {s['required']}, rostered {s['assigned']})" for s in uncovered[:15]]
    no_roster = [s for s in coverage.get("sites", []) if s.get("required") and not s.get("roster_id")]
    if no_roster:
        lines.append(f"\n<b>Sites without a roster today ({len(no_roster)})</b>")
        lines += [f" • {s['site_name']}" for s in no_roster[:15]]
    if reminders:
        lines.append(f"\n<b>Licence / permit reminders ({len(reminders)})</b>")
        for r in reminders[:20]:
            when = "EXPIRED" if r["expired"] else f"expires in {r['days']} days"
            lines.append(f" • {r['guard_no']} {r['name']} — {r['document']} {when} ({r['expiry']})")
    return "\n".join(lines)


def run_daily(today: Optional[date] = None) -> dict:
    """Absent marking + reminders + coverage digest for every company. Returns a summary."""
    from secops_data_store import secops_store
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    summary = {"date": today.isoformat(), "companies": {}}
    for cid in secops_store.companies():
        try:
            absent = secops_store.mark_absent(cid, yesterday)
            reminders = licence_reminders(secops_store.list_guards(cid), today)
            coverage = secops_store.coverage_summary(cid, today)
            res = {"marked_absent": absent, "reminders": len(reminders), "posts_uncovered": coverage.get("posts_uncovered", 0)}
            if absent or reminders or coverage.get("posts_uncovered"):
                res["sent"] = _notify(cid, _digest(cid, today, absent, reminders, coverage))
            summary["companies"][cid] = res
            logger.info("secops daily: company=%s %s", cid, res)
        except Exception as e:  # one bad company must not stop the others
            logger.error("secops daily: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def run_monthly(today: Optional[date] = None) -> dict:
    """Generate the previous month's payroll feed (draft) for every company."""
    from secops_data_store import secops_store
    import secops_logic as L
    today = today or date.today()
    period = L.previous_month(today)
    summary = {"period": period, "companies": {}}
    for cid in secops_store.companies():
        try:
            res = secops_store.generate_feed(cid, period, force=False)
            summary["companies"][cid] = res
            if not res.get("error") and res.get("rows"):
                _notify(cid, f"💰 <b>Payroll feed {period}</b> generated for {res['rows']} guard(s) — review it under Security Operations → Payroll Feed.")
            logger.info("secops monthly: company=%s %s", cid, res)
        except Exception as e:
            logger.error("secops monthly: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(run_daily, CronTrigger(hour=6, minute=0), id=DAILY_JOB_ID, replace_existing=True,
                      misfire_grace_time=6 * 3600, coalesce=True)
    scheduler.add_job(run_monthly, CronTrigger(day=1, hour=6, minute=30), id=MONTHLY_JOB_ID, replace_existing=True,
                      misfire_grace_time=24 * 3600, coalesce=True)
    logger.info("secops: daily job (06:00) and monthly payroll feed (1st 06:30) registered")
