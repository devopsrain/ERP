"""
Approval Engine scheduled jobs.

``register_jobs(scheduler)`` is called by app.py with the shared APScheduler
BackgroundScheduler. Importing this module has no side effects.

Daily 08:30 — email the current approvers (and the admin) about requests that
have been pending longer than APPROVAL_ESCALATION_DAYS (default 3).
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

JOB_ID = "approval_escalation_reminders"


def _row(req: dict) -> str:
    return (f"<li><b>{req.get('title','')}</b> — {req.get('entity_type','')} · "
            f"{req.get('currency','')} {req.get('amount_display','—')} · requested by "
            f"{req.get('requested_by','')} · pending {req.get('age_days', 0)} days · "
            f"step {req.get('current_step')} · <a href=\"/approvals/requests/{req.get('id','')}\">open</a></li>")


def send_escalation_reminders(days: int = None) -> int:
    """Send reminder emails for overdue pending requests. Returns the number of
    reminder emails attempted. Never raises."""
    try:
        from approval_data_store import approval_store, ESCALATION_DAYS
        from email_service import notify_admin, send_email

        days = ESCALATION_DAYS if days is None else int(days)
        overdue = approval_store.get_overdue(days)
        if not overdue:
            logger.info("approval reminders: nothing pending > %s days", days)
            return 0

        # approver -> [requests], per company (emails are looked up per company)
        per_company: Dict[str, Dict[str, List[dict]]] = {}
        for req in overdue:
            approvers = approval_store.current_approvers(req) or []
            bucket = per_company.setdefault(req["company_id"], {})
            for a in approvers:
                bucket.setdefault(a, []).append(req)

        sent = 0
        for cid, by_user in per_company.items():
            for username, reqs in by_user.items():
                emails = approval_store._emails_for([username], cid)
                if not emails:
                    continue
                html = (f"<h3>EBMS — {len(reqs)} approval(s) waiting for you</h3>"
                        f"<p>These requests have been pending for more than {days} day(s):</p>"
                        f"<ul>{''.join(_row(r) for r in reqs)}</ul>"
                        f"<p>Open your inbox: /approvals/inbox</p>")
                send_email(emails, f"[EBMS] {len(reqs)} approval(s) overdue", html, category="approval_reminder")
                sent += 1

        summary = (f"<h3>EBMS approval escalation</h3><p>{len(overdue)} request(s) pending "
                   f"longer than {days} day(s).</p><ul>{''.join(_row(r) for r in overdue)}</ul>")
        notify_admin(f"[EBMS] {len(overdue)} approval request(s) overdue", summary)
        logger.info("approval reminders: %s overdue, %s approver emails", len(overdue), sent)
        return sent
    except Exception as e:
        logger.error("approval reminders failed: %s", e)
        return 0


def register_jobs(scheduler) -> None:
    """Attach the daily 08:30 escalation job to the shared scheduler."""
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        send_escalation_reminders,
        trigger=CronTrigger(hour=8, minute=30),
        id=JOB_ID,
        replace_existing=True,
    )
