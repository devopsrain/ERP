"""
Quality Management — scheduled jobs.

``register_jobs(scheduler)`` is picked up by app.py's shared APScheduler
BackgroundScheduler. One daily job at 07:00:

* recomputes the calibration status column (valid / due_soon / expired) for
  every piece of equipment and lists what is due within 30 days or expired;
* flags CAPA records whose target date passed as ``overdue``;
* collects open customer complaints older than the SLA (14 days);
* sends one digest per company through Telegram (topic ``daily_digest``,
  when the bot is configured) and e-mail (``QUALITY_ALERT_EMAIL`` or
  ``ADMIN_EMAIL``, when Resend is configured). Both are best-effort no-ops
  otherwise, so the job never fails because a channel is missing.

Importing this module has no side effects; ``run_daily_reminders`` can be
called by hand (``python -c "import quality_jobs; print(quality_jobs.run_daily_reminders())"``).
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

JOB_ID = "quality_daily_reminders"
COMPLAINT_SLA_DAYS = 14


def _digest(company_id: str, today: date, due: list, expired: list, overdue: list, complaints: list) -> str:
    lines = [f"🧪 <b>Quality reminders — {company_id}</b> ({today.isoformat()})"]
    if expired:
        lines.append(f"\n<b>Calibration expired ({len(expired)})</b>")
        lines += [f" • {e['equipment_tag']} {e['equipment_name']} — due {e.get('next_due_date')}" for e in expired[:10]]
    if due:
        lines.append(f"\n<b>Calibration due within 30 days ({len(due)})</b>")
        lines += [f" • {e['equipment_tag']} {e['equipment_name']} — {e.get('next_due_date')}" for e in due[:10]]
    if overdue:
        lines.append(f"\n<b>Overdue CAPA ({len(overdue)})</b>")
        lines += [f" • {c['capa_no']} — {c.get('responsible_person') or 'unassigned'} — {c['days_overdue']} days" for c in overdue[:10]]
    if complaints:
        lines.append(f"\n<b>Complaints open > {COMPLAINT_SLA_DAYS} days ({len(complaints)})</b>")
        lines += [f" • {c['complaint_no']} — {c.get('customer_name')} ({c['status']})" for c in complaints[:10]]
    return "\n".join(lines)


def _notify(company_id: str, text: str) -> dict:
    sent = {"telegram": 0, "email": False}
    try:
        from telegram_bot import notify_topic
        sent["telegram"] = notify_topic(company_id, "daily_digest", text)
    except Exception as e:
        logger.debug("quality reminders: telegram unavailable: %s", e)
    to = os.environ.get("QUALITY_ALERT_EMAIL") or os.environ.get("ADMIN_EMAIL") or ""
    if to:
        try:
            from email_service import send_email
            html = "<pre style='font-family:inherit'>" + text.replace("<b>", "<strong>").replace("</b>", "</strong>") + "</pre>"
            sent["email"] = bool(send_email(to, f"EBMS quality reminders — {company_id}", html, category="quality"))
        except Exception as e:
            logger.debug("quality reminders: email unavailable: %s", e)
    return sent


def run_daily_reminders(today: Optional[date] = None) -> dict:
    """Refresh statuses and send reminders for every company. Returns a summary."""
    from quality_data_store import quality_store
    from quality_forms import complaint_is_overdue

    today = today or date.today()
    summary = {"date": today.isoformat(), "companies": {}}
    try:
        quality_store.refresh_equipment_status(today=today)
        flagged = quality_store.mark_overdue_capa(today=today)
        summary["capa_flagged_overdue"] = flagged
    except Exception as e:
        logger.error("quality job: status refresh failed: %s", e)
    for cid in quality_store.companies():
        try:
            equipment = quality_store.list_equipment(cid)
            due = [e for e in equipment if e["status"] == "due_soon"]
            expired = [e for e in equipment if e["status"] == "expired"]
            overdue = quality_store.list_capa(cid, status="overdue")
            complaints = [c for c in quality_store.list_complaints(cid)
                          if complaint_is_overdue(c["status"], c["date_received"], today, COMPLAINT_SLA_DAYS)]
            res = {"calibration_due_soon": len(due), "calibration_expired": len(expired),
                   "capa_overdue": len(overdue), "complaints_overdue": len(complaints)}
            if due or expired or overdue or complaints:
                res["sent"] = _notify(cid, _digest(cid, today, due, expired, overdue, complaints))
            summary["companies"][cid] = res
            logger.info("quality job: company=%s %s", cid, res)
        except Exception as e:  # one bad company must not stop the others
            logger.error("quality job: company=%s failed: %s", cid, e)
            summary["companies"][cid] = {"error": str(e)}
    return summary


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        run_daily_reminders,
        CronTrigger(hour=7, minute=0),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=6 * 3600,   # still run if the app was down at 07:00
        coalesce=True,
    )
    logger.info("quality: daily reminder job registered (07:00)")
