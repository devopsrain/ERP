"""
Customer & Supplier Portal — data store (PostgreSQL).

Own tables:  portal_users, portal_tickets, portal_ticket_messages,
             portal_documents, portal_rfqs, portal_rfq_invitations,
             portal_rfq_responses, portal_audit
Read-only views over existing modules, ALWAYS scoped by company_id AND the
portal user's party_key (case-insensitive name or TIN match):
  customers  -> vat_income, cpo_records, pm_projects (if a client column
                exists), ems_bookings, contracts (party_type=client)
  suppliers  -> proc_purchase_orders (+vendor, lines, GRN, invoices),
                proc_purchase_requisitions, vat_expenses, contracts

Columns of foreign tables are guarded through information_schema so a
schema that drifts (or a module that is not installed) degrades to an empty
list instead of a 500.
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import List, Optional

from db import get_conn, get_cursor

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_users (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    kind              TEXT NOT NULL DEFAULT 'customer',   -- customer|supplier
    email             TEXT NOT NULL,
    full_name         TEXT NOT NULL DEFAULT '',
    org_name          TEXT NOT NULL DEFAULT '',
    tin               TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    party_key         TEXT NOT NULL DEFAULT '',
    password_hash     TEXT,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    failed_attempts   INTEGER NOT NULL DEFAULT 0,
    locked_until      TIMESTAMP,
    invite_token      TEXT,
    invite_expires_at TIMESTAMP,
    reset_token       TEXT,
    reset_expires_at  TIMESTAMP,
    last_login_at     TIMESTAMP,
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_users_email
    ON portal_users(company_id, kind, LOWER(email));
CREATE INDEX IF NOT EXISTS idx_portal_users_company ON portal_users(company_id);
CREATE INDEX IF NOT EXISTS idx_portal_users_invite  ON portal_users(invite_token);
CREATE INDEX IF NOT EXISTS idx_portal_users_reset   ON portal_users(reset_token);

CREATE TABLE IF NOT EXISTS portal_tickets (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    portal_user_id  TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open',    -- open|in_progress|resolved|closed
    priority        TEXT NOT NULL DEFAULT 'normal',  -- low|normal|high
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portal_tickets_company ON portal_tickets(company_id, status);
CREATE INDEX IF NOT EXISTS idx_portal_tickets_user    ON portal_tickets(portal_user_id);

CREATE TABLE IF NOT EXISTS portal_ticket_messages (
    id           TEXT PRIMARY KEY,
    ticket_id    TEXT NOT NULL,
    author_kind  TEXT NOT NULL DEFAULT 'portal',  -- portal|staff
    author       TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portal_ticket_msgs ON portal_ticket_messages(ticket_id, created_at);

CREATE TABLE IF NOT EXISTS portal_documents (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    portal_user_id  TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'other',     -- quote|invoice|delivery_note|other
    filename        TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    size            BIGINT NOT NULL DEFAULT 0,
    related_ref     TEXT NOT NULL DEFAULT '',
    uploaded_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    status          TEXT NOT NULL DEFAULT 'received',  -- received|accepted|rejected
    staff_note      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_portal_documents_company ON portal_documents(company_id, status);
CREATE INDEX IF NOT EXISTS idx_portal_documents_user    ON portal_documents(portal_user_id);

CREATE TABLE IF NOT EXISTS portal_rfqs (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL DEFAULT 'default',
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    due_at       TIMESTAMP,
    status       TEXT NOT NULL DEFAULT 'open',   -- open|closed|awarded
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portal_rfqs_company ON portal_rfqs(company_id, status);

CREATE TABLE IF NOT EXISTS portal_rfq_invitations (
    rfq_id          TEXT NOT NULL,
    portal_user_id  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'invited',  -- invited|responded|declined
    invited_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rfq_id, portal_user_id)
);

CREATE TABLE IF NOT EXISTS portal_rfq_responses (
    id              TEXT PRIMARY KEY,
    rfq_id          TEXT NOT NULL,
    portal_user_id  TEXT NOT NULL,
    amount          NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'ETB',
    delivery_days   INTEGER,
    notes           TEXT NOT NULL DEFAULT '',
    attachment_path TEXT,
    submitted_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_rfq_response ON portal_rfq_responses(rfq_id, portal_user_id);

CREATE TABLE IF NOT EXISTS portal_audit (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    portal_user_id  TEXT,
    action          TEXT NOT NULL,
    ip              TEXT NOT NULL DEFAULT '',
    ua              TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_portal_audit_company ON portal_audit(company_id, created_at DESC);
"""

TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high")
DOCUMENT_KINDS = ("quote", "invoice", "delivery_note", "other")
DOCUMENT_STATUSES = ("received", "accepted", "rejected")
RFQ_STATUSES = ("open", "closed", "awarded")
USER_KINDS = ("customer", "supplier")

_USER_COLS = ("id, company_id, kind, email, full_name, org_name, tin, phone, party_key, "
              "is_active, failed_attempts, locked_until, invite_token IS NOT NULL AS has_invite, "
              "(locked_until IS NOT NULL AND locked_until > NOW()) AS is_locked, "
              "invite_expires_at, reset_expires_at, last_login_at, created_by, created_at, "
              "password_hash IS NOT NULL AS has_password")


