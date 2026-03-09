"""
Backup Data Store - PostgreSQL-backed backup engine with scheduler
Exports: backup_engine (BackupEngine), backup_scheduler (BackupScheduler)
"""

import logging
import os
import uuid
import zipfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler as _APSScheduler
    from apscheduler.triggers.cron import CronTrigger as _CronTrigger
    _APS_AVAILABLE = True
except ImportError:
    _APS_AVAILABLE = False

from db import get_cursor

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).parent / "data" / "backups"
DATA_DIR   = Path(__file__).parent / "data"


class BackupEngine:
    """Creates zip archives of web/data/, logs to backup_log table."""

    @property
    def backup_dir(self) -> str:
        return str(BACKUP_DIR)

    # ------------------------------------------------------------------
    # Stats & Listing
    # ------------------------------------------------------------------
    def get_stats(self) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) AS total,
                       COALESCE(SUM(compressed_size),0) AS total_size,
                       COALESCE(SUM(file_count),0) AS total_files
                       FROM backup_log"""
                )
                row = cur.fetchone()
                on_disk = len(list(BACKUP_DIR.glob('*.zip'))) if BACKUP_DIR.exists() else 0
                return {
                    'total_backups': int(row['total']) if row else 0,
                    'total_size': int(row['total_size']) if row else 0,
                    'total_files': int(row['total_files']) if row else 0,
                    'archives_on_disk': on_disk,
                }
        except Exception as e:
            logger.error("get_stats failed: %s", e)
            return {'total_backups': 0, 'total_size': 0, 'total_files': 0, 'archives_on_disk': 0}

    def list_backups(self) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM backup_log ORDER BY timestamp DESC LIMIT 100")
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                path = BACKUP_DIR / r.get('archive_name', '')
                r['on_disk'] = path.exists()
            return rows
        except Exception as e:
            logger.error("list_backups failed: %s", e)
            return []

    def get_backup_log(self, limit: int = 20) -> List[dict]:
        return self.list_backups()[:limit]

    def get_backup_details(self, archive_name: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM backup_log WHERE archive_name=%s",
                    (archive_name,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                details = dict(row)
            path = BACKUP_DIR / archive_name
            details['on_disk'] = path.exists()
            if path.exists():
                try:
                    with zipfile.ZipFile(path, 'r') as zf:
                        details['contents'] = zf.namelist()
                except Exception:
                    details['contents'] = []
            return details
        except Exception as e:
            logger.error("get_backup_details failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_backup(self, label: str = None, triggered_by: str = 'system') -> dict:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        label_part = ('_' + label) if label else ''
        archive_name = f"backup_{ts}{label_part}.zip"
        archive_path = BACKUP_DIR / archive_name
        now = datetime.utcnow().isoformat()

        try:
            file_count = 0
            original_size = 0
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                if DATA_DIR.exists():
                    for fpath in DATA_DIR.rglob('*'):
                        if fpath.is_file() and fpath.suffix != '.zip':
                            rel = fpath.relative_to(DATA_DIR.parent)
                            zf.write(fpath, str(rel))
                            file_count += 1
                            original_size += fpath.stat().st_size

            compressed_size = archive_path.stat().st_size if archive_path.exists() else 0
            ratio = (
                round((1 - compressed_size / original_size) * 100, 1)
                if original_size > 0 else 0
            )

            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO backup_log
                       (id, archive_name, archive_path, file_count, original_size,
                        compressed_size, compression_ratio, timestamp, triggered_by, label)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (str(uuid.uuid4()), archive_name, str(archive_path),
                     file_count, original_size, compressed_size, ratio,
                     now, triggered_by, label or '')
                )
            return {
                'success': True,
                'archive_name': archive_name,
                'file_count': file_count,
                'original_size': original_size,
                'compressed_size': compressed_size,
                'compression_ratio': ratio,
            }
        except Exception as e:
            logger.error("create_backup failed: %s", e)
            if archive_path.exists():
                archive_path.unlink()
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------
    def restore_backup(self, archive_name: str, confirm: bool = False) -> dict:
        if not confirm:
            return {'success': False, 'error': 'Restore requires confirm=True'}
        path = BACKUP_DIR / archive_name
        if not path.exists():
            return {'success': False, 'error': f'{archive_name} not found on disk'}
        safety = self.create_backup(label='pre_restore', triggered_by='system:restore')
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                zf.extractall(DATA_DIR.parent)
                restored = len(zf.namelist())
            return {
                'success': True,
                'restored_files': restored,
                'safety_backup': safety.get('archive_name', 'none'),
            }
        except Exception as e:
            logger.error("restore_backup failed: %s", e)
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Delete & Purge
    # ------------------------------------------------------------------
    def delete_backup(self, archive_name: str) -> bool:
        try:
            path = BACKUP_DIR / archive_name
            if path.exists():
                path.unlink()
            with get_cursor() as cur:
                cur.execute("DELETE FROM backup_log WHERE archive_name=%s", (archive_name,))
            return True
        except Exception as e:
            logger.error("delete_backup failed: %s", e)
            return False

    def purge_old_backups(self, keep_days: int = 30) -> int:
        try:
            cutoff = (datetime.utcnow() - timedelta(days=keep_days)).isoformat()
            with get_cursor() as cur:
                cur.execute(
                    "SELECT archive_name FROM backup_log WHERE timestamp < %s",
                    (cutoff,)
                )
                old = cur.fetchall()
            count = 0
            for row in old:
                if self.delete_backup(row['archive_name']):
                    count += 1
            return count
        except Exception as e:
            logger.error("purge_old_backups failed: %s", e)
            return 0


class BackupScheduler:
    """
    Daily backup scheduler.
    Uses APScheduler (BackgroundScheduler) when available;
    falls back to a daemon thread with a wait-loop if APScheduler is not installed.
    """

    def __init__(self, engine: BackupEngine, hour: int = 1):
        self._engine = engine
        self._hour = hour
        self._scheduler = None   # APScheduler instance (when available)
        self._stop_event = None  # threading.Event (thread fallback)
        self.is_running = False
        self.next_run: Optional[str] = None

    def start(self):
        if self.is_running:
            return
        if _APS_AVAILABLE:
            self._start_aps()
        else:
            self._start_thread()

    def _start_aps(self):
        self._scheduler = _APSScheduler(daemon=True)
        self._scheduler.add_job(
            self._run_backup,
            trigger=_CronTrigger(hour=self._hour, minute=0),
            id='daily_backup',
            replace_existing=True,
            misfire_grace_time=600,   # allow 10-min late start
        )
        self._scheduler.start()
        self.is_running = True
        self._refresh_next_run()
        logger.info("Backup scheduler started (APScheduler, daily at %02d:00)", self._hour)

    def _start_thread(self):
        """Thread-based fallback when APScheduler is not installed."""
        import threading
        self._stop_event = threading.Event()
        t = threading.Thread(
            target=self._run_loop, daemon=True, name='backup-scheduler'
        )
        t.start()
        self.is_running = True
        self._refresh_next_run()
        logger.info("Backup scheduler started (thread fallback, daily at %02d:00)", self._hour)

    def stop(self):
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
        if self._stop_event is not None:
            self._stop_event.set()
        self.is_running = False
        self.next_run = None
        logger.info("Backup scheduler stopped")

    def _refresh_next_run(self):
        if self._scheduler is not None:
            job = self._scheduler.get_job('daily_backup')
            if job and job.next_run_time:
                self.next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')
                return
        # Fallback: calculate manually
        now = datetime.now()
        nxt = now.replace(hour=self._hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        self.next_run = nxt.strftime('%Y-%m-%d %H:%M:%S')

    def _run_backup(self):
        """Called by APScheduler or the thread loop."""
        try:
            result = self._engine.create_backup(triggered_by='scheduler')
            logger.info("Scheduled backup completed: %s", result.get('archive_name', 'unknown'))
            self._refresh_next_run()
        except Exception as e:
            logger.error("Scheduled backup failed: %s", e, exc_info=True)

    def _run_loop(self):
        """Thread fallback: sleep until daily trigger then call _run_backup."""
        while not self._stop_event.is_set():
            now = datetime.now()
            target = now.replace(hour=self._hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            self.next_run = target.strftime('%Y-%m-%d %H:%M:%S')
            wait_secs = (target - now).total_seconds()
            self._stop_event.wait(timeout=wait_secs)
            if not self._stop_event.is_set():
                self._run_backup()


# Module-level singletons used by backup_routes.py and app.py
backup_engine = BackupEngine()
backup_scheduler = BackupScheduler(backup_engine, hour=1)
