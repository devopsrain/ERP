"""
Outbound Webhooks + API Keys — PostgreSQL data store and delivery engine.

Tables: api_keys, webhook_endpoints, webhook_deliveries,
        webhook_inbound_log, webhook_inbound_secrets

Public surface used by other modules::

    from webhook_data_store import emit
    emit(company_id, "invoice.created", {"invoice_id": "...", ...})

``emit`` writes one delivery row per matching active endpoint and pushes the
first attempt onto a background thread. ``webhook_jobs`` retries anything
that is still pending/failed with exponential backoff (see BACKOFF).

Signing (outbound)
    X-EBMS-Signature: t=<unix>,v1=<hex hmac_sha256(secret, f"{t}.{body}")>
    X-EBMS-Event, X-EBMS-Delivery-Id

Delivery status lifecycle
    pending  → never attempted (or manually re-queued)
    failed   → last attempt failed, retry scheduled at next_attempt_at
    success  → 2xx received
    dead     → MAX_ATTEMPTS exhausted (retry button re-queues it)
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

from psycopg2.extras import Json

from db import get_conn

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
HTTP_TIMEOUT = 10
SIGNATURE_TOLERANCE_SECONDS = 300
RESPONSE_KEEP_CHARS = 2000
ATTEMPT_LOG_KEEP = 10
LOG_RETENTION_DAYS = 30

# Standard event catalogue (documented on /webhooks/docs). Other modules may
# emit any dotted name; these are the ones promised to integrators.
STANDARD_EVENTS = [
    ("invoice.created",                "An invoice was issued"),
    ("invoice.paid",                   "An invoice was fully settled"),
    ("payment.received",               "A payment (bank / mobile money / cash) was recorded"),
    ("payment.reconciled",             "A payment was matched to an invoice or ledger entry"),
    ("bid.created",                    "A tender / bid record was opened"),
    ("bid.won",                        "A bid was marked as won"),
    ("purchase_requisition.approved",  "A purchase requisition passed approval"),
    ("approval.decided",               "Any approval request was approved or rejected"),
    ("employee.created",               "A new employee record was created"),
    ("asset.disposed",                 "A fixed asset was disposed of"),
]
STANDARD_EVENT_NAMES = [e for e, _ in STANDARD_EVENTS]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL DEFAULT '',
    prefix        TEXT NOT NULL DEFAULT '',
    key_hash      TEXT NOT NULL,
    salt          TEXT NOT NULL,
    scopes        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMP,
    expires_at    TIMESTAMP,
    revoked_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_api_keys_company ON api_keys(company_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix  ON api_keys(prefix);

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    url           TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    secret        TEXT NOT NULL,
    events        JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_company ON webhook_endpoints(company_id);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                TEXT PRIMARY KEY,
    endpoint_id       TEXT NOT NULL,
    company_id        TEXT NOT NULL DEFAULT 'default',
    event             TEXT NOT NULL,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending|failed|success|dead
    attempts          INT  NOT NULL DEFAULT 0,
    next_attempt_at   TIMESTAMP,
    last_status_code  INT,
    last_error        TEXT,
    last_response     TEXT,
    attempt_log       JSONB NOT NULL DEFAULT '[]'::jsonb,
    dedupe_key        TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    delivered_at      TIMESTAMP
);
ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS last_response TEXT;
ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS attempt_log JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_company  ON webhook_deliveries(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_endpoint ON webhook_deliveries(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due      ON webhook_deliveries(status, next_attempt_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_deliveries_dedupe
    ON webhook_deliveries(endpoint_id, dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS webhook_inbound_log (
    id            TEXT PRIMARY KEY,
    company_id    TEXT,
    source        TEXT NOT NULL,
    headers       JSONB NOT NULL DEFAULT '{}'::jsonb,
    body          TEXT NOT NULL DEFAULT '',
    received_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    verified      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_webhook_inbound_source ON webhook_inbound_log(source, received_at DESC);

CREATE TABLE IF NOT EXISTS webhook_inbound_secrets (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    source        TEXT NOT NULL,
    secret        TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, source)
);
"""


def _opt(value):
    """'' → None (Postgres rejects '' for TIMESTAMP/INT)."""
    return value if value not in ("", None) else None


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("webhooks schema ready")
    except Exception as e:
        logger.error("webhooks schema init failed: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
#  Pure helpers (unit-tested, no DB)
# ═════════════════════════════════════════════════════════════════════════════

def canonical_body(envelope: dict) -> str:
    """Deterministic JSON so signature and stored payload always agree."""
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True, default=str, ensure_ascii=False)