def _opt(value):
    """'' -> None (Postgres rejects '' for DATE/NUMERIC/TIMESTAMP)."""
    return value if value not in ("", None) else None


def _num(value, default=0):
    if value in ("", None):
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _int(value):
    if value in ("", None):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _s(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value))


def upload_root() -> str:
    root = (os.environ.get("PORTAL_UPLOAD_DIR") or "").strip()
    if not root:
        root = os.path.join(tempfile.gettempdir(), "ebms_portal_uploads")
    return root


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("portal schema ready")
    except Exception as e:
        logger.error("portal schema init failed: %s", e)


def _party_clause(name_col: str, tin_col: Optional[str] = None) -> str:
    """SQL fragment matching a party by exact (case-insensitive, trimmed)
    name — or by TIN when the table has one. Consumes 2 params when a TIN
    column is given, otherwise 1. Never a LIKE: no partial matches leak."""
    clause = f"LOWER(TRIM({name_col})) = LOWER(TRIM(%s))"
    if tin_col:
        clause = f"({clause} OR (NULLIF(TRIM({tin_col}), '') IS NOT NULL AND LOWER(TRIM({tin_col})) = LOWER(TRIM(%s))))"
    return clause


class PortalDataStore:

    def __init__(self):
        self._columns_cache: dict = {}

    def ensure_schema(self):
        ensure_schema()

    # ── schema guards ────────────────────────────────────────────
    def _table_columns(self, table_name: str) -> set:
        cols = self._columns_cache.get(table_name)
        if cols:
            return cols
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                    (table_name,))
                cols = {r["column_name"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("_table_columns(%s) failed: %s", table_name, e)
            cols = set()
        if cols:
            self._columns_cache[table_name] = cols
        return cols

    def _has(self, table: str, *cols: str) -> bool:
        have = self._table_columns(table)
        return bool(have) and all(c in have for c in cols)

    def _rows(self, sql: str, params=()) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("portal query failed: %s | %s", e, sql[:160])
            return []

    def _row(self, sql: str, params=()) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    r = cur.fetchone()
                    return dict(r) if r else None
        except Exception as e:
            logger.error("portal query failed: %s | %s", e, sql[:160])
            return None

    def _exec(self, sql: str, params=()) -> int:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return cur.rowcount
        except Exception as e:
            logger.error("portal write failed: %s | %s", e, sql[:160])
            return -1

    # ── company branding ─────────────────────────────────────────
    def company_name(self, company_id: str) -> str:
        if self._has("tenants", "company_name"):
            row = self._row("SELECT company_name FROM tenants WHERE company_id=%s", (company_id,))
            if row and row.get("company_name"):
                return row["company_name"]
        return (os.environ.get("COMPANY_NAME") or "").strip()

    def companies(self) -> List[dict]:
        if self._has("tenants", "company_name"):
            return self._rows("SELECT company_id, company_name FROM tenants ORDER BY company_name")
        return []

    # ── audit ────────────────────────────────────────────────────
    def audit(self, company_id: str, portal_user_id: Optional[str], action: str,
              ip: str = "", ua: str = "") -> None:
        self._exec(
            "INSERT INTO portal_audit(id,company_id,portal_user_id,action,ip,ua) VALUES(%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), company_id or "default", portal_user_id, action[:200], (ip or "")[:64], (ua or "")[:300]))

    def recent_audit(self, company_id: str, limit: int = 50) -> List[dict]:
        return self._rows(
            """SELECT a.*, u.email, u.kind FROM portal_audit a
               LEFT JOIN portal_users u ON u.id=a.portal_user_id
               WHERE a.company_id=%s ORDER BY a.created_at DESC LIMIT %s""",
            (company_id, limit))

    # ── users ────────────────────────────────────────────────────
    def list_users(self, company_id: str, kind: Optional[str] = None) -> List[dict]:
        sql = f"SELECT {_USER_COLS} FROM portal_users WHERE company_id=%s"
        params = [company_id]
        if kind in USER_KINDS:
            sql += " AND kind=%s"; params.append(kind)
        sql += " ORDER BY kind, org_name, email"
        return self._rows(sql, params)

    def get_user(self, user_id: str, company_id: Optional[str] = None) -> Optional[dict]:
        sql = f"SELECT {_USER_COLS} FROM portal_users WHERE id=%s"
        params = [user_id]
        if company_id:
            sql += " AND company_id=%s"; params.append(company_id)
        return self._row(sql, params)

    def _user_with_hash(self, where: str, params) -> Optional[dict]:
        return self._row(f"SELECT * FROM portal_users WHERE {where}", params)

    def users_by_email(self, email: str) -> List[dict]:
        """All accounts sharing an e-mail (a person can be a customer of one
        company and a supplier of another). Includes password_hash: login only."""
        return self._rows(
            "SELECT * FROM portal_users WHERE LOWER(email)=LOWER(%s) ORDER BY last_login_at DESC NULLS LAST, created_at",
            (_s(email),))

    def email_taken(self, company_id: str, kind: str, email: str, exclude_id: str = None) -> bool:
        sql = "SELECT 1 AS x FROM portal_users WHERE company_id=%s AND kind=%s AND LOWER(email)=LOWER(%s)"
        params = [company_id, kind, _s(email)]
        if exclude_id:
            sql += " AND id<>%s"; params.append(exclude_id)
        return self._row(sql, params) is not None

    def create_invite(self, company_id: str, data: dict, token: str, expires_at: datetime,
                      created_by: str = "") -> Optional[dict]:
        kind = data.get("kind") if data.get("kind") in USER_KINDS else "customer"
        email = _s(data.get("email")).lower()
        if not email or "@" not in email:
            return None
        if self.email_taken(company_id, kind, email):
            return None
        uid = str(uuid.uuid4())
        party_key = _s(data.get("party_key")) or _s(data.get("org_name")) or _s(data.get("full_name"))
        n = self._exec(
            """INSERT INTO portal_users(id,company_id,kind,email,full_name,org_name,tin,phone,party_key,
                   invite_token,invite_expires_at,created_by)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (uid, company_id, kind, email, _s(data.get("full_name")), _s(data.get("org_name")),
             _s(data.get("tin")), _s(data.get("phone")), party_key, token, expires_at, created_by))
        return self.get_user(uid, company_id) if n > 0 else None

    def update_user(self, user_id: str, company_id: str, data: dict) -> bool:
        kind = data.get("kind") if data.get("kind") in USER_KINDS else None
        email = _s(data.get("email")).lower()
        current = self.get_user(user_id, company_id)
        if not current:
            return False
        kind = kind or current["kind"]
        email = email or current["email"]
        if self.email_taken(company_id, kind, email, exclude_id=user_id):
            return False
        party_key = _s(data.get("party_key")) or current.get("party_key") or ""
        return self._exec(
            """UPDATE portal_users SET kind=%s,email=%s,full_name=%s,org_name=%s,tin=%s,phone=%s,party_key=%s,
                   is_active=%s WHERE id=%s AND company_id=%s""",
            (kind, email, _s(data.get("full_name")), _s(data.get("org_name")), _s(data.get("tin")),
             _s(data.get("phone")), party_key, bool(data.get("is_active", current.get("is_active", True))),
             user_id, company_id)) > 0

    def update_profile(self, user_id: str, full_name: str, phone: str) -> bool:
        return self._exec("UPDATE portal_users SET full_name=%s, phone=%s WHERE id=%s",
                          (_s(full_name), _s(phone), user_id)) > 0

    def set_active(self, user_id: str, company_id: str, active: bool) -> bool:
        return self._exec("UPDATE portal_users SET is_active=%s WHERE id=%s AND company_id=%s",
                          (bool(active), user_id, company_id)) > 0

    def unlock(self, user_id: str, company_id: str) -> bool:
        return self._exec(
            "UPDATE portal_users SET failed_attempts=0, locked_until=NULL WHERE id=%s AND company_id=%s",
            (user_id, company_id)) > 0

    def delete_user(self, user_id: str, company_id: str) -> bool:
        return self._exec("DELETE FROM portal_users WHERE id=%s AND company_id=%s",
                          (user_id, company_id)) > 0

    def set_invite_token(self, user_id: str, company_id: str, token: str, expires_at: datetime) -> bool:
        return self._exec(
            "UPDATE portal_users SET invite_token=%s, invite_expires_at=%s WHERE id=%s AND company_id=%s",
            (token, expires_at, user_id, company_id)) > 0

    def get_user_by_invite_token(self, token: str) -> Optional[dict]:
        if not token:
            return None
        return self._row(
            f"SELECT {_USER_COLS} FROM portal_users WHERE invite_token=%s AND is_active=TRUE", (token,))

    def accept_invite(self, user_id: str, password_hash: str) -> bool:
        return self._exec(
            """UPDATE portal_users SET password_hash=%s, invite_token=NULL, invite_expires_at=NULL,
                   failed_attempts=0, locked_until=NULL WHERE id=%s""",
            (password_hash, user_id)) > 0

    def set_reset_token(self, user_id: str, token: str, expires_at: datetime) -> bool:
        return self._exec(
            "UPDATE portal_users SET reset_token=%s, reset_expires_at=%s WHERE id=%s",
            (token, expires_at, user_id)) > 0

    def get_user_by_reset_token(self, token: str) -> Optional[dict]:
        if not token:
            return None
        return self._row(
            f"SELECT {_USER_COLS} FROM portal_users WHERE reset_token=%s AND is_active=TRUE", (token,))

    def set_password(self, user_id: str, password_hash: str) -> bool:
        return self._exec(
            """UPDATE portal_users SET password_hash=%s, reset_token=NULL, reset_expires_at=NULL,
                   failed_attempts=0, locked_until=NULL WHERE id=%s""",
            (password_hash, user_id)) > 0

    def password_hash_for(self, user_id: str) -> Optional[str]:
        row = self._row("SELECT password_hash FROM portal_users WHERE id=%s", (user_id,))
        return row.get("password_hash") if row else None

    def record_login_failure(self, user_id: str, failed_attempts: int, locked_until) -> None:
        self._exec("UPDATE portal_users SET failed_attempts=%s, locked_until=%s WHERE id=%s",
                   (int(failed_attempts), locked_until, user_id))

    def record_login_success(self, user_id: str) -> None:
        self._exec(
            "UPDATE portal_users SET failed_attempts=0, locked_until=NULL, last_login_at=NOW() WHERE id=%s",
            (user_id,))

    def user_stats(self, company_id: str) -> dict:
        rows = self._rows(
            """SELECT kind, COUNT(*) AS total,
                      SUM(CASE WHEN password_hash IS NOT NULL THEN 1 ELSE 0 END) AS activated,
                      SUM(CASE WHEN locked_until IS NOT NULL AND locked_until > NOW() THEN 1 ELSE 0 END) AS locked
               FROM portal_users WHERE company_id=%s GROUP BY kind""", (company_id,))
        out = {"customer": {"total": 0, "activated": 0, "locked": 0},
               "supplier": {"total": 0, "activated": 0, "locked": 0}}
        for r in rows:
            out[r["kind"]] = {"total": int(r["total"] or 0), "activated": int(r["activated"] or 0),
                              "locked": int(r["locked"] or 0)}
        return out

    # ── customer views ───────────────────────────────────────────
    def customer_invoices(self, company_id: str, party_key: str) -> List[dict]:
        if not party_key or not self._has("vat_income", "customer_name"):
            return []
        tin = "customer_tin" if self._has("vat_income", "customer_tin") else None
        active = " AND is_active=TRUE" if self._has("vat_income", "is_active") else ""
        params = [company_id, party_key] + ([party_key] if tin else [])
        return self._rows(
            f"""SELECT income_id, contract_date, description, category, invoice_number,
                       gross_amount, vat_amount, net_amount, vat_type, vat_rate, customer_name
                FROM vat_income WHERE company_id=%s AND {_party_clause('customer_name', tin)}{active}
                ORDER BY contract_date DESC NULLS LAST, created_date DESC NULLS LAST LIMIT 500""",
            params)

    def customer_invoice(self, company_id: str, party_key: str, income_id: str) -> Optional[dict]:
        if not party_key or not self._has("vat_income", "customer_name"):
            return None
        tin = "customer_tin" if self._has("vat_income", "customer_tin") else None
        params = [income_id, company_id, party_key] + ([party_key] if tin else [])
        return self._row(
            f"""SELECT * FROM vat_income WHERE income_id=%s AND company_id=%s
                AND {_party_clause('customer_name', tin)}""", params)

    @staticmethod
    def totals(rows: List[dict], fields=("gross_amount", "vat_amount", "net_amount")) -> dict:
        out = {f: 0.0 for f in fields}
        for r in rows:
            for f in fields:
                out[f] += _num(r.get(f))
        out["count"] = len(rows)
        return out

    def customer_cpos(self, company_id: str, party_key: str) -> List[dict]:
        if not party_key or not self._has("cpo_records", "name"):
            return []
        return self._rows(
            f"""SELECT id, name, date, amount, bid_name, is_returned, returned_date, created_at
                FROM cpo_records WHERE company_id=%s AND {_party_clause('name')}
                ORDER BY date DESC, created_at DESC LIMIT 500""", (company_id, party_key))

    def customer_bookings(self, company_id: str, party_key: str) -> List[dict]:
        """EMS bookings/quotations raised for this client."""
        if not party_key or not self._has("ems_bookings", "client_name"):
            return []
        return self._rows(
            f"""SELECT b.id, b.event_name, b.event_start, b.event_end, b.status,
                       b.hall_rent, b.services_total, b.total_amount, v.name AS venue_name
                FROM ems_bookings b LEFT JOIN ems_venues v ON v.id=b.venue_id
                WHERE b.company_id=%s AND {_party_clause('b.client_name')}
                ORDER BY b.event_start DESC LIMIT 200""", (company_id, party_key))

    def customer_projects(self, company_id: str, party_key: str) -> List[dict]:
        """pm_projects has no client column in the base schema; use one if
        a later migration added it, otherwise return [] (never guess)."""
        if not party_key:
            return []
        cols = self._table_columns("pm_projects")
        client_col = next((c for c in ("client_name", "customer_name", "client", "customer") if c in cols), None)
        if not client_col:
            return []
        return self._rows(
            f"""SELECT id, name, classification, status, start_date, end_date, total_budget
                FROM pm_projects WHERE company_id=%s AND {_party_clause(client_col)}
                ORDER BY start_date DESC NULLS LAST LIMIT 200""", (company_id, party_key))

    def party_contracts(self, company_id: str, party_key: str, party_types: tuple) -> List[dict]:
        if not party_key or not self._has("contracts", "party_name"):
            return []
        ph = ",".join(["%s"] * len(party_types))
        return self._rows(
            f"""SELECT id, title, party_type, contract_type, value, currency, start_date, end_date, status
                FROM contracts WHERE company_id=%s AND party_type IN ({ph})
                AND {_party_clause('party_name', 'party_reference')}
                ORDER BY start_date DESC NULLS LAST LIMIT 200""",
            [company_id, *party_types, party_key, party_key])

    # ── supplier views ───────────────────────────────────────────
    def _vendor_ids(self, company_id: str, party_key: str) -> List[str]:
        if not party_key or not self._has("proc_vendors", "name"):
            return []
        tin = "tin_number" if self._has("proc_vendors", "tin_number") else None
        params = [company_id, party_key] + ([party_key] if tin else [])
        rows = self._rows(
            f"SELECT id FROM proc_vendors WHERE company_id=%s AND {_party_clause('name', tin)}", params)
        return [r["id"] for r in rows]

    def supplier_orders(self, company_id: str, party_key: str) -> List[dict]:
        vids = self._vendor_ids(company_id, party_key)
        if not vids or not self._has("proc_purchase_orders", "vendor_id"):
            return []
        ph = ",".join(["%s"] * len(vids))
        return self._rows(
            f"""SELECT po.id, po.title, po.delivery_date, po.payment_terms, po.total_amount, po.status,
                       po.grn_received, po.invoice_matched, po.created_at, po.pr_id, v.name AS vendor_name
                FROM proc_purchase_orders po JOIN proc_vendors v ON v.id=po.vendor_id
                WHERE po.company_id=%s AND po.vendor_id IN ({ph})
                ORDER BY po.created_at DESC LIMIT 500""", [company_id, *vids])

    def supplier_order(self, company_id: str, party_key: str, po_id: str) -> Optional[dict]:
        vids = self._vendor_ids(company_id, party_key)
        if not vids:
            return None
        ph = ",".join(["%s"] * len(vids))
        po = self._row(
            f"""SELECT po.*, v.name AS vendor_name FROM proc_purchase_orders po
                JOIN proc_vendors v ON v.id=po.vendor_id
                WHERE po.id=%s AND po.company_id=%s AND po.vendor_id IN ({ph})""",
            [po_id, company_id, *vids])
        if not po:
            return None
        po["lines"] = self._rows("SELECT * FROM proc_po_lines WHERE po_id=%s ORDER BY id", (po_id,)) \
            if self._has("proc_po_lines", "po_id") else []
        po["grns"] = self._rows(
            "SELECT * FROM proc_grn WHERE po_id=%s AND company_id=%s ORDER BY received_date DESC",
            (po_id, company_id)) if self._has("proc_grn", "po_id") else []
        po["invoices"] = self._rows(
            "SELECT * FROM proc_invoices WHERE po_id=%s AND company_id=%s ORDER BY invoice_date DESC",
            (po_id, company_id)) if self._has("proc_invoices", "po_id") else []
        po["requisition"] = None
        if po.get("pr_id") and self._has("proc_purchase_requisitions", "id"):
            po["requisition"] = self._row(
                "SELECT id, title, department, status, total_amount, created_at FROM proc_purchase_requisitions "
                "WHERE id=%s AND company_id=%s", (po["pr_id"], company_id))
        return po

    def supplier_requisitions(self, company_id: str, party_key: str) -> List[dict]:
        """Requisitions that resulted in a PO awarded to this supplier."""
        vids = self._vendor_ids(company_id, party_key)
        if not vids or not self._has("proc_purchase_requisitions", "id"):
            return []
        ph = ",".join(["%s"] * len(vids))
        return self._rows(
            f"""SELECT DISTINCT pr.id, pr.title, pr.department, pr.status, pr.total_amount, pr.created_at
                FROM proc_purchase_requisitions pr
                JOIN proc_purchase_orders po ON po.pr_id=pr.id AND po.company_id=pr.company_id
                WHERE pr.company_id=%s AND po.vendor_id IN ({ph})
                ORDER BY pr.created_at DESC LIMIT 200""", [company_id, *vids])

    def supplier_expenses(self, company_id: str, party_key: str) -> List[dict]:
        if not party_key or not self._has("vat_expenses", "supplier_name"):
            return []
        tin = "supplier_tin" if self._has("vat_expenses", "supplier_tin") else None
        active = " AND is_active=TRUE" if self._has("vat_expenses", "is_active") else ""
        params = [company_id, party_key] + ([party_key] if tin else [])
        return self._rows(
            f"""SELECT expense_id, expense_date, description, category, receipt_number,
                       gross_amount, vat_amount, net_amount, vat_type, supplier_name
                FROM vat_expenses WHERE company_id=%s AND {_party_clause('supplier_name', tin)}{active}
                ORDER BY expense_date DESC NULLS LAST, created_date DESC NULLS LAST LIMIT 500""",
            params)

    def supplier_proc_invoices(self, company_id: str, party_key: str) -> List[dict]:
        vids = self._vendor_ids(company_id, party_key)
        if not vids or not self._has("proc_invoices", "po_id"):
            return []
        ph = ",".join(["%s"] * len(vids))
        return self._rows(
            f"""SELECT i.id, i.invoice_number, i.invoice_date, i.amount, i.status, i.po_id, po.title AS po_title
                FROM proc_invoices i JOIN proc_purchase_orders po ON po.id=i.po_id
                WHERE i.company_id=%s AND po.vendor_id IN ({ph})
                ORDER BY i.invoice_date DESC LIMIT 500""", [company_id, *vids])

    # ── tickets ──────────────────────────────────────────────────
    def create_ticket(self, company_id: str, portal_user_id: str, subject: str, body: str,
                      priority: str = "normal") -> Optional[dict]:
        subject = _s(subject)[:200]
        if not subject:
            return None
        tid = str(uuid.uuid4())
        n = self._exec(
            "INSERT INTO portal_tickets(id,company_id,portal_user_id,subject,body,priority) VALUES(%s,%s,%s,%s,%s,%s)",
            (tid, company_id, portal_user_id, subject, _s(body),
             priority if priority in TICKET_PRIORITIES else "normal"))
        return self.get_ticket(tid, company_id) if n > 0 else None

    def user_tickets(self, company_id: str, portal_user_id: str) -> List[dict]:
        return self._rows(
            """SELECT t.*, (SELECT COUNT(*) FROM portal_ticket_messages m WHERE m.ticket_id=t.id) AS message_count
               FROM portal_tickets t WHERE t.company_id=%s AND t.portal_user_id=%s
               ORDER BY t.updated_at DESC""", (company_id, portal_user_id))

    def company_tickets(self, company_id: str, status: Optional[str] = None) -> List[dict]:
        sql = """SELECT t.*, u.email, u.full_name, u.org_name, u.kind,
                        (SELECT COUNT(*) FROM portal_ticket_messages m WHERE m.ticket_id=t.id) AS message_count
                 FROM portal_tickets t LEFT JOIN portal_users u ON u.id=t.portal_user_id
                 WHERE t.company_id=%s"""
        params = [company_id]
        if status in TICKET_STATUSES:
            sql += " AND t.status=%s"; params.append(status)
        sql += " ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, t.updated_at DESC"
        return self._rows(sql, params)

    def get_ticket(self, ticket_id: str, company_id: str, portal_user_id: Optional[str] = None) -> Optional[dict]:
        sql = """SELECT t.*, u.email, u.full_name, u.org_name, u.kind
                 FROM portal_tickets t LEFT JOIN portal_users u ON u.id=t.portal_user_id
                 WHERE t.id=%s AND t.company_id=%s"""
        params = [ticket_id, company_id]
        if portal_user_id:
            sql += " AND t.portal_user_id=%s"; params.append(portal_user_id)
        t = self._row(sql, params)
        if t:
            t["messages"] = self._rows(
                "SELECT * FROM portal_ticket_messages WHERE ticket_id=%s ORDER BY created_at", (ticket_id,))
        return t

    def add_ticket_message(self, ticket_id: str, author_kind: str, author: str, body: str,
                           reopen: bool = False) -> bool:
        body = _s(body)
        if not body:
            return False
        n = self._exec(
            "INSERT INTO portal_ticket_messages(id,ticket_id,author_kind,author,body) VALUES(%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), ticket_id, "staff" if author_kind == "staff" else "portal", _s(author)[:120], body))
        if n > 0:
            if reopen:
                self._exec("UPDATE portal_tickets SET updated_at=NOW(), status=CASE WHEN status IN ('resolved','closed') THEN 'open' ELSE status END WHERE id=%s", (ticket_id,))
            else:
                self._exec("UPDATE portal_tickets SET updated_at=NOW() WHERE id=%s", (ticket_id,))
        return n > 0

    def set_ticket_status(self, ticket_id: str, company_id: str, status: str) -> bool:
        if status not in TICKET_STATUSES:
            return False
        return self._exec("UPDATE portal_tickets SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                          (status, ticket_id, company_id)) > 0

    def ticket_stats(self, company_id: str) -> dict:
        rows = self._rows("SELECT status, COUNT(*) AS c FROM portal_tickets WHERE company_id=%s GROUP BY status",
                          (company_id,))
        out = {s: 0 for s in TICKET_STATUSES}
        for r in rows:
            out[r["status"]] = int(r["c"])
        return out

    # ── documents ────────────────────────────────────────────────
    def create_document(self, company_id: str, portal_user_id: str, kind: str, filename: str,
                        stored_path: str, size: int, related_ref: str = "") -> Optional[dict]:
        did = str(uuid.uuid4())
        n = self._exec(
            """INSERT INTO portal_documents(id,company_id,portal_user_id,kind,filename,stored_path,size,related_ref)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (did, company_id, portal_user_id, kind if kind in DOCUMENT_KINDS else "other",
             _s(filename)[:255], stored_path, int(size or 0), _s(related_ref)[:120]))
        return self.get_document(did, company_id) if n > 0 else None

    def user_documents(self, company_id: str, portal_user_id: str) -> List[dict]:
        return self._rows(
            "SELECT * FROM portal_documents WHERE company_id=%s AND portal_user_id=%s ORDER BY uploaded_at DESC",
            (company_id, portal_user_id))

    def company_documents(self, company_id: str, status: Optional[str] = None) -> List[dict]:
        sql = """SELECT d.*, u.email, u.org_name, u.full_name FROM portal_documents d
                 LEFT JOIN portal_users u ON u.id=d.portal_user_id WHERE d.company_id=%s"""
        params = [company_id]
        if status in DOCUMENT_STATUSES:
            sql += " AND d.status=%s"; params.append(status)
        sql += " ORDER BY CASE d.status WHEN 'received' THEN 0 ELSE 1 END, d.uploaded_at DESC"
        return self._rows(sql, params)

    def get_document(self, doc_id: str, company_id: str, portal_user_id: Optional[str] = None) -> Optional[dict]:
        sql = """SELECT d.*, u.email, u.org_name FROM portal_documents d
                 LEFT JOIN portal_users u ON u.id=d.portal_user_id WHERE d.id=%s AND d.company_id=%s"""
        params = [doc_id, company_id]
        if portal_user_id:
            sql += " AND d.portal_user_id=%s"; params.append(portal_user_id)
        return self._row(sql, params)

    def review_document(self, doc_id: str, company_id: str, status: str, note: str = "") -> bool:
        if status not in DOCUMENT_STATUSES:
            return False
        return self._exec("UPDATE portal_documents SET status=%s, staff_note=%s WHERE id=%s AND company_id=%s",
                          (status, _s(note)[:1000], doc_id, company_id)) > 0

    def document_stats(self, company_id: str) -> dict:
        rows = self._rows("SELECT status, COUNT(*) AS c FROM portal_documents WHERE company_id=%s GROUP BY status",
                          (company_id,))
        out = {s: 0 for s in DOCUMENT_STATUSES}
        for r in rows:
            out[r["status"]] = int(r["c"])
        return out

    # ── RFQs ─────────────────────────────────────────────────────
    def create_rfq(self, company_id: str, data: dict, created_by: str = "") -> Optional[dict]:
        title = _s(data.get("title"))[:200]
        if not title:
            return None
        rid = str(uuid.uuid4())
        n = self._exec(
            "INSERT INTO portal_rfqs(id,company_id,title,description,due_at,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (rid, company_id, title, _s(data.get("description")), _opt(data.get("due_at")), created_by))
        return self.get_rfq(rid, company_id) if n > 0 else None

    def update_rfq(self, rfq_id: str, company_id: str, data: dict) -> bool:
        title = _s(data.get("title"))[:200]
        if not title:
            return False
        return self._exec(
            "UPDATE portal_rfqs SET title=%s, description=%s, due_at=%s WHERE id=%s AND company_id=%s",
            (title, _s(data.get("description")), _opt(data.get("due_at")), rfq_id, company_id)) > 0

    def set_rfq_status(self, rfq_id: str, company_id: str, status: str) -> bool:
        if status not in RFQ_STATUSES:
            return False
        return self._exec("UPDATE portal_rfqs SET status=%s WHERE id=%s AND company_id=%s",
                          (status, rfq_id, company_id)) > 0

    def company_rfqs(self, company_id: str) -> List[dict]:
        return self._rows(
            """SELECT r.*,
                      (SELECT COUNT(*) FROM portal_rfq_invitations i WHERE i.rfq_id=r.id) AS invited_count,
                      (SELECT COUNT(*) FROM portal_rfq_responses p WHERE p.rfq_id=r.id) AS response_count
               FROM portal_rfqs r WHERE r.company_id=%s
               ORDER BY CASE r.status WHEN 'open' THEN 0 ELSE 1 END, r.created_at DESC""", (company_id,))

    def get_rfq(self, rfq_id: str, company_id: str) -> Optional[dict]:
        return self._row("SELECT * FROM portal_rfqs WHERE id=%s AND company_id=%s", (rfq_id, company_id))

    def rfq_invitations(self, rfq_id: str) -> List[dict]:
        return self._rows(
            """SELECT i.*, u.email, u.org_name, u.full_name FROM portal_rfq_invitations i
               JOIN portal_users u ON u.id=i.portal_user_id WHERE i.rfq_id=%s ORDER BY u.org_name, u.email""",
            (rfq_id,))

    def invite_suppliers(self, rfq_id: str, company_id: str, user_ids: List[str]) -> int:
        """Only suppliers of the same company can be invited."""
        count = 0
        for uid in user_ids:
            u = self.get_user(uid, company_id)
            if not u or u.get("kind") != "supplier":
                continue
            n = self._exec(
                "INSERT INTO portal_rfq_invitations(rfq_id, portal_user_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (rfq_id, uid))
            if n > 0:
                count += 1
        return count

    def rfq_responses(self, rfq_id: str) -> List[dict]:
        return self._rows(
            """SELECT p.*, u.email, u.org_name, u.full_name FROM portal_rfq_responses p
               JOIN portal_users u ON u.id=p.portal_user_id WHERE p.rfq_id=%s ORDER BY p.amount ASC""",
            (rfq_id,))

    def supplier_rfqs(self, company_id: str, portal_user_id: str) -> List[dict]:
        return self._rows(
            """SELECT r.*, i.status AS invitation_status, p.amount AS my_amount, p.submitted_at
               FROM portal_rfq_invitations i
               JOIN portal_rfqs r ON r.id=i.rfq_id AND r.company_id=%s
               LEFT JOIN portal_rfq_responses p ON p.rfq_id=r.id AND p.portal_user_id=i.portal_user_id
               WHERE i.portal_user_id=%s
               ORDER BY CASE r.status WHEN 'open' THEN 0 ELSE 1 END, r.due_at ASC NULLS LAST""",
            (company_id, portal_user_id))

    def supplier_rfq(self, rfq_id: str, company_id: str, portal_user_id: str) -> Optional[dict]:
        """An RFQ is visible to a supplier only through an invitation."""
        r = self._row(
            """SELECT r.*, i.status AS invitation_status FROM portal_rfq_invitations i
               JOIN portal_rfqs r ON r.id=i.rfq_id
               WHERE i.rfq_id=%s AND i.portal_user_id=%s AND r.company_id=%s""",
            (rfq_id, portal_user_id, company_id))
        if r:
            r["my_response"] = self._row(
                "SELECT * FROM portal_rfq_responses WHERE rfq_id=%s AND portal_user_id=%s",
                (rfq_id, portal_user_id))
        return r

    def submit_rfq_response(self, rfq_id: str, company_id: str, portal_user_id: str, data: dict,
                            attachment_path: Optional[str] = None) -> bool:
        rfq = self.supplier_rfq(rfq_id, company_id, portal_user_id)
        if not rfq or rfq.get("status") != "open":
            return False
        if rfq.get("due_at") and isinstance(rfq["due_at"], datetime) and rfq["due_at"] < datetime.now():
            return False
        amount = _num(data.get("amount"), None)
        if amount is None or amount < 0:
            return False
        existing = rfq.get("my_response")
        path = attachment_path or (existing or {}).get("attachment_path")
        if existing:
            n = self._exec(
                """UPDATE portal_rfq_responses SET amount=%s, currency=%s, delivery_days=%s, notes=%s,
                       attachment_path=%s, submitted_at=NOW() WHERE id=%s""",
                (amount, _s(data.get("currency")) or "ETB", _int(data.get("delivery_days")),
                 _s(data.get("notes")), path, existing["id"]))
        else:
            n = self._exec(
                """INSERT INTO portal_rfq_responses(id,rfq_id,portal_user_id,amount,currency,delivery_days,notes,attachment_path)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), rfq_id, portal_user_id, amount, _s(data.get("currency")) or "ETB",
                 _int(data.get("delivery_days")), _s(data.get("notes")), path))
        if n > 0:
            self._exec("UPDATE portal_rfq_invitations SET status='responded' WHERE rfq_id=%s AND portal_user_id=%s",
                       (rfq_id, portal_user_id))
        return n > 0

    def decline_rfq(self, rfq_id: str, company_id: str, portal_user_id: str) -> bool:
        rfq = self.supplier_rfq(rfq_id, company_id, portal_user_id)
        if not rfq or rfq.get("status") != "open":
            return False
        return self._exec(
            "UPDATE portal_rfq_invitations SET status='declined' WHERE rfq_id=%s AND portal_user_id=%s",
            (rfq_id, portal_user_id)) > 0

    # ── home dashboards ──────────────────────────────────────────
    def customer_home(self, company_id: str, user: dict) -> dict:
        inv = self.customer_invoices(company_id, user.get("party_key", ""))
        return {
            "invoice_totals": self.totals(inv),
            "recent_invoices": inv[:5],
            "cpo_count": len(self.customer_cpos(company_id, user.get("party_key", ""))),
            "open_tickets": sum(1 for t in self.user_tickets(company_id, user["id"])
                                if t.get("status") in ("open", "in_progress")),
        }

    def supplier_home(self, company_id: str, user: dict) -> dict:
        orders = self.supplier_orders(company_id, user.get("party_key", ""))
        rfqs = self.supplier_rfqs(company_id, user["id"])
        docs = self.user_documents(company_id, user["id"])
        return {
            "open_orders": [o for o in orders if o.get("status") == "open"][:5],
            "order_count": len(orders),
            "open_rfqs": [r for r in rfqs if r.get("status") == "open" and r.get("invitation_status") == "invited"],
            "pending_docs": sum(1 for d in docs if d.get("status") == "received"),
            "open_tickets": sum(1 for t in self.user_tickets(company_id, user["id"])
                                if t.get("status") in ("open", "in_progress")),
        }


portal_store = PortalDataStore()

__all__ = ["portal_store", "PortalDataStore", "ensure_schema", "upload_root",
           "TICKET_STATUSES", "TICKET_PRIORITIES", "DOCUMENT_KINDS", "DOCUMENT_STATUSES",
           "RFQ_STATUSES", "USER_KINDS"]
