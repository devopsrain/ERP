"""
Scheduled report runs.

register_jobs(scheduler) is discovered by app.py and hooks `run_due_schedules`
onto the shared APScheduler BackgroundScheduler every 15 minutes. Each due
schedule (next_run_at <= now) is executed, rendered to its format, stored
under <tempdir>/ebms_reports/, emailed via reports_mailer and re-armed.

compute_next_run() is pure (unit-tested) and shared with the routes.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

JOB_ID = "reports_scheduled_runs"
INTERVAL_MINUTES = 15
FREQUENCIES = ("daily", "weekly", "monthly")
FORMATS = ("pdf", "xlsx", "csv")


# ── next-run computation (pure) ─────────────────────────────────────

def compute_next_run(frequency: str, hour: int, minute: int, weekday: Optional[int] = None,
                     day_of_month: Optional[int] = None, after: Optional[datetime] = None) -> datetime:
    """First run time strictly AFTER `after` (default: now, naive local time).

    daily   – every day at hour:minute
    weekly  – on `weekday` (0=Mon … 6=Sun) at hour:minute
    monthly – on `day_of_month` (1..31, clamped to the month's length) at hour:minute
    """
    after = after or datetime.now()
    after = after.replace(second=0, microsecond=0) if after.second or after.microsecond else after
    hour = min(max(int(hour or 0), 0), 23)
    minute = min(max(int(minute or 0), 0), 59)
    freq = (frequency or "daily").lower()
    if freq not in FREQUENCIES:
        raise ValueError(f"unknown frequency {frequency!r}")

    if freq == "daily":
        cand = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return cand if cand > after else cand + timedelta(days=1)

    if freq == "weekly":
        wd = int(weekday if weekday is not None else 0) % 7
        cand = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        cand += timedelta(days=(wd - cand.weekday()) % 7)
        return cand if cand > after else cand + timedelta(days=7)

    dom = min(max(int(day_of_month or 1), 1), 31)
    year, month = after.year, after.month
    for _ in range(3):
        day = min(dom, calendar.monthrange(year, month)[1])
        cand = datetime(year, month, day, hour, minute)
        if cand > after:
            return cand
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return cand  # pragma: no cover — loop above always returns


def describe_schedule(s: dict) -> str:
    """Human summary, e.g. 'Weekly on Monday at 07:00'."""
    t = f"{int(s.get('hour') or 0):02d}:{int(s.get('minute') or 0):02d}"
    freq = (s.get("frequency") or "daily").lower()
    if freq == "weekly":
        wd = int(s.get("weekday") or 0) % 7
        return f"Weekly on {calendar.day_name[wd]} at {t}"
    if freq == "monthly":
        return f"Monthly on day {int(s.get('day_of_month') or 1)} at {t}"
    return f"Daily at {t}"


# ── execution ───────────────────────────────────────────────────────

def run_schedule(schedule: dict, triggered_by: str = "scheduler", now: Optional[datetime] = None) -> dict:
    """Run one schedule end-to-end. Returns {'ok': bool, 'run_id', 'path', 'emailed', 'error'}."""
    from reports_data_store import report_store
    from reports_engine import MIME, build_meta, render, run_report, safe_filename, save_output
    from reports_mailer import parse_recipients, send_report_email

    now = now or datetime.now()
    cid = schedule["company_id"]
    fmt = (schedule.get("format") or "pdf").lower()
    if fmt not in FORMATS:
        fmt = "pdf"
    run_id = report_store.start_run(cid, schedule["report_id"], triggered_by,
                                    schedule_id=schedule.get("id"), output_format=fmt)
    out = {"ok": False, "run_id": run_id, "path": None, "emailed": False, "error": None}
    try:
        defn = report_store.get_definition(schedule["report_id"], cid)
        if not defn:
            raise RuntimeError("report definition not found")
        result = run_report(defn, cid)
        meta = build_meta(defn, cid, result, now)
        content = render(result, meta, fmt, defn.get("chart"))
        path = save_output(content, defn.get("name") or "report", fmt)
        out["path"] = path

        recipients = parse_recipients(schedule.get("recipients"))
        if recipients:
            subject = (schedule.get("subject") or "").strip() or f"[EBMS] {defn.get('name')} — {now:%Y-%m-%d}"
            html = (
                f"<h3>{_esc(defn.get('name') or 'Report')}</h3>"
                f"<p><b>Company:</b> {_esc(meta['company_name'])}<br>"
                f"<b>Generated:</b> {meta['generated_at']}<br>"
                f"<b>Rows:</b> {result.row_count:,}<br>"
                f"<b>Filters:</b> {_esc(meta['filters_summary'])}</p>"
                f"<p>The report is attached as {fmt.upper()}.</p>"
                "<p style='color:#888;font-size:12px'>Sent automatically by EBMS Report Builder.</p>"
            )
            out["emailed"] = send_report_email(recipients, subject, html,
                                               [(safe_filename(defn.get("name") or "report", fmt), content, MIME[fmt])])
        report_store.finish_run(run_id, "ok", result.row_count, None, path)
        out["ok"] = True
    except Exception as e:
        logger.error("report schedule %s failed: %s", schedule.get("id"), e)
        out["error"] = str(e)
        report_store.finish_run(run_id, "error", 0, str(e), None)
    finally:
        if schedule.get("id"):
            try:
                nxt = compute_next_run(schedule.get("frequency"), schedule.get("hour"), schedule.get("minute"),
                                       schedule.get("weekday"), schedule.get("day_of_month"), after=now)
                report_store.mark_schedule_run(schedule["id"], now, nxt)
            except Exception as e:
                logger.error("mark_schedule_run %s: %s", schedule.get("id"), e)
    return out


def run_due_schedules(now: Optional[datetime] = None) -> int:
    """Run every active schedule whose next_run_at <= now. Returns number processed. Never raises."""
    try:
        from reports_data_store import report_store
        now = now or datetime.now()
        due = report_store.due_schedules(now)
        for s in due:
            run_schedule(s, "scheduler", now)
        if due:
            logger.info("reports_jobs: processed %d due schedule(s)", len(due))
        return len(due)
    except Exception as e:
        logger.error("reports_jobs.run_due_schedules failed: %s", e)
        return 0


def register_jobs(scheduler) -> None:
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(run_due_schedules, trigger=IntervalTrigger(minutes=INTERVAL_MINUTES),
                      id=JOB_ID, replace_existing=True, coalesce=True, max_instances=1,
                      misfire_grace_time=600)
    logger.info("reports_jobs: %s registered (every %d min)", JOB_ID, INTERVAL_MINUTES)


def _esc(s) -> str:
    import html
    return html.escape(str(s or ""))