def compute_signature(secret: str, body: str, timestamp: Optional[int] = None) -> str:
    """Return the full ``X-EBMS-Signature`` header value."""
    t = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(secret.encode("utf-8"), f"{t}.{body}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def parse_signature_header(header: str) -> Tuple[Optional[int], List[str]]:
    """``t=..,v1=..[,v1=..]`` → (t, [v1,...]); (None, []) when malformed."""
    t = None
    sigs: List[str] = []
    for part in (header or "").split(","):
        k, _, v = part.strip().partition("=")
        if k == "t":
            try:
                t = int(v)
            except ValueError:
                return None, []
        elif k == "v1" and v:
            sigs.append(v.strip())
    return t, sigs


def verify_signature(secret: str, body: str, header: str,
                     tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
                     now: Optional[int] = None) -> bool:
    """Verify an ``X-EBMS-Signature`` header (constant-time, replay-window checked)."""
    if not secret or header is None:
        return False
    t, sigs = parse_signature_header(header)
    if t is None or not sigs:
        return False
    current = int(now if now is not None else time.time())
    if tolerance is not None and abs(current - t) > tolerance:
        return False
    expected = compute_signature(secret, body, t).split("v1=", 1)[1]
    return any(hmac.compare_digest(expected, s) for s in sigs)


def verify_inbound_signature(secret: str, body: str, header: str) -> bool:
    """
    Inbound providers vary: accept our own ``t=..,v1=..`` form, a bare
    hex HMAC-SHA256 of the body, or ``sha256=<hex>`` (GitHub style).
    """
    if not secret or not header:
        return False
    header = header.strip()
    if "v1=" in header:
        return verify_signature(secret, body, header)
    candidate = header.split("=", 1)[1] if header.lower().startswith("sha256=") else header
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), candidate.strip().lower())


def event_matches(pattern: str, event: str) -> bool:
    """
    ``*`` matches everything, ``invoice.*`` matches ``invoice.created`` (and
    deeper), otherwise exact match. Any other glob falls back to fnmatch.
    """
    pattern = (pattern or "").strip().lower()
    event = (event or "").strip().lower()
    if not pattern or not event:
        return False
    if pattern == "*" or pattern == event:
        return True
    if pattern.endswith(".*"):
        return event.startswith(pattern[:-1])
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatchcase(event, pattern)
    return False


def endpoint_subscribed(events, event: str) -> bool:
    """``events`` is the endpoint's JSON list of patterns (may be str/None)."""
    if isinstance(events, str):
        try:
            events = json.loads(events)
        except ValueError:
            events = [e for e in events.replace(",", " ").split()]
    return any(event_matches(p, event) for p in (events or []))


def backoff_seconds(attempt: int) -> int:
    """
    Delay before the NEXT attempt after ``attempt`` failures:
      1→60s, 2→4m, 3→16m, 4→~1h, 5→~4.3h  (base 60s × 4^(n-1), cap 24h).
    """
    attempt = max(1, int(attempt))
    return int(min(60 * (4 ** (attempt - 1)), 24 * 3600))


def next_state_after_failure(attempts_done: int) -> Tuple[str, Optional[int]]:
    """(status, delay_seconds) after ``attempts_done`` failed attempts."""
    if attempts_done >= MAX_ATTEMPTS:
        return "dead", None
    return "failed", backoff_seconds(attempts_done)


def _private_allowed() -> bool:
    return os.environ.get("WEBHOOKS_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes")


def _default_resolver(host: str) -> List[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})


def is_url_allowed(url: str, resolver: Callable[[str], List[str]] = None,
                   allow_private: Optional[bool] = None) -> Tuple[bool, str]:
    """
    SSRF guard. Refuses non-http(s) schemes and hosts that resolve to
    loopback / private / link-local / reserved ranges unless
    ``WEBHOOKS_ALLOW_PRIVATE=1`` (or ``allow_private=True``).
    """
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False, "Malformed URL"
    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://"
    host = parsed.hostname
    if not host:
        return False, "URL has no host"
    if allow_private is None:
        allow_private = _private_allowed()
    if allow_private:
        return True, ""
    if host.lower() in ("localhost",) or host.lower().endswith(".localhost"):
        return False, "Loopback hosts are not allowed"
    try:
        addresses = (resolver or _default_resolver)(host)
    except Exception:
        return False, f"Could not resolve host {host}"
    if not addresses:
        return False, f"Could not resolve host {host}"
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False, f"Unrecognised address {addr}"
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return False, f"{host} resolves to a private/internal address ({addr})"
        if ip.version == 6 and getattr(ip, "ipv4_mapped", None) is not None:
            mapped = ip.ipv4_mapped
            if mapped.is_private or mapped.is_loopback or mapped.is_link_local:
                return False, f"{host} resolves to a private/internal address ({addr})"
    return True, ""


