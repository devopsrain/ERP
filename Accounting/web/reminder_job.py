"""
Bid deadline reminder job.

send_due_reminders() emails the case handler (fallback: ADMIN_EMAIL) for every
bid whose deadline falls within its reminder window, then marks the bid so it
is never reminded twice. Scheduled daily from the app lifespan (see app.py);
can also be run manually:

    docker compose exec web python -c "from reminder_job import send_due_reminders; print(send_due_reminders())"

Never raises — a failure is logged and the job simply tries again next run.
"""

import logging
import os
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Statuses that still need a reminder (anything not yet decided/closed).
REMINDABLE_STATUSES = {"open", "submitted", "pending", "in_progress", "in progress", "draft"}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d.%m.%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
)


def parse_deadline(value) -> "date | None":
    """Parse a deadline stored as TEXT (or date/datetime) defensively."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # ISO first (handles 'YYYY-MM-DD' and 'YYYY-MM-DDTHH:MM:SS...')
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def is_due_for_reminder(deadline_value, reminder_days_before, today: date = None) -> bool:
    """
    True when the deadline is today or within `reminder_days_before` days
    ahead of `today`. Overdue or unparseable deadlines return False.
    """
    deadline = parse_deadline(deadline_value)
    if deadline is None:
        return False
    try:
        days = max(int(reminder_days_before), 0)
    except (TypeError, ValueError):
        days = 3
    today = today or date.today()
    delta = (deadline - today).days
    return 0 <= delta <= days


def send_due_reminders() -> int:
    """
    Email reminders for bids whose deadline is inside the reminder window.
    Returns the number of reminders successfully sent. Never raises.
    """
    try:
        from db import get_cursor
        from email_service import send_email
    except Exception as e:
        logger.error("reminder_job imports failed: %s", e)
        return 0

    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()

    try:
        with get_cursor() as cur:
            cur.execute(
                """SELECT id, title, reference_number, organization, status,
                          deadline, reminder_days_before, case_handler_name,
                          case_handler_email
                   FROM bid_records
                   WHERE reminder_sent = FALSE"""
            )
            candidates = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("reminder_job query failed: %s", e)
        return 0

    sent = 0
    today = date.today()
    for bid in candidates:
        status = str(bid.get("status") or "").strip().lower()
        if status not in REMINDABLE_STATUSES:
            continue
        if not is_due_for_reminder(bid.get("deadline"),
                                   bid.get("reminder_days_before"), today):
            continue

        recipient = (bid.get("case_handler_email") or "").strip() or admin_email
        if not recipient:
            logger.warning("reminder_skipped (no recipient): bid=%s %r",
                           bid.get("id"), bid.get("title"))
            continue

        title    = bid.get("title") or "(untitled bid)"
        ref      = bid.get("reference_number") or "-"
        deadline = bid.get("deadline") or "-"
        subject  = f"Bid deadline reminder: {title} (due {deadline})"
        html = (
            "<h3>Bid deadline approaching</h3>"
            f"<p><b>Title:</b> {title}</p>"
            f"<p><b>Reference:</b> {ref}</p>"
            f"<p><b>Organization:</b> {bid.get('organization') or '-'}</p>"
            f"<p><b>Deadline:</b> {deadline}</p>"
            f"<p><b>Status:</b> {bid.get('status') or '-'}</p>"
            "<p>Please make sure the submission is on track.</p>"
        )

        if not send_email(recipient, subject, html, category="bid_reminder"):
            continue                      # retry on the next run
        try:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE bid_records SET reminder_sent = TRUE WHERE id = %s",
                    (bid["id"],)
                )
            sent += 1
            logger.info("reminder_sent: bid=%s to=%s deadline=%s",
                        bid.get("id"), recipient, deadline)
        except Exception as e:
            logger.error("reminder_sent flag update failed for bid %s: %s",
                         bid.get("id"), e)

    logger.info("send_due_reminders complete: %d candidate(s), %d sent",
                len(candidates), sent)
    return sent
