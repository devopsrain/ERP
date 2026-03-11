"""
Background Worker — Ethiopian Business Management System

Run this as a SEPARATE PROCESS from the web server so that long-running
scheduled jobs (backups, payroll reports) do not block FastAPI route
handlers or delay HTTP responses.

Usage (on the server):
    # Start the worker in the background (keeps running)
    python worker.py &

    # Or as a systemd service (recommended for production):
    # WorkingDirectory=/opt/ethiopian-business/web
    # ExecStart=python /opt/ethiopian-business/web/worker.py

    # Or via supervisor (same as the web process):
    # [program:ethiopian-worker]
    # command=python /opt/ethiopian-business/web/worker.py
    # directory=/opt/ethiopian-business/web

The worker imports the same data stores and DB connection pool as the
web app — ensure DATABASE_URL is set in the environment before starting.
"""
from __future__ import annotations

import logging
import os
import sys
import signal
import time

# Add parent directory so absolute imports work the same as in app.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker")

# ── AWS Secrets (no-op outside AWS) ─────────────────────────────
try:
    from secrets_loader import load_secrets
    load_secrets()
except ImportError:
    pass


def _start_backup_scheduler():
    """Schedule daily backups at 01:00 via APScheduler (or thread fallback)."""
    try:
        from backup_data_store import BackupEngine, BackupScheduler
        engine = BackupEngine()
        scheduler = BackupScheduler(engine, hour=1)
        scheduler.start()
        logger.info("Daily backup scheduler started (next run: %s)", scheduler.next_run)
        return scheduler
    except Exception as e:
        logger.error("Failed to start backup scheduler: %s", e)
        return None


def _start_purge_scheduler():
    """Schedule weekly purge of backups older than 30 days."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from backup_data_store import BackupEngine

        engine = BackupEngine()
        sched = BackgroundScheduler(daemon=True)
        sched.add_job(
            lambda: logger.info(
                "Purged %d old backups", engine.purge_old_backups(keep_days=30)
            ),
            trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
            id="weekly_purge",
            replace_existing=True,
        )
        sched.start()
        logger.info("Weekly purge scheduler started (Sundays at 02:00)")
        return sched
    except ImportError:
        logger.warning("APScheduler not installed — weekly purge disabled")
        return None
    except Exception as e:
        logger.error("Failed to start purge scheduler: %s", e)
        return None


def main():
    logger.info("═══════════════════════════════════════════════")
    logger.info("  Ethiopian Business Suite — Background Worker ")
    logger.info("═══════════════════════════════════════════════")

    schedulers = []

    backup_sched = _start_backup_scheduler()
    if backup_sched:
        schedulers.append(backup_sched)

    purge_sched = _start_purge_scheduler()
    if purge_sched:
        schedulers.append(purge_sched)

    if not schedulers:
        logger.error("No schedulers started — worker exiting.")
        sys.exit(1)

    # Graceful shutdown on SIGTERM / SIGINT
    _running = [True]

    def _stop(sig, frame):
        logger.info("Worker shutting down (signal %s)…", sig)
        _running[0] = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("Worker running. Press Ctrl-C to stop.")
    while _running[0]:
        time.sleep(5)

    # Stop all schedulers
    for s in schedulers:
        try:
            if hasattr(s, "stop"):
                s.stop()
            elif hasattr(s, "shutdown"):
                s.shutdown(wait=False)
        except Exception:
            pass

    logger.info("Worker stopped cleanly.")


if __name__ == "__main__":
    main()
