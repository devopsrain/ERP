"""
SIEM Data Store - PostgreSQL backend
Security event logging for all data uploads.
"""

import uuid
import logging
from collections import deque
from datetime import datetime, timedelta

from db import get_cursor

logger = logging.getLogger(__name__)

RAPID_UPLOAD_THRESHOLD = 10
RAPID_UPLOAD_WINDOW_MINUTES = 5
LARGE_FILE_SIZE_BYTES = 10 * 1024 * 1024


class SIEMDataStore:
    """Centralized security event logging backed by PostgreSQL."""

    def __init__(self):
        self._recent_events: deque = deque(maxlen=500)
        self._ensure_tables_exist()

    def _ensure_tables_exist(self):
        """Ensure SIEM tables exist in the database."""
        try:
            with get_cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS siem_events (
                        event_id         TEXT PRIMARY KEY,
                        timestamp        TEXT NOT NULL DEFAULT '',
                        ip_address       TEXT NOT NULL DEFAULT '',
                        username         TEXT NOT NULL DEFAULT '',
                        module           TEXT NOT NULL DEFAULT '',
                        endpoint         TEXT NOT NULL DEFAULT '',
                        http_method      TEXT NOT NULL DEFAULT '',
                        filename         TEXT NOT NULL DEFAULT '',
                        file_size_bytes  INTEGER NOT NULL DEFAULT 0,
                        records_imported INTEGER NOT NULL DEFAULT 0,
                        status           TEXT NOT NULL DEFAULT '',
                        details          TEXT NOT NULL DEFAULT '',
                        user_agent       TEXT NOT NULL DEFAULT '',
                        referer          TEXT NOT NULL DEFAULT '',
                        content_type     TEXT NOT NULL DEFAULT ''
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_siem_events_timestamp ON siem_events(timestamp)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_siem_events_ip_address ON siem_events(ip_address)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_siem_events_module ON siem_events(module)")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS siem_alerts (
                        alert_id     TEXT PRIMARY KEY,
                        timestamp    TEXT NOT NULL DEFAULT '',
                        severity     TEXT NOT NULL DEFAULT 'medium',
                        rule         TEXT NOT NULL DEFAULT '',
                        message      TEXT NOT NULL DEFAULT '',
                        event_id     TEXT NOT NULL DEFAULT '',
                        ip_address   TEXT NOT NULL DEFAULT '',
                        acknowledged BOOLEAN NOT NULL DEFAULT FALSE
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_siem_alerts_acknowledged ON siem_alerts(acknowledged)")
                logger.info("SIEM tables initialized successfully")
        except Exception as e:
            logger.warning("Could not initialize SIEM tables: %s", e)

    def log_upload_event(self, request_obj, module: str, endpoint: str,
                         filename: str = None, file_size: int = 0,
                         records_imported: int = 0, status: str = 'success',
                         details: str = '', user: str = None):
        if user is None:
            # Try FastAPI/Starlette session first (available when request_obj is a FastAPI Request)
            try:
                sess = getattr(request_obj, 'session', None)
                if sess is not None:
                    user = sess.get('username') or None
            except Exception:
                pass
            # Fall back to any session dict (already attempted above)
            if not user:
                user = 'anonymous'

        forwarded = request_obj.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        real_ip = request_obj.headers.get('X-Real-IP', '')
        # Support both FastAPI (request.client.host) and Flask (request.remote_addr)
        fallback_ip = (
            getattr(getattr(request_obj, 'client', None), 'host', None)
            or getattr(request_obj, 'remote_addr', None)
            or 'unknown'
        )
        ip_address = forwarded or real_ip or fallback_ip
        user_agent = request_obj.headers.get('User-Agent', 'unknown')
        referer = request_obj.headers.get('Referer', '')

        event = {
            'event_id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'ip_address': ip_address,
            'username': user,
            'module': module,
            'endpoint': endpoint,
            'http_method': request_obj.method,
            'filename': filename or '',
            'file_size_bytes': file_size,
            'records_imported': records_imported,
            'status': status,
            'details': details,
            'user_agent': user_agent,
            'referer': referer,
            'content_type': request_obj.headers.get('content-type', '') or getattr(request_obj, 'content_type', '') or '',
        }

        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO siem_events
                       (event_id,timestamp,ip_address,username,module,endpoint,
                        http_method,filename,file_size_bytes,records_imported,
                        status,details,user_agent,referer,content_type)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (event['event_id'], event['timestamp'], ip_address, user,
                     module, endpoint, event['http_method'], event['filename'],
                     file_size, records_imported, status, details,
                     user_agent, referer, event['content_type'])
                )
        except Exception as e:
            logger.warning("SIEM DB write failed: %s", e)

        self._recent_events.append({'ip_address': ip_address, 'timestamp': datetime.now()})
        self._evaluate_alerts(event)
        return event

    def get_all_events(self, limit: int = 500):
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM siem_events ORDER BY timestamp DESC LIMIT %s", (limit,)
                )
                rows = cur.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    d['user'] = d.pop('username', '')
                    result.append(d)
                return result
        except Exception as e:
            logger.error("get_all_events failed: %s", e)
            return []

    def get_events_by_ip(self, ip_address: str):
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM siem_events WHERE ip_address=%s ORDER BY timestamp DESC",
                    (ip_address,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_events_by_ip failed: %s", e)
            return []

    def get_events_by_module(self, module: str):
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM siem_events WHERE module=%s ORDER BY timestamp DESC",
                    (module,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_events_by_module failed: %s", e)
            return []

    def get_events_by_date_range(self, start_date: str, end_date: str):
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM siem_events WHERE timestamp >= %s AND timestamp <= %s "
                    "ORDER BY timestamp DESC",
                    (start_date or '', (end_date + 'T23:59:59') if end_date else '9999')
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_events_by_date_range failed: %s", e)
            return []

    def get_events_by_status(self, status: str):
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM siem_events WHERE status=%s ORDER BY timestamp DESC",
                    (status,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_events_by_status failed: %s", e)
            return []

    def get_event_by_id(self, event_id: str):
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM siem_events WHERE event_id=%s", (event_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_event_by_id failed: %s", e)
            return None

    def get_dashboard_stats(self):
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM siem_events")
                total = cur.fetchone()['total']
                cur.execute("SELECT COUNT(DISTINCT ip_address) AS cnt FROM siem_events")
                unique_ips = cur.fetchone()['cnt']
                cur.execute("SELECT COUNT(*) AS cnt FROM siem_events WHERE status='success'")
                successful = cur.fetchone()['cnt']
                cur.execute("SELECT COUNT(*) AS cnt FROM siem_events WHERE status='failed'")
                failed = cur.fetchone()['cnt']
                cur.execute("SELECT COALESCE(SUM(records_imported),0) AS s FROM siem_events")
                total_records = cur.fetchone()['s']
                cur.execute("SELECT COALESCE(SUM(file_size_bytes),0) AS s FROM siem_events")
                total_bytes = cur.fetchone()['s']
                cur.execute("SELECT COUNT(DISTINCT module) AS cnt FROM siem_events")
                modules_active = cur.fetchone()['cnt']
                today = datetime.now().strftime('%Y-%m-%d')
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM siem_events WHERE timestamp >= %s",
                    (today,)
                )
                events_today = cur.fetchone()['cnt']
                week_ago = (datetime.now() - timedelta(days=7)).isoformat()
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM siem_events WHERE timestamp >= %s",
                    (week_ago,)
                )
                events_week = cur.fetchone()['cnt']
                cur.execute(
                    "SELECT ip_address, COUNT(*) AS cnt FROM siem_events "
                    "GROUP BY ip_address ORDER BY cnt DESC LIMIT 5"
                )
                top_ips = [dict(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT module, COUNT(*) AS cnt FROM siem_events "
                    "GROUP BY module ORDER BY cnt DESC LIMIT 5"
                )
                top_modules = [dict(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT * FROM siem_events WHERE status='failed' "
                    "ORDER BY timestamp DESC LIMIT 10"
                )
                recent_failures = [dict(r) for r in cur.fetchall()]

            return {
                'total_events': total,
                'unique_ips': unique_ips,
                'successful_uploads': successful,
                'failed_uploads': failed,
                'total_records_imported': total_records,
                'total_data_bytes': total_bytes,
                'modules_active': modules_active,
                'events_today': events_today,
                'events_this_week': events_week,
                'top_ips': top_ips,
                'top_modules': top_modules,
                'hourly_activity': [],
                'recent_failures': recent_failures,
            }
        except Exception as e:
            logger.error("get_dashboard_stats failed: %s", e)
            return {
                'total_events': 0, 'unique_ips': 0, 'successful_uploads': 0,
                'failed_uploads': 0, 'total_records_imported': 0,
                'total_data_bytes': 0, 'modules_active': 0,
                'events_today': 0, 'events_this_week': 0,
                'top_ips': [], 'top_modules': [], 'hourly_activity': [],
                'recent_failures': [],
            }

    def get_alerts(self, acknowledged: bool = None, limit: int = 100):
        try:
            with get_cursor() as cur:
                if acknowledged is None:
                    cur.execute(
                        "SELECT * FROM siem_alerts ORDER BY timestamp DESC LIMIT %s", (limit,)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM siem_alerts WHERE acknowledged=%s "
                        "ORDER BY timestamp DESC LIMIT %s",
                        (acknowledged, limit)
                    )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_alerts failed: %s", e)
            return []

    def acknowledge_alert(self, alert_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE siem_alerts SET acknowledged=TRUE WHERE alert_id=%s",
                    (alert_id,)
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("acknowledge_alert failed: %s", e)
            return False

    def get_alert_counts(self) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT severity, COUNT(*) AS cnt FROM siem_alerts "
                    "WHERE acknowledged=FALSE GROUP BY severity"
                )
                counts = {r['severity']: r['cnt'] for r in cur.fetchall()}
            return {
                'critical': counts.get('critical', 0),
                'high': counts.get('high', 0),
                'medium': counts.get('medium', 0),
                'low': counts.get('low', 0),
                'unacknowledged': sum(counts.values()),
                'total_unacknowledged': sum(counts.values()),
            }
        except Exception as e:
            logger.error("get_alert_counts failed: %s", e)
            return {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'unacknowledged': 0, 'total_unacknowledged': 0}

    def _evaluate_alerts(self, event: dict):
        alerts = []
        if event.get('file_size_bytes', 0) > LARGE_FILE_SIZE_BYTES:
            alerts.append({
                'severity': 'medium',
                'rule': 'large_file_upload',
                'message': f"Large file upload: {event['filename']} ({event['file_size_bytes']} bytes)",
            })
        # Rapid upload detection using in-memory ring buffer
        cutoff = datetime.now() - timedelta(minutes=RAPID_UPLOAD_WINDOW_MINUTES)
        recent_from_ip = [e for e in self._recent_events
                         if e['ip_address'] == event['ip_address']
                         and e['timestamp'] > cutoff]
        if len(recent_from_ip) >= RAPID_UPLOAD_THRESHOLD:
            alerts.append({
                'severity': 'high',
                'rule': 'rapid_uploads',
                'message': f"Rapid uploads from {event['ip_address']}: {len(recent_from_ip)} in {RAPID_UPLOAD_WINDOW_MINUTES}min",
            })

        if not alerts:
            return

        try:
            with get_cursor() as cur:
                for a in alerts:
                    cur.execute(
                        """INSERT INTO siem_alerts
                           (alert_id,timestamp,severity,rule,message,event_id,
                            ip_address,acknowledged)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), datetime.now().isoformat(),
                         a['severity'], a['rule'], a['message'],
                         event['event_id'], event['ip_address'], False)
                    )
        except Exception as e:
            logger.warning("_evaluate_alerts DB write failed: %s", e)


# Singleton instance
siem_store = SIEMDataStore()