def generate_endpoint_secret() -> str:
    return "whsec_" + secrets.token_hex(24)


def build_envelope(delivery_id: str, company_id: str, event: str, payload: dict, created_at=None) -> dict:
    return {
        "id": delivery_id,
        "event": event,
        "company_id": company_id,
        "created_at": (created_at.isoformat() if hasattr(created_at, "isoformat") else created_at)
                      or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "data": payload or {},
    }


def _dedupe_key(event: str, payload: dict) -> str:
    """
    Guards against the same bus event being dispatched twice (the Redis
    listener re-delivers locally emitted events). Explicit ``event_id`` wins;
    otherwise identical payloads within a 5-minute bucket collapse.
    """
    eid = (payload or {}).get("event_id") or (payload or {}).get("id")
    if eid:
        return f"{event}:{eid}"
    raw = canonical_body(payload or {})
    bucket = int(time.time() // 300)
    return hashlib.sha256(f"{event}|{raw}|{bucket}".encode("utf-8")).hexdigest()


# ── HTTP transport ───────────────────────────────────────────────────────────

def _http_post(url: str, body: bytes, headers: dict, timeout: int = HTTP_TIMEOUT) -> Tuple[int, str]:
    """POST and return (status_code, response_text). Raises on transport errors."""
    try:
        import requests  # in production requirements (requirements-aws.txt)
        resp = requests.post(url, data=body, headers=headers, timeout=timeout, allow_redirects=False)
        return resp.status_code, (resp.text or "")[:RESPONSE_KEEP_CHARS]
    except ImportError:
        from urllib import request as _ur, error as _ue
        req = _ur.Request(url, data=body, headers=headers, method="POST")
        try:
            with _ur.urlopen(req, timeout=timeout) as r:  # noqa: S310 — URL SSRF-checked
                return r.status, r.read(RESPONSE_KEEP_CHARS).decode("utf-8", "replace")
        except _ue.HTTPError as he:
            return he.code, (he.read(RESPONSE_KEEP_CHARS) or b"").decode("utf-8", "replace")


# ═════════════════════════════════════════════════════════════════════════════
#  Store
# ═════════════════════════════════════════════════════════════════════════════

class WebhookDataStore:

    def ensure_schema(self):
        ensure_schema()

    # ── API keys ─────────────────────────────────────────────────────────────

    def create_api_key(self, company_id: str, name: str, prefix: str, key_hash: str, salt: str,
                       scopes: list, created_by: str = "", expires_at=None) -> Optional[dict]:
        try:
            kid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO api_keys(id,company_id,name,prefix,key_hash,salt,scopes,created_by,expires_at)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (kid, company_id, name or "", prefix, key_hash, salt, Json(list(scopes or [])),
                         created_by or "", _opt(expires_at)))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_api_key: %s", e); return None

    def list_api_keys(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM api_keys WHERE company_id=%s ORDER BY created_at DESC", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_api_keys: %s", e); return []

    def get_api_key(self, key_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM api_keys WHERE id=%s AND company_id=%s", (key_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_api_key: %s", e); return None

    def find_api_keys_by_prefix(self, prefix: str) -> List[dict]:
        """Candidate rows for verification (all companies — the key identifies the tenant)."""
        if not prefix:
            return []
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM api_keys WHERE prefix=%s AND revoked_at IS NULL", (prefix,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("find_api_keys_by_prefix: %s", e); return []

    def touch_api_key(self, key_id: str) -> None:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE api_keys SET last_used_at=NOW() WHERE id=%s", (key_id,))
        except Exception as e:
            logger.debug("touch_api_key: %s", e)

    def revoke_api_key(self, key_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE api_keys SET revoked_at=NOW() WHERE id=%s AND company_id=%s AND revoked_at IS NULL",
                                (key_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("revoke_api_key: %s", e); return False

    # ── Endpoints ────────────────────────────────────────────────────────────

    def list_endpoints(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT e.*,
                                  (SELECT COUNT(*) FROM webhook_deliveries d WHERE d.endpoint_id=e.id) AS delivery_count,
                                  (SELECT COUNT(*) FROM webhook_deliveries d WHERE d.endpoint_id=e.id AND d.status IN ('failed','dead')) AS failure_count,
                                  (SELECT MAX(delivered_at) FROM webhook_deliveries d WHERE d.endpoint_id=e.id AND d.status='success') AS last_success_at
                           FROM webhook_endpoints e WHERE e.company_id=%s ORDER BY e.created_at DESC""",
                        (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_endpoints: %s", e); return []

    def get_endpoint(self, endpoint_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM webhook_endpoints WHERE id=%s AND company_id=%s", (endpoint_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_endpoint: %s", e); return None

    def create_endpoint(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            eid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO webhook_endpoints(id,company_id,url,description,secret,events,is_active,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (eid, company_id, data["url"].strip(), data.get("description") or "",
                         data.get("secret") or generate_endpoint_secret(), Json(list(data.get("events") or [])),
                         bool(data.get("is_active", True)), data.get("created_by") or ""))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_endpoint: %s", e); return None

    def update_endpoint(self, endpoint_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE webhook_endpoints SET url=%s, description=%s, events=%s, is_active=%s, updated_at=NOW()
                           WHERE id=%s AND company_id=%s""",
                        (data["url"].strip(), data.get("description") or "", Json(list(data.get("events") or [])),
                         bool(data.get("is_active", True)), endpoint_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_endpoint: %s", e); return False

    def set_endpoint_active(self, endpoint_id: str, company_id: str, active: bool) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE webhook_endpoints SET is_active=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                                (bool(active), endpoint_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("set_endpoint_active: %s", e); return False

    def rotate_endpoint_secret(self, endpoint_id: str, company_id: str) -> Optional[str]:
        try:
            new_secret = generate_endpoint_secret()
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE webhook_endpoints SET secret=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                                (new_secret, endpoint_id, company_id))
                    return new_secret if cur.rowcount else None
        except Exception as e:
            logger.error("rotate_endpoint_secret: %s", e); return None

    def delete_endpoint(self, endpoint_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM webhook_deliveries WHERE endpoint_id=%s AND company_id=%s", (endpoint_id, company_id))
                    cur.execute("DELETE FROM webhook_endpoints WHERE id=%s AND company_id=%s", (endpoint_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_endpoint: %s", e); return False

    def active_endpoints_for_event(self, company_id: str, event: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM webhook_endpoints WHERE company_id=%s AND is_active=TRUE", (company_id,))
                    rows = [dict(r) for r in cur.fetchall()]
            return [r for r in rows if endpoint_subscribed(r.get("events"), event)]
        except Exception as e:
            logger.error("active_endpoints_for_event: %s", e); return []

    # ── Deliveries ───────────────────────────────────────────────────────────

    def create_delivery(self, endpoint_id: str, company_id: str, event: str, payload: dict,
                        dedupe_key: Optional[str] = None, delay_seconds: Optional[int] = None) -> Optional[dict]:
        """Insert a pending delivery; returns None when deduplicated or on error."""
        try:
            did = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO webhook_deliveries(id,endpoint_id,company_id,event,payload,status,dedupe_key,next_attempt_at)
                           VALUES(%s,%s,%s,%s,%s,'pending',%s,
                                  CASE WHEN %s IS NULL THEN NULL ELSE NOW() + %s * INTERVAL '1 second' END)
                           ON CONFLICT DO NOTHING RETURNING *""",
                        (did, endpoint_id, company_id, event, Json(payload or {}), dedupe_key,
                         delay_seconds, delay_seconds))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("create_delivery: %s", e); return None

    def get_delivery(self, delivery_id: str, company_id: Optional[str] = None) -> Optional[dict]:
        try:
            sql = """SELECT d.*, e.url AS endpoint_url, e.secret AS endpoint_secret, e.description AS endpoint_description
                     FROM webhook_deliveries d JOIN webhook_endpoints e ON e.id=d.endpoint_id WHERE d.id=%s"""
            params = [delivery_id]
            if company_id:
                sql += " AND d.company_id=%s"; params.append(company_id)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_delivery: %s", e); return None

    def list_deliveries(self, company_id: str, status: str = None, endpoint_id: str = None,
                        event: str = None, limit: int = 100) -> List[dict]:
        try:
            sql = """SELECT d.*, e.url AS endpoint_url FROM webhook_deliveries d
                     LEFT JOIN webhook_endpoints e ON e.id=d.endpoint_id WHERE d.company_id=%s"""
            params: list = [company_id]
            if status:
                sql += " AND d.status=%s"; params.append(status)
            if endpoint_id:
                sql += " AND d.endpoint_id=%s"; params.append(endpoint_id)
            if event:
                sql += " AND d.event=%s"; params.append(event)
            sql += " ORDER BY d.created_at DESC LIMIT %s"; params.append(int(limit))
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_deliveries: %s", e); return []

    def list_events_seen(self, company_id: str) -> List[str]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT event FROM webhook_deliveries WHERE company_id=%s ORDER BY event", (company_id,))
                    return [r["event"] for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_events_seen: %s", e); return []

    def claim_due_deliveries(self, limit: int = 100) -> List[dict]:
        """
        Atomically pick due pending/failed rows and push their next_attempt_at
        5 minutes out, so concurrent workers never send the same delivery twice
        and a crashed worker's rows come back automatically.
        """
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE webhook_deliveries d SET next_attempt_at = NOW() + INTERVAL '5 minutes'
                           WHERE d.id IN (
                               SELECT id FROM webhook_deliveries
                               WHERE status IN ('pending','failed')
                                 AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                               ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED)
                           RETURNING d.*""", (int(limit),))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("claim_due_deliveries: %s", e); return []

    def record_attempt(self, delivery_id: str, ok: bool, status_code: Optional[int], error: Optional[str],
                       response_text: Optional[str], entry: dict) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT attempts, attempt_log FROM webhook_deliveries WHERE id=%s FOR UPDATE", (delivery_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    attempts = int(row["attempts"] or 0) + 1
                    log = list(row["attempt_log"] or [])
                    log.append(entry)
                    log = log[-ATTEMPT_LOG_KEEP:]
                    if ok:
                        cur.execute(
                            """UPDATE webhook_deliveries SET status='success', attempts=%s, next_attempt_at=NULL,
                               last_status_code=%s, last_error=NULL, last_response=%s, attempt_log=%s, delivered_at=NOW()
                               WHERE id=%s RETURNING *""",
                            (attempts, status_code, response_text, Json(log), delivery_id))
                    else:
                        status, delay = next_state_after_failure(attempts)
                        cur.execute(
                            """UPDATE webhook_deliveries SET status=%s, attempts=%s,
                               next_attempt_at = CASE WHEN %s IS NULL THEN NULL ELSE NOW() + %s * INTERVAL '1 second' END,
                               last_status_code=%s, last_error=%s, last_response=%s, attempt_log=%s
                               WHERE id=%s RETURNING *""",
                            (status, attempts, delay, delay, status_code, (error or "")[:1000], response_text,
                             Json(log), delivery_id))
                    r = cur.fetchone()
                    return dict(r) if r else None
        except Exception as e:
            logger.error("record_attempt: %s", e); return None

    def requeue_delivery(self, delivery_id: str, company_id: str) -> bool:
        """Manual retry: back to pending, due now (attempt counter kept for the audit trail)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE webhook_deliveries SET status='pending', next_attempt_at=NULL
                           WHERE id=%s AND company_id=%s AND status<>'success'""", (delivery_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("requeue_delivery: %s", e); return False

    def prune_old(self, days: int = LOG_RETENTION_DAYS) -> dict:
        out = {"deliveries": 0, "inbound": 0}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM webhook_deliveries WHERE created_at < NOW() - %s * INTERVAL '1 day' AND status IN ('success','dead')",
                                (int(days),))
                    out["deliveries"] = cur.rowcount
                    cur.execute("DELETE FROM webhook_inbound_log WHERE received_at < NOW() - %s * INTERVAL '1 day'", (int(days),))
                    out["inbound"] = cur.rowcount
        except Exception as e:
            logger.error("prune_old: %s", e)
        return out

    def get_stats(self, company_id: str) -> dict:
        base = {"endpoints": 0, "endpoints_active": 0, "api_keys_active": 0,
                "pending": 0, "failed": 0, "success": 0, "dead": 0, "total": 0,
                "last24h_success": 0, "last24h_failed": 0, "inbound_24h": 0}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c, COUNT(*) FILTER (WHERE is_active) AS a FROM webhook_endpoints WHERE company_id=%s", (company_id,))
                    r = cur.fetchone(); base["endpoints"] = r["c"]; base["endpoints_active"] = r["a"]
                    cur.execute("SELECT COUNT(*) AS c FROM api_keys WHERE company_id=%s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())", (company_id,))
                    base["api_keys_active"] = cur.fetchone()["c"]
                    cur.execute("SELECT status, COUNT(*) AS c FROM webhook_deliveries WHERE company_id=%s GROUP BY status", (company_id,))
                    for row in cur.fetchall():
                        base[row["status"]] = row["c"]
                    base["total"] = base["pending"] + base["failed"] + base["success"] + base["dead"]
                    cur.execute("""SELECT COUNT(*) FILTER (WHERE status='success') AS s,
                                          COUNT(*) FILTER (WHERE status IN ('failed','dead')) AS f
                                   FROM webhook_deliveries WHERE company_id=%s AND created_at > NOW() - INTERVAL '24 hours'""", (company_id,))
                    r = cur.fetchone(); base["last24h_success"] = r["s"]; base["last24h_failed"] = r["f"]
                    cur.execute("SELECT COUNT(*) AS c FROM webhook_inbound_log WHERE (company_id=%s OR company_id IS NULL) AND received_at > NOW() - INTERVAL '24 hours'", (company_id,))
                    base["inbound_24h"] = cur.fetchone()["c"]
        except Exception as e:
            logger.error("get_stats: %s", e)
        return base

    def recent_failures(self, company_id: str, limit: int = 10) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT d.*, e.url AS endpoint_url FROM webhook_deliveries d
                           LEFT JOIN webhook_endpoints e ON e.id=d.endpoint_id
                           WHERE d.company_id=%s AND d.status IN ('failed','dead')
                           ORDER BY d.created_at DESC LIMIT %s""", (company_id, int(limit)))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("recent_failures: %s", e); return []

    # ── Inbound ──────────────────────────────────────────────────────────────

    def log_inbound(self, company_id: Optional[str], source: str, headers: dict, body: str, verified: bool) -> Optional[str]:
        try:
            iid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO webhook_inbound_log(id,company_id,source,headers,body,verified)
                           VALUES(%s,%s,%s,%s,%s,%s)""",
                        (iid, _opt(company_id), source[:100], Json(headers or {}), body or "", bool(verified)))
            return iid
        except Exception as e:
            logger.error("log_inbound: %s", e); return None

    def list_inbound(self, company_id: str, limit: int = 20) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, company_id, source, verified, received_at, LEFT(body, 200) AS body_preview
                           FROM webhook_inbound_log WHERE company_id=%s OR company_id IS NULL
                           ORDER BY received_at DESC LIMIT %s""", (company_id, int(limit)))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_inbound: %s", e); return []

    def inbound_secrets_for_source(self, source: str, company_id: Optional[str] = None) -> List[dict]:
        try:
            sql = "SELECT * FROM webhook_inbound_secrets WHERE source=%s"
            params = [source]
            if company_id:
                sql += " AND company_id=%s"; params.append(company_id)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("inbound_secrets_for_source: %s", e); return []

    def list_inbound_secrets(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM webhook_inbound_secrets WHERE company_id=%s ORDER BY source", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_inbound_secrets: %s", e); return []

    def set_inbound_secret(self, company_id: str, source: str, secret: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO webhook_inbound_secrets(id,company_id,source,secret) VALUES(%s,%s,%s,%s)
                           ON CONFLICT (company_id, source) DO UPDATE SET secret=EXCLUDED.secret""",
                        (str(uuid.uuid4()), company_id, source.strip().lower(), secret))
            return True
        except Exception as e:
            logger.error("set_inbound_secret: %s", e); return False

    def delete_inbound_secret(self, secret_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM webhook_inbound_secrets WHERE id=%s AND company_id=%s", (secret_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_inbound_secret: %s", e); return False


webhook_store = WebhookDataStore()


# ═════════════════════════════════════════════════════════════════════════════
#  Delivery engine
# ═════════════════════════════════════════════════════════════════════════════

def send_delivery(delivery: dict, endpoint: dict) -> dict:
    """
    Perform ONE HTTP attempt for ``delivery`` against ``endpoint`` and persist
    the outcome. Returns the updated delivery row (or the original on DB error).
    """
    envelope = build_envelope(delivery["id"], delivery.get("company_id", "default"), delivery["event"],
                              delivery.get("payload") or {}, delivery.get("created_at"))
    body = canonical_body(envelope)
    started = time.time()
    signature = compute_signature(endpoint["secret"], body)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "EBMS-Webhooks/1.0",
        "X-EBMS-Event": delivery["event"],
        "X-EBMS-Delivery-Id": delivery["id"],
        "X-EBMS-Signature": signature,
    }
    ok, status_code, error, response_text = False, None, None, None

    allowed, reason = is_url_allowed(endpoint["url"])
    if not allowed:
        error = f"Blocked: {reason}"
    else:
        try:
            status_code, response_text = _http_post(endpoint["url"], body.encode("utf-8"), headers)
            ok = 200 <= int(status_code) < 300
            if not ok:
                error = f"HTTP {status_code}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]

    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok,
        "status_code": status_code,
        "error": error,
        "duration_ms": int((time.time() - started) * 1000),
        "request_headers": {k: v for k, v in headers.items() if k != "Content-Type"},
        "response": (response_text or "")[:RESPONSE_KEEP_CHARS],
    }
    updated = webhook_store.record_attempt(delivery["id"], ok, status_code, error, response_text, entry)
    if ok:
        logger.info("webhook_delivered id=%s event=%s url=%s status=%s", delivery["id"], delivery["event"], endpoint["url"], status_code)
    else:
        logger.warning("webhook_failed id=%s event=%s url=%s err=%s", delivery["id"], delivery["event"], endpoint["url"], error)
    return updated or delivery


def attempt_delivery(delivery_id: str) -> Optional[dict]:
    """Load a delivery + its endpoint and try to send once."""
    d = webhook_store.get_delivery(delivery_id)
    if not d:
        return None
    endpoint = {"url": d["endpoint_url"], "secret": d["endpoint_secret"]}
    return send_delivery(d, endpoint)


def _deliver_in_background(delivery_ids: List[str]) -> None:
    def _run():
        for did in delivery_ids:
            try:
                attempt_delivery(did)
            except Exception as exc:  # never let a thread die noisily
                logger.error("background delivery %s crashed: %s", did, exc)
    t = threading.Thread(target=_run, name="webhook-deliver", daemon=True)
    t.start()


def emit(company_id: str, event: str, payload: Optional[dict] = None, *, deliver_now: bool = True,
         sync: bool = False) -> List[dict]:
    """
    Fan an event out to every active endpoint of ``company_id`` subscribed to
    ``event``. Returns the created delivery rows (empty when nobody listens).

    deliver_now=True  → first attempt starts immediately on a daemon thread
                        (or inline when sync=True, e.g. the "send test" button);
                        the retry job picks anything up that is still due.
    deliver_now=False → rows wait for the 2-minute retry job.
    """
    company_id = company_id or "default"
    event = (event or "").strip()
    if not event:
        return []
    payload = payload or {}
    created: List[dict] = []
    try:
        endpoints = webhook_store.active_endpoints_for_event(company_id, event)
        if not endpoints:
            return []
        dk = _dedupe_key(event, payload)
        for ep in endpoints:
            row = webhook_store.create_delivery(ep["id"], company_id, event, payload, dedupe_key=dk,
                                                delay_seconds=60 if deliver_now else None)
            if row:
                created.append(row)
        if deliver_now and created:
            if sync:
                created = [attempt_delivery(r["id"]) or r for r in created]
            else:
                _deliver_in_background([r["id"] for r in created])
    except Exception as exc:
        logger.error("webhook emit failed event=%s company=%s err=%s", event, company_id, exc)
    return created


def retry_due_deliveries(limit: int = 100) -> dict:
    """Called by webhook_jobs every 2 minutes. Returns counters."""
    counters = {"claimed": 0, "success": 0, "failed": 0}
    for d in webhook_store.claim_due_deliveries(limit):
        counters["claimed"] += 1
        try:
            result = attempt_delivery(d["id"])
            if result and result.get("status") == "success":
                counters["success"] += 1
            else:
                counters["failed"] += 1
        except Exception as exc:
            counters["failed"] += 1
            logger.error("retry delivery %s crashed: %s", d["id"], exc)
    return counters
