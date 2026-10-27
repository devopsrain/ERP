"""
Security Operations (manned guarding) — PostgreSQL data store.

Tables (every one carries ``company_id TEXT NOT NULL DEFAULT 'default'``)
    secops_settings          per-company night window, allowance, overtime multipliers
    secops_holidays          optional public-holiday list (2.5x overtime)
    secops_sequences         gapless numbering (incident numbers INC-YYYY-000001, guard numbers)
    secops_clients           guarding clients (→ contracts.id / commercial_customers.id optional)
    secops_sites             guarded sites with post orders
    secops_posts             posts inside a site (coverage, guards required, armed)
    secops_shift_templates   Day 06-18 / Night 18-06 / …
    secops_guards            guard profile extending ``employees``
    secops_rosters           one roster per site per period
    secops_assignments       guard × post × shift × day
    secops_attendance        check-in / check-out with computed hours
    secops_kit_items         uniform / radio / torch / … catalogue (→ inventory_items optional)
    secops_kit_issues        kit issued to a guard or a site
    secops_incidents         incident register (attachments through document_storage)
    secops_payroll_feed      monthly attendance → payroll lines

Public Python API (importable by other modules; every call swallows DB errors
and returns None / [] / {} / False so a missing table never breaks a caller):

    from secops_data_store import (guard_for_user, record_checkin,
                                   coverage_summary, open_incident_count)

    guard_for_user(company_id, username, email) -> dict | None
        Maps the logged-in user → employees row (hrm_store.get_employee_for_user)
        → secops_guards row. The dict is the guard profile joined with the
        employee (name, basic_salary, …) plus ``home_site_name``.

    record_checkin(company_id, guard_id=None, guard_no=None, employee_id=None,
                   action="in"|"out", source="self", lat=None, lng=None,
                   note="", recorded_by="", at=None) -> dict
        "in": opens an attendance row for today's assignment (or the guard's
        home site when nothing is rostered); status present/late by the
        shift's start time and the company's grace minutes.
        "out": closes the open row and fills hours_worked / night_hours /
        overtime_hours.  Returns {"ok": bool, "error": str, "attendance": dict}.

    coverage_summary(company_id, day) -> dict
        {"day", "sites": [{site_id, site_name, client_name, required, assigned,
         present, uncovered, coverage_pct, roster_id}], "sites_total",
         "sites_covered", "posts_uncovered", "guards_required", "guards_assigned",
         "guards_present", "absent", "late", "on_duty_now"}

    open_incident_count(company_id) -> int
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Iterable, List, Optional

from db import get_conn
import secops_logic as L

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secops_settings (
    company_id                TEXT PRIMARY KEY DEFAULT 'default',
    night_start               TIME NOT NULL DEFAULT '22:00',
    night_end                 TIME NOT NULL DEFAULT '06:00',
    night_allowance_per_hour  NUMERIC(12,2) NOT NULL DEFAULT 0,
    overtime_weekday          NUMERIC(5,2) NOT NULL DEFAULT 1.5,
    overtime_weekend          NUMERIC(5,2) NOT NULL DEFAULT 2.0,
    overtime_holiday          NUMERIC(5,2) NOT NULL DEFAULT 2.5,
    weekend_days              TEXT NOT NULL DEFAULT '6',
    late_grace_minutes        INT NOT NULL DEFAULT 15,
    default_hours_per_day     NUMERIC(5,2) NOT NULL DEFAULT 8,
    billing_currency          TEXT NOT NULL DEFAULT 'ETB',
    updated_at                TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS secops_holidays (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    holiday_date  DATE NOT NULL,
    name          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_secops_holidays_company ON secops_holidays(company_id, holiday_date);

CREATE TABLE IF NOT EXISTS secops_sequences (
    company_id  TEXT NOT NULL DEFAULT 'default',
    kind        TEXT NOT NULL,
    year        INT  NOT NULL,
    last_no     INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, kind, year)
);

CREATE TABLE IF NOT EXISTS secops_clients (
    id                          TEXT PRIMARY KEY,
    company_id                  TEXT NOT NULL DEFAULT 'default',
    name                        TEXT NOT NULL,
    contact                     TEXT NOT NULL DEFAULT '',
    phone                       TEXT NOT NULL DEFAULT '',
    email                       TEXT NOT NULL DEFAULT '',
    address                     TEXT NOT NULL DEFAULT '',
    contract_id                 TEXT,
    customer_id                 TEXT,
    billing_rate_per_guard_hour NUMERIC(12,2) NOT NULL DEFAULT 0,
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    notes                       TEXT NOT NULL DEFAULT '',
    created_at                  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_clients_company ON secops_clients(company_id);

CREATE TABLE IF NOT EXISTS secops_sites (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL DEFAULT 'default',
    client_id               TEXT,
    code                    TEXT NOT NULL DEFAULT '',
    name                    TEXT NOT NULL,
    address                 TEXT NOT NULL DEFAULT '',
    region                  TEXT NOT NULL DEFAULT '',
    gps_lat                 NUMERIC(10,6),
    gps_lng                 NUMERIC(10,6),
    site_type               TEXT NOT NULL DEFAULT 'office',
    supervisor_employee_id  TEXT,
    start_date              DATE,
    end_date                DATE,
    status                  TEXT NOT NULL DEFAULT 'active',
    instructions            TEXT NOT NULL DEFAULT '',
    required_guard_grade    TEXT NOT NULL DEFAULT 'guard',
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_sites_company ON secops_sites(company_id, status);

CREATE TABLE IF NOT EXISTS secops_posts (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    site_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    coverage        TEXT NOT NULL DEFAULT '24h',
    hours_per_day   NUMERIC(5,2) NOT NULL DEFAULT 24,
    guards_required INT NOT NULL DEFAULT 1,
    armed           BOOLEAN NOT NULL DEFAULT FALSE,
    grade_required  TEXT NOT NULL DEFAULT 'guard',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_posts_site ON secops_posts(site_id);

CREATE TABLE IF NOT EXISTS secops_shift_templates (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    start_time    TIME NOT NULL DEFAULT '06:00',
    end_time      TIME NOT NULL DEFAULT '18:00',
    is_night      BOOLEAN NOT NULL DEFAULT FALSE,
    break_minutes INT NOT NULL DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order    INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_secops_shifts_company ON secops_shift_templates(company_id);

CREATE TABLE IF NOT EXISTS secops_guards (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL DEFAULT 'default',
    employee_id             TEXT NOT NULL,
    guard_no                TEXT NOT NULL DEFAULT '',
    grade                   TEXT NOT NULL DEFAULT 'guard',
    licence_no              TEXT NOT NULL DEFAULT '',
    licence_expiry          DATE,
    firearm_permit_no       TEXT,
    firearm_permit_expiry   DATE,
    medical_fitness_expiry  DATE,
    training_level          TEXT NOT NULL DEFAULT 'basic',
    home_site_id            TEXT,
    status                  TEXT NOT NULL DEFAULT 'active',
    notes                   TEXT NOT NULL DEFAULT '',
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_secops_guards_company ON secops_guards(company_id, status);

CREATE TABLE IF NOT EXISTS secops_rosters (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    site_id       TEXT NOT NULL,
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',
    created_by    TEXT NOT NULL DEFAULT '',
    published_at  TIMESTAMP,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_rosters_site ON secops_rosters(company_id, site_id, period_start);

CREATE TABLE IF NOT EXISTS secops_assignments (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    roster_id         TEXT NOT NULL,
    post_id           TEXT NOT NULL,
    guard_id          TEXT NOT NULL,
    shift_template_id TEXT,
    duty_date         DATE NOT NULL,
    start_at          TIMESTAMP,
    end_at            TIMESTAMP,
    is_relief         BOOLEAN NOT NULL DEFAULT FALSE,
    status            TEXT NOT NULL DEFAULT 'scheduled',
    notes             TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_assign_roster ON secops_assignments(roster_id);
CREATE INDEX IF NOT EXISTS idx_secops_assign_guard ON secops_assignments(company_id, guard_id, duty_date);
CREATE INDEX IF NOT EXISTS idx_secops_assign_date ON secops_assignments(company_id, duty_date);

CREATE TABLE IF NOT EXISTS secops_attendance (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    assignment_id   TEXT,
    guard_id        TEXT NOT NULL,
    site_id         TEXT,
    post_id         TEXT,
    duty_date       DATE NOT NULL,
    check_in_at     TIMESTAMP,
    check_out_at    TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'present',
    hours_worked    NUMERIC(6,2) NOT NULL DEFAULT 0,
    night_hours     NUMERIC(6,2) NOT NULL DEFAULT 0,
    overtime_hours  NUMERIC(6,2) NOT NULL DEFAULT 0,
    late_minutes    INT NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'manual',
    gps_lat         NUMERIC(10,6),
    gps_lng         NUMERIC(10,6),
    note            TEXT NOT NULL DEFAULT '',
    recorded_by     TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_att_guard ON secops_attendance(company_id, guard_id, duty_date);
CREATE INDEX IF NOT EXISTS idx_secops_att_site ON secops_attendance(company_id, site_id, duty_date);

CREATE TABLE IF NOT EXISTS secops_kit_items (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    name              TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'uniform',
    inventory_item_id TEXT,
    tracks_serial     BOOLEAN NOT NULL DEFAULT FALSE,
    sizes             TEXT NOT NULL DEFAULT '',
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_kit_items_company ON secops_kit_items(company_id);

CREATE TABLE IF NOT EXISTS secops_kit_issues (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    kit_item_id     TEXT NOT NULL,
    guard_id        TEXT,
    site_id         TEXT,
    serial_no       TEXT NOT NULL DEFAULT '',
    size            TEXT NOT NULL DEFAULT '',
    qty             NUMERIC(10,2) NOT NULL DEFAULT 1,
    issued_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    issued_by       TEXT NOT NULL DEFAULT '',
    expected_return DATE,
    returned_at     TIMESTAMP,
    condition_out   TEXT NOT NULL DEFAULT 'good',
    condition_in    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'issued',
    client_owned    BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_secops_kit_issues_guard ON secops_kit_issues(company_id, guard_id, status);
CREATE INDEX IF NOT EXISTS idx_secops_kit_issues_site ON secops_kit_issues(company_id, site_id, status);

CREATE TABLE IF NOT EXISTS secops_incidents (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL DEFAULT 'default',
    incident_no         TEXT NOT NULL,
    site_id             TEXT NOT NULL,
    post_id             TEXT,
    guard_id            TEXT,
    occurred_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    category            TEXT NOT NULL DEFAULT 'other',
    severity            TEXT NOT NULL DEFAULT 'low',
    description         TEXT NOT NULL DEFAULT '',
    actions_taken       TEXT NOT NULL DEFAULT '',
    reported_by         TEXT NOT NULL DEFAULT '',
    reported_to_police  BOOLEAN NOT NULL DEFAULT FALSE,
    police_ref          TEXT NOT NULL DEFAULT '',
    client_notified_at  TIMESTAMP,
    client_notification_note TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'open',
    closed_at           TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_secops_incidents_company ON secops_incidents(company_id, status, occurred_at);

CREATE TABLE IF NOT EXISTS secops_payroll_feed (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL DEFAULT 'default',
    period           TEXT NOT NULL,
    guard_id         TEXT NOT NULL,
    employee_id      TEXT NOT NULL,
    days_present     INT NOT NULL DEFAULT 0,
    days_absent      INT NOT NULL DEFAULT 0,
    hours_worked     NUMERIC(8,2) NOT NULL DEFAULT 0,
    night_hours      NUMERIC(8,2) NOT NULL DEFAULT 0,
    overtime_hours   NUMERIC(8,2) NOT NULL DEFAULT 0,
    night_allowance  NUMERIC(12,2) NOT NULL DEFAULT 0,
    overtime_pay     NUMERIC(12,2) NOT NULL DEFAULT 0,
    hourly_rate      NUMERIC(12,2) NOT NULL DEFAULT 0,
    generated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    status           TEXT NOT NULL DEFAULT 'draft',
    UNIQUE (company_id, period, guard_id)
);
"""

DEFAULT_SHIFTS = (
    ("Day 06-18", "06:00", "18:00", False, 0, 1),
    ("Night 18-06", "18:00", "06:00", True, 0, 2),
    ("Morning 06-14", "06:00", "14:00", False, 0, 3),
    ("Afternoon 14-22", "14:00", "22:00", False, 0, 4),
)

_opt = L.opt
_D = L.D


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "on", "yes", "y")


def _int(v, default=0) -> int:
    try:
        return int(Decimal(str(v)))
    except Exception:
        return default


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("secops schema ready")
    except Exception as e:
        logger.error("secops schema init failed: %s", e)


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1", (table,))
    return cur.fetchone() is not None


def _table_columns(cur, table: str) -> set:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))
    return {r["column_name"] for r in cur.fetchall()}


_GUARD_SELECT = """
    SELECT g.*, e.name, e.email, e.phone_number, e.department, e.position, e.basic_salary,
           e.work_days_per_month, e.work_hours_per_day, e.hire_date, e.is_active AS employee_active,
           s.name AS home_site_name
      FROM secops_guards g
      LEFT JOIN employees e ON e.employee_id = g.employee_id AND e.company_id = g.company_id
      LEFT JOIN secops_sites s ON s.id = g.home_site_id
"""


class SecOpsDataStore:

    def ensure_schema(self):
        ensure_schema()

    # ══════════════════════════════════════════════════════════════
    #  Settings / holidays
    # ══════════════════════════════════════════════════════════════
    def get_settings(self, cid: str) -> dict:
        out = dict(L.DEFAULT_SETTINGS)
        out["company_id"] = cid
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM secops_settings WHERE company_id=%s", (cid,))
                row = cur.fetchone()
                if row:
                    out.update(dict(row))
        except Exception as e:
            logger.error("secops get_settings: %s", e)
        for k in ("night_start", "night_end"):
            v = out.get(k)
            if isinstance(v, time):
                out[k] = v.strftime("%H:%M")
        return out

    def save_settings(self, cid: str, data: dict) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secops_settings(company_id, night_start, night_end, night_allowance_per_hour,
                           overtime_weekday, overtime_weekend, overtime_holiday, weekend_days,
                           late_grace_minutes, default_hours_per_day, billing_currency, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT (company_id) DO UPDATE SET
                           night_start=EXCLUDED.night_start, night_end=EXCLUDED.night_end,
                           night_allowance_per_hour=EXCLUDED.night_allowance_per_hour,
                           overtime_weekday=EXCLUDED.overtime_weekday, overtime_weekend=EXCLUDED.overtime_weekend,
                           overtime_holiday=EXCLUDED.overtime_holiday, weekend_days=EXCLUDED.weekend_days,
                           late_grace_minutes=EXCLUDED.late_grace_minutes,
                           default_hours_per_day=EXCLUDED.default_hours_per_day,
                           billing_currency=EXCLUDED.billing_currency, updated_at=NOW()""",
                    (cid, L.parse_time(data.get("night_start")) or time(22, 0),
                     L.parse_time(data.get("night_end")) or time(6, 0),
                     _D(data.get("night_allowance_per_hour")),
                     _D(data.get("overtime_weekday")) or Decimal("1.5"),
                     _D(data.get("overtime_weekend")) or Decimal("2.0"),
                     _D(data.get("overtime_holiday")) or Decimal("2.5"),
                     ",".join(str(d) for d in sorted(L.weekend_day_set(data.get("weekend_days")))),
                     _int(data.get("late_grace_minutes"), 15),
                     _D(data.get("default_hours_per_day")) or Decimal(8),
                     (data.get("billing_currency") or "ETB").strip()[:8]))
            return True
        except Exception as e:
            logger.error("secops save_settings: %s", e); return False

    def list_holidays(self, cid: str, year: Optional[int] = None) -> List[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                if year:
                    cur.execute("SELECT * FROM secops_holidays WHERE company_id=%s AND EXTRACT(YEAR FROM holiday_date)=%s ORDER BY holiday_date", (cid, year))
                else:
                    cur.execute("SELECT * FROM secops_holidays WHERE company_id=%s ORDER BY holiday_date DESC LIMIT 200", (cid,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_holidays: %s", e); return []

    def add_holiday(self, cid: str, holiday_date, name: str) -> bool:
        d = L.parse_date(holiday_date)
        if not d:
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO secops_holidays(id, company_id, holiday_date, name) VALUES (%s,%s,%s,%s)",
                            (str(uuid.uuid4()), cid, d, (name or "").strip()[:120]))
            return True
        except Exception as e:
            logger.error("secops add_holiday: %s", e); return False

    def delete_holiday(self, cid: str, holiday_id: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM secops_holidays WHERE id=%s AND company_id=%s", (holiday_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops delete_holiday: %s", e); return False

    # ══════════════════════════════════════════════════════════════
    #  Sequences
    # ══════════════════════════════════════════════════════════════
    @staticmethod
    def _next_no(cur, cid: str, kind: str, year: int) -> int:
        cur.execute(
            """INSERT INTO secops_sequences(company_id, kind, year, last_no) VALUES (%s,%s,%s,1)
               ON CONFLICT (company_id, kind, year)
               DO UPDATE SET last_no = secops_sequences.last_no + 1
               RETURNING last_no""", (cid, kind, year))
        return int(cur.fetchone()["last_no"])

    # ══════════════════════════════════════════════════════════════
    #  Lookups into sibling modules (guarded)
    # ══════════════════════════════════════════════════════════════
    def lookups(self, cid: str) -> dict:
        out = {"contracts": [], "customers": [], "employees": [], "inventory_items": []}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                if _table_exists(cur, "contracts"):
                    cur.execute("SELECT id, title, party_name, status FROM contracts WHERE company_id=%s AND party_type='client' ORDER BY title LIMIT 300", (cid,))
                    out["contracts"] = [dict(r) for r in cur.fetchall()]
                if _table_exists(cur, "commercial_customers"):
                    cur.execute("SELECT id, code, name, contact_person, phone, email, address FROM commercial_customers WHERE company_id=%s ORDER BY name LIMIT 500", (cid,))
                    out["customers"] = [dict(r) for r in cur.fetchall()]
                if _table_exists(cur, "employees"):
                    cur.execute("SELECT employee_id, name, department, position FROM employees WHERE company_id=%s AND is_active=TRUE ORDER BY name LIMIT 1000", (cid,))
                    out["employees"] = [dict(r) for r in cur.fetchall()]
                if _table_exists(cur, "inventory_items"):
                    cur.execute("SELECT id, sku, name, current_stock, unit FROM inventory_items WHERE company_id=%s ORDER BY name LIMIT 500", (cid,))
                    out["inventory_items"] = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops lookups: %s", e)
        return out

    def employees_without_profile(self, cid: str) -> List[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT e.employee_id, e.name, e.department, e.position FROM employees e
                       WHERE e.company_id=%s AND e.is_active=TRUE
                         AND NOT EXISTS (SELECT 1 FROM secops_guards g WHERE g.company_id=e.company_id AND g.employee_id=e.employee_id)
                       ORDER BY e.name LIMIT 1000""", (cid,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops employees_without_profile: %s", e); return []

    # ══════════════════════════════════════════════════════════════
    #  Clients
    # ══════════════════════════════════════════════════════════════
    def list_clients(self, cid: str, active_only: bool = False, q: Optional[str] = None) -> List[dict]:
        try:
            sql = """SELECT c.*, (SELECT COUNT(*) FROM secops_sites s WHERE s.client_id=c.id AND s.status='active') AS active_sites
                     FROM secops_clients c WHERE c.company_id=%s"""
            params: list = [cid]
            if active_only:
                sql += " AND c.is_active=TRUE"
            if q:
                sql += " AND (c.name ILIKE %s OR c.contact ILIKE %s OR c.phone ILIKE %s)"
                params += [f"%{q}%"] * 3
            sql += " ORDER BY c.name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_clients: %s", e); return []

    def get_client(self, cid: str, client_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM secops_clients WHERE id=%s AND company_id=%s", (client_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_client: %s", e); return None

    def create_client(self, cid: str, data: dict) -> Optional[dict]:
        if not (data.get("name") or "").strip():
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secops_clients(id, company_id, name, contact, phone, email, address, contract_id,
                           customer_id, billing_rate_per_guard_hour, is_active, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (str(uuid.uuid4()), cid, data["name"].strip(), data.get("contact", ""), data.get("phone", ""),
                     data.get("email", ""), data.get("address", ""), _opt(data.get("contract_id")),
                     _opt(data.get("customer_id")), _D(data.get("billing_rate_per_guard_hour")),
                     _bool(data.get("is_active", True)), data.get("notes", "")))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops create_client: %s", e); return None

    def update_client(self, cid: str, client_id: str, data: dict) -> bool:
        if not (data.get("name") or "").strip():
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE secops_clients SET name=%s, contact=%s, phone=%s, email=%s, address=%s, contract_id=%s,
                           customer_id=%s, billing_rate_per_guard_hour=%s, is_active=%s, notes=%s, updated_at=NOW()
                       WHERE id=%s AND company_id=%s""",
                    (data["name"].strip(), data.get("contact", ""), data.get("phone", ""), data.get("email", ""),
                     data.get("address", ""), _opt(data.get("contract_id")), _opt(data.get("customer_id")),
                     _D(data.get("billing_rate_per_guard_hour")), _bool(data.get("is_active")),
                     data.get("notes", ""), client_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_client: %s", e); return False

    # ══════════════════════════════════════════════════════════════
    #  Sites & posts
    # ══════════════════════════════════════════════════════════════
    _SITE_SELECT = """
        SELECT s.*, c.name AS client_name, c.billing_rate_per_guard_hour,
               e.name AS supervisor_name,
               (SELECT COUNT(*) FROM secops_posts p WHERE p.site_id=s.id AND p.is_active=TRUE) AS post_count,
               (SELECT COALESCE(SUM(p.guards_required),0) FROM secops_posts p WHERE p.site_id=s.id AND p.is_active=TRUE) AS guards_required
          FROM secops_sites s
          LEFT JOIN secops_clients c ON c.id = s.client_id
          LEFT JOIN employees e ON e.employee_id = s.supervisor_employee_id AND e.company_id = s.company_id
    """

    def list_sites(self, cid: str, status: Optional[str] = None, client_id: Optional[str] = None,
                   q: Optional[str] = None) -> List[dict]:
        try:
            sql = self._SITE_SELECT + " WHERE s.company_id=%s"
            params: list = [cid]
            if status:
                sql += " AND s.status=%s"; params.append(status)
            if client_id:
                sql += " AND s.client_id=%s"; params.append(client_id)
            if q:
                sql += " AND (s.name ILIKE %s OR s.code ILIKE %s OR s.address ILIKE %s OR c.name ILIKE %s)"
                params += [f"%{q}%"] * 4
            sql += " ORDER BY s.status, s.name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_sites: %s", e); return []

    def get_site(self, cid: str, site_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._SITE_SELECT + " WHERE s.id=%s AND s.company_id=%s", (site_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_site: %s", e); return None

    def _site_params(self, data: dict) -> tuple:
        return (_opt(data.get("client_id")), (data.get("code") or "").strip(), data["name"].strip(),
                data.get("address", ""), data.get("region", ""), _opt(data.get("gps_lat")), _opt(data.get("gps_lng")),
                data.get("site_type") if data.get("site_type") in L.SITE_TYPES else "other",
                _opt(data.get("supervisor_employee_id")), _opt(data.get("start_date")), _opt(data.get("end_date")),
                data.get("status") if data.get("status") in L.SITE_STATUSES else "active",
                data.get("instructions", ""), data.get("required_guard_grade") or "guard")

    def create_site(self, cid: str, data: dict) -> Optional[dict]:
        if not (data.get("name") or "").strip():
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secops_sites(id, company_id, client_id, code, name, address, region, gps_lat, gps_lng,
                           site_type, supervisor_employee_id, start_date, end_date, status, instructions, required_guard_grade)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (str(uuid.uuid4()), cid) + self._site_params(data))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops create_site: %s", e); return None

    def update_site(self, cid: str, site_id: str, data: dict) -> bool:
        if not (data.get("name") or "").strip():
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE secops_sites SET client_id=%s, code=%s, name=%s, address=%s, region=%s, gps_lat=%s, gps_lng=%s,
                           site_type=%s, supervisor_employee_id=%s, start_date=%s, end_date=%s, status=%s, instructions=%s,
                           required_guard_grade=%s, updated_at=NOW()
                       WHERE id=%s AND company_id=%s""",
                    self._site_params(data) + (site_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_site: %s", e); return False

    def list_posts(self, cid: str, site_id: Optional[str] = None, active_only: bool = False) -> List[dict]:
        try:
            sql = "SELECT p.*, s.name AS site_name FROM secops_posts p JOIN secops_sites s ON s.id=p.site_id WHERE p.company_id=%s"
            params: list = [cid]
            if site_id:
                sql += " AND p.site_id=%s"; params.append(site_id)
            if active_only:
                sql += " AND p.is_active=TRUE"
            sql += " ORDER BY s.name, p.name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_posts: %s", e); return []

    def get_post(self, cid: str, post_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM secops_posts WHERE id=%s AND company_id=%s", (post_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_post: %s", e); return None

    def create_post(self, cid: str, site_id: str, data: dict) -> Optional[dict]:
        if not (data.get("name") or "").strip():
            return None
        cov = data.get("coverage") if data.get("coverage") in L.COVERAGES else "24h"
        hours = _D(data.get("hours_per_day")) or {"24h": Decimal(24), "day": Decimal(12), "night": Decimal(12)}.get(cov, Decimal(8))
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secops_posts(id, company_id, site_id, name, coverage, hours_per_day, guards_required, armed,
                           grade_required, is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (str(uuid.uuid4()), cid, site_id, data["name"].strip(), cov, hours,
                     max(1, _int(data.get("guards_required"), 1)), _bool(data.get("armed")),
                     data.get("grade_required") or "guard", _bool(data.get("is_active", True))))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops create_post: %s", e); return None

    def update_post(self, cid: str, post_id: str, data: dict) -> bool:
        if not (data.get("name") or "").strip():
            return False
        cov = data.get("coverage") if data.get("coverage") in L.COVERAGES else "24h"
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE secops_posts SET name=%s, coverage=%s, hours_per_day=%s, guards_required=%s, armed=%s,
                           grade_required=%s, is_active=%s WHERE id=%s AND company_id=%s""",
                    (data["name"].strip(), cov, _D(data.get("hours_per_day")) or Decimal(24),
                     max(1, _int(data.get("guards_required"), 1)), _bool(data.get("armed")),
                     data.get("grade_required") or "guard", _bool(data.get("is_active")), post_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_post: %s", e); return False

    def delete_post(self, cid: str, post_id: str) -> tuple[bool, str]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM secops_assignments WHERE post_id=%s AND company_id=%s", (post_id, cid))
                if cur.fetchone()["c"]:
                    cur.execute("UPDATE secops_posts SET is_active=FALSE WHERE id=%s AND company_id=%s", (post_id, cid))
                    return True, "Post has roster history — deactivated instead of deleted"
                cur.execute("DELETE FROM secops_posts WHERE id=%s AND company_id=%s", (post_id, cid))
                return cur.rowcount > 0, ""
        except Exception as e:
            logger.error("secops delete_post: %s", e); return False, str(e)

    # ══════════════════════════════════════════════════════════════
    #  Shift templates
    # ══════════════════════════════════════════════════════════════
    def list_shifts(self, cid: str, active_only: bool = False) -> List[dict]:
        try:
            sql = "SELECT * FROM secops_shift_templates WHERE company_id=%s"
            if active_only:
                sql += " AND is_active=TRUE"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql + " ORDER BY sort_order, start_time", (cid,))
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["hours"] = L.shift_hours(r)
            return rows
        except Exception as e:
            logger.error("secops list_shifts: %s", e); return []

    def seed_default_shifts(self, cid: str) -> int:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM secops_shift_templates WHERE company_id=%s", (cid,))
                if cur.fetchone()["c"]:
                    return 0
                for name, st, et, night, brk, order in DEFAULT_SHIFTS[:2]:
                    cur.execute(
                        """INSERT INTO secops_shift_templates(id, company_id, name, start_time, end_time, is_night, break_minutes, sort_order)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), cid, name, st, et, night, brk, order))
                return 2
        except Exception as e:
            logger.error("secops seed_default_shifts: %s", e); return 0

    def create_shift(self, cid: str, data: dict) -> Optional[dict]:
        if not (data.get("name") or "").strip():
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secops_shift_templates(id, company_id, name, start_time, end_time, is_night, break_minutes, is_active, sort_order)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (str(uuid.uuid4()), cid, data["name"].strip(), L.parse_time(data.get("start_time")) or time(6, 0),
                     L.parse_time(data.get("end_time")) or time(18, 0), _bool(data.get("is_night")),
                     _int(data.get("break_minutes"), 0), _bool(data.get("is_active", True)), _int(data.get("sort_order"), 0)))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops create_shift: %s", e); return None

    def update_shift(self, cid: str, shift_id: str, data: dict) -> bool:
        if not (data.get("name") or "").strip():
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE secops_shift_templates SET name=%s, start_time=%s, end_time=%s, is_night=%s, break_minutes=%s,
                           is_active=%s, sort_order=%s WHERE id=%s AND company_id=%s""",
                    (data["name"].strip(), L.parse_time(data.get("start_time")) or time(6, 0),
                     L.parse_time(data.get("end_time")) or time(18, 0), _bool(data.get("is_night")),
                     _int(data.get("break_minutes"), 0), _bool(data.get("is_active")), _int(data.get("sort_order"), 0),
                     shift_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_shift: %s", e); return False

    def delete_shift(self, cid: str, shift_id: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM secops_assignments WHERE shift_template_id=%s AND company_id=%s", (shift_id, cid))
                if cur.fetchone()["c"]:
                    cur.execute("UPDATE secops_shift_templates SET is_active=FALSE WHERE id=%s AND company_id=%s", (shift_id, cid))
                else:
                    cur.execute("DELETE FROM secops_shift_templates WHERE id=%s AND company_id=%s", (shift_id, cid))
                return True
        except Exception as e:
            logger.error("secops delete_shift: %s", e); return False

    # ══════════════════════════════════════════════════════════════
    #  Guards
    # ══════════════════════════════════════════════════════════════
    def _decorate_guard(self, g: dict, today: Optional[date] = None) -> dict:
        today = today or date.today()
        g["expiring"] = L.expiring_documents(g, today)
        g["document_status"] = L.worst_document_status(g, today)
        return g

    def list_guards(self, cid: str, status: Optional[str] = None, q: Optional[str] = None,
                    site_id: Optional[str] = None, grade: Optional[str] = None) -> List[dict]:
        try:
            sql = _GUARD_SELECT + " WHERE g.company_id=%s"
            params: list = [cid]
            if status:
                sql += " AND g.status=%s"; params.append(status)
            if site_id:
                sql += " AND g.home_site_id=%s"; params.append(site_id)
            if grade:
                sql += " AND g.grade=%s"; params.append(grade)
            if q:
                sql += " AND (e.name ILIKE %s OR g.guard_no ILIKE %s OR g.licence_no ILIKE %s OR g.employee_id ILIKE %s)"
                params += [f"%{q}%"] * 4
            sql += " ORDER BY g.status, e.name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [self._decorate_guard(dict(r)) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_guards: %s", e); return []

    def get_guard(self, cid: str, guard_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(_GUARD_SELECT + " WHERE g.id=%s AND g.company_id=%s", (guard_id, cid))
                r = cur.fetchone()
                return self._decorate_guard(dict(r)) if r else None
        except Exception as e:
            logger.error("secops get_guard: %s", e); return None

    def _guard_by(self, cid: str, **where) -> Optional[dict]:
        col, val = next(iter(where.items()))
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(_GUARD_SELECT + f" WHERE g.company_id=%s AND g.{col}=%s", (cid, val))
                r = cur.fetchone()
                return self._decorate_guard(dict(r)) if r else None
        except Exception as e:
            logger.error("secops guard lookup %s: %s", col, e); return None

    def guard_by_employee(self, cid: str, employee_id: str) -> Optional[dict]:
        return self._guard_by(cid, employee_id=employee_id)

    def guard_by_no(self, cid: str, guard_no: str) -> Optional[dict]:
        return self._guard_by(cid, guard_no=guard_no)

    def create_guard(self, cid: str, data: dict) -> tuple[Optional[dict], str]:
        emp = (data.get("employee_id") or "").strip()
        if not emp:
            return None, "Select an employee"
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM secops_guards WHERE company_id=%s AND employee_id=%s", (cid, emp))
                if cur.fetchone():
                    return None, "This employee already has a guard profile"
                guard_no = (data.get("guard_no") or "").strip()
                if not guard_no:
                    guard_no = L.guard_no_for(self._next_no(cur, cid, "guard", 0))
                cur.execute(
                    """INSERT INTO secops_guards(id, company_id, employee_id, guard_no, grade, licence_no, licence_expiry,
                           firearm_permit_no, firearm_permit_expiry, medical_fitness_expiry, training_level, home_site_id, status, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (str(uuid.uuid4()), cid, emp, guard_no,
                     data.get("grade") if data.get("grade") in L.GRADES else "guard",
                     data.get("licence_no", ""), _opt(data.get("licence_expiry")), _opt(data.get("firearm_permit_no")),
                     _opt(data.get("firearm_permit_expiry")), _opt(data.get("medical_fitness_expiry")),
                     data.get("training_level") or "basic", _opt(data.get("home_site_id")),
                     data.get("status") if data.get("status") in L.GUARD_STATUSES else "active", data.get("notes", "")))
                return dict(cur.fetchone()), ""
        except Exception as e:
            logger.error("secops create_guard: %s", e); return None, str(e)

    def update_guard(self, cid: str, guard_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE secops_guards SET guard_no=%s, grade=%s, licence_no=%s, licence_expiry=%s, firearm_permit_no=%s,
                           firearm_permit_expiry=%s, medical_fitness_expiry=%s, training_level=%s, home_site_id=%s, notes=%s,
                           updated_at=NOW() WHERE id=%s AND company_id=%s""",
                    ((data.get("guard_no") or "").strip(), data.get("grade") if data.get("grade") in L.GRADES else "guard",
                     data.get("licence_no", ""), _opt(data.get("licence_expiry")), _opt(data.get("firearm_permit_no")),
                     _opt(data.get("firearm_permit_expiry")), _opt(data.get("medical_fitness_expiry")),
                     data.get("training_level") or "basic", _opt(data.get("home_site_id")), data.get("notes", ""),
                     guard_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_guard: %s", e); return False

    def set_guard_status(self, cid: str, guard_id: str, status: str) -> tuple[bool, List[dict]]:
        """Returns (ok, outstanding kit). Kit still issued is reported when a guard exits."""
        if status not in L.GUARD_STATUSES:
            return False, []
        outstanding = self.outstanding_kit(cid, guard_id=guard_id) if status == "exited" else []
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE secops_guards SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                            (status, guard_id, cid))
                ok = cur.rowcount > 0
                if ok and status in ("exited", "suspended"):
                    cur.execute("""UPDATE secops_assignments SET status='cancelled' WHERE company_id=%s AND guard_id=%s
                                   AND duty_date >= CURRENT_DATE AND status='scheduled'""", (cid, guard_id))
            return ok, outstanding
        except Exception as e:
            logger.error("secops set_guard_status: %s", e); return False, outstanding

    def guards_expiring(self, cid: str, days: int = 30) -> List[dict]:
        today = date.today()
        return [g for g in self.list_guards(cid) if g["status"] != "exited" and L.expiring_documents(g, today, days)]

    # ══════════════════════════════════════════════════════════════
    #  Rosters & assignments
    # ══════════════════════════════════════════════════════════════
    _ROSTER_SELECT = """
        SELECT r.*, s.name AS site_name, c.name AS client_name,
               (SELECT COUNT(*) FROM secops_assignments a WHERE a.roster_id=r.id AND a.status<>'cancelled') AS assignment_count
          FROM secops_rosters r JOIN secops_sites s ON s.id=r.site_id LEFT JOIN secops_clients c ON c.id=s.client_id
    """

    def list_rosters(self, cid: str, site_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> List[dict]:
        try:
            sql = self._ROSTER_SELECT + " WHERE r.company_id=%s"
            params: list = [cid]
            if site_id:
                sql += " AND r.site_id=%s"; params.append(site_id)
            if status:
                sql += " AND r.status=%s"; params.append(status)
            sql += " ORDER BY r.period_start DESC, s.name LIMIT %s"; params.append(limit)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_rosters: %s", e); return []

    def get_roster(self, cid: str, roster_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._ROSTER_SELECT + " WHERE r.id=%s AND r.company_id=%s", (roster_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_roster: %s", e); return None

    def create_roster(self, cid: str, site_id: str, period_start, period_end=None, created_by: str = "") -> Optional[dict]:
        """One roster per site per period start; returns the existing one when present."""
        ps = L.parse_date(period_start)
        if not ps or not site_id:
            return None
        pe = L.parse_date(period_end) or ps + timedelta(days=6)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM secops_rosters WHERE company_id=%s AND site_id=%s AND period_start=%s", (cid, site_id, ps))
                r = cur.fetchone()
                if r:
                    rid = r["id"]
                else:
                    rid = str(uuid.uuid4())
                    cur.execute("""INSERT INTO secops_rosters(id, company_id, site_id, period_start, period_end, status, created_by)
                                   VALUES (%s,%s,%s,%s,%s,'draft',%s)""", (rid, cid, site_id, ps, pe, created_by))
            return self.get_roster(cid, rid)
        except Exception as e:
            logger.error("secops create_roster: %s", e); return None

    def set_roster_status(self, cid: str, roster_id: str, status: str) -> bool:
        if status not in L.ROSTER_STATUSES:
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_rosters SET status=%s, updated_at=NOW(),
                               published_at = CASE WHEN %s='published' THEN NOW() ELSE published_at END
                               WHERE id=%s AND company_id=%s""", (status, status, roster_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops set_roster_status: %s", e); return False

    _ASSIGN_SELECT = """
        SELECT a.*, p.name AS post_name, p.site_id, s.name AS site_name, e.name AS guard_name, g.guard_no, g.grade,
               t.name AS shift_name, t.start_time, t.end_time, t.is_night, t.break_minutes, r.status AS roster_status
          FROM secops_assignments a
          JOIN secops_posts p ON p.id=a.post_id
          JOIN secops_sites s ON s.id=p.site_id
          JOIN secops_guards g ON g.id=a.guard_id
          LEFT JOIN employees e ON e.employee_id=g.employee_id AND e.company_id=g.company_id
          LEFT JOIN secops_shift_templates t ON t.id=a.shift_template_id
          JOIN secops_rosters r ON r.id=a.roster_id
    """

    def list_assignments(self, cid: str, roster_id: Optional[str] = None, guard_id: Optional[str] = None,
                         site_id: Optional[str] = None, date_from=None, date_to=None, day=None,
                         published_only: bool = False, include_cancelled: bool = False) -> List[dict]:
        try:
            sql = self._ASSIGN_SELECT + " WHERE a.company_id=%s"
            params: list = [cid]
            if roster_id:
                sql += " AND a.roster_id=%s"; params.append(roster_id)
            if guard_id:
                sql += " AND a.guard_id=%s"; params.append(guard_id)
            if site_id:
                sql += " AND p.site_id=%s"; params.append(site_id)
            if day:
                sql += " AND a.duty_date=%s"; params.append(L.parse_date(day))
            if date_from:
                sql += " AND a.duty_date>=%s"; params.append(L.parse_date(date_from))
            if date_to:
                sql += " AND a.duty_date<=%s"; params.append(L.parse_date(date_to))
            if published_only:
                sql += " AND r.status='published'"
            if not include_cancelled:
                sql += " AND a.status<>'cancelled'"
            sql += " ORDER BY a.duty_date, t.sort_order, p.name, e.name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_assignments: %s", e); return []

    def guard_assignments_between(self, cid: str, guard_id: str, start_at: datetime, end_at: datetime) -> List[dict]:
        """Any non-cancelled assignment of the guard overlapping [start_at, end_at) — any site."""
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._ASSIGN_SELECT + """ WHERE a.company_id=%s AND a.guard_id=%s AND a.status<>'cancelled'
                            AND a.start_at < %s AND a.end_at > %s""", (cid, guard_id, end_at, start_at))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops guard_assignments_between: %s", e); return []

    def add_assignment(self, cid: str, roster_id: str, data: dict) -> tuple[Optional[dict], str]:
        """Assign a guard to post × day × shift; refuses overlapping duties (same guard, any site)."""
        roster = self.get_roster(cid, roster_id)
        if not roster:
            return None, "Roster not found"
        if roster["status"] == "closed":
            return None, "Roster is closed"
        post = self.get_post(cid, data.get("post_id"))
        guard = self.get_guard(cid, data.get("guard_id"))
        duty = L.parse_date(data.get("duty_date"))
        if not post or not guard or not duty:
            return None, "Post, guard and date are required"
        if guard["status"] not in ("active",):
            return None, f"Guard is {guard['status'].replace('_', ' ')}"
        if not (roster["period_start"] <= duty <= roster["period_end"]):
            return None, "Date is outside the roster period"
        shifts = {s["id"]: s for s in self.list_shifts(cid)}
        shift = shifts.get(data.get("shift_template_id"))
        if not shift:
            return None, "Choose a shift"
        start_at, end_at = L.shift_bounds(duty, shift)
        existing = self.guard_assignments_between(cid, guard["id"], start_at, end_at)
        conflicts = L.find_conflicts({"guard_id": guard["id"], "start_at": start_at, "end_at": end_at}, existing)
        if conflicts:
            c = conflicts[0]
            return None, (f"Conflict: {guard.get('name') or guard['guard_no']} is already on {c.get('site_name')} / "
                          f"{c.get('post_name')} ({c.get('shift_name') or ''} {c['duty_date']})")
        if not L.grade_meets(guard.get("grade"), post.get("grade_required")):
            return None, f"Post requires grade {post.get('grade_required')}"
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""SELECT COUNT(*) AS c FROM secops_assignments WHERE roster_id=%s AND post_id=%s AND duty_date=%s
                               AND shift_template_id=%s AND status<>'cancelled'""", (roster_id, post["id"], duty, shift["id"]))
                if cur.fetchone()["c"] >= int(post.get("guards_required") or 1) and not _bool(data.get("is_relief")):
                    return None, "Post already has the required number of guards for this shift — tick relief to add an extra guard"
                cur.execute(
                    """INSERT INTO secops_assignments(id, company_id, roster_id, post_id, guard_id, shift_template_id, duty_date,
                           start_at, end_at, is_relief, status, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'scheduled',%s) RETURNING *""",
                    (str(uuid.uuid4()), cid, roster_id, post["id"], guard["id"], shift["id"], duty, start_at, end_at,
                     _bool(data.get("is_relief")), data.get("notes", "")))
                return dict(cur.fetchone()), ""
        except Exception as e:
            logger.error("secops add_assignment: %s", e); return None, str(e)

    def delete_assignment(self, cid: str, assignment_id: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_assignments a SET status='cancelled' FROM secops_rosters r
                               WHERE a.id=%s AND a.company_id=%s AND r.id=a.roster_id AND r.status<>'closed'""", (assignment_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops delete_assignment: %s", e); return False

    def auto_fill(self, cid: str, roster_id: str) -> dict:
        """Copy last period's assignments for the same site, shifted into this period."""
        roster = self.get_roster(cid, roster_id)
        if not roster:
            return {"added": 0, "skipped": 0, "error": "Roster not found"}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""SELECT * FROM secops_rosters WHERE company_id=%s AND site_id=%s AND period_start<%s
                               ORDER BY period_start DESC LIMIT 1""", (cid, roster["site_id"], roster["period_start"]))
                prev = cur.fetchone()
        except Exception as e:
            logger.error("secops auto_fill: %s", e); return {"added": 0, "skipped": 0, "error": str(e)}
        if not prev:
            return {"added": 0, "skipped": 0, "error": "No earlier roster for this site"}
        previous = self.list_assignments(cid, roster_id=prev["id"])
        candidates = L.auto_fill_from_previous(previous, roster["period_start"], roster["period_end"], prev["period_start"])
        added = skipped = 0
        for c in candidates:
            row, err = self.add_assignment(cid, roster_id, c)
            if row:
                added += 1
            else:
                skipped += 1
        return {"added": added, "skipped": skipped, "error": ""}

    def roster_grid(self, cid: str, roster: dict) -> dict:
        """Everything the builder page needs: posts, days, shifts, slots and the assignments per slot."""
        posts = self.list_posts(cid, roster["site_id"], active_only=True)
        shifts = self.list_shifts(cid, active_only=True)
        days = L.date_range(roster["period_start"], roster["period_end"])
        slots = L.expand_roster(posts, shifts, roster["period_start"], roster["period_end"])
        assignments = self.list_assignments(cid, roster_id=roster["id"])
        by_slot: dict[str, list] = {}
        for a in assignments:
            by_slot.setdefault(f"{a['post_id']}|{a['duty_date'].isoformat()}|{a['shift_template_id']}", []).append(a)
        gaps = L.coverage_gaps(slots, assignments)
        required = sum(s["guards_required"] for s in slots)
        return {"posts": posts, "shifts": shifts, "days": days, "slots": slots, "assignments": assignments,
                "by_slot": by_slot, "gaps": gaps, "required": required,
                "assigned": min(len(assignments), required) if required else len(assignments),
                "coverage_pct": L.coverage_pct(required, len(assignments)),
                "post_shifts": {p["id"]: [s["id"] for s in L.slots_for_post(p, shifts)] for p in posts}}

    # ══════════════════════════════════════════════════════════════
    #  Attendance
    # ══════════════════════════════════════════════════════════════
    _ATT_SELECT = """
        SELECT at.*, e.name AS guard_name, g.guard_no, s.name AS site_name, p.name AS post_name,
               t.name AS shift_name, t.start_time, t.end_time
          FROM secops_attendance at
          JOIN secops_guards g ON g.id=at.guard_id
          LEFT JOIN employees e ON e.employee_id=g.employee_id AND e.company_id=g.company_id
          LEFT JOIN secops_sites s ON s.id=at.site_id
          LEFT JOIN secops_posts p ON p.id=at.post_id
          LEFT JOIN secops_assignments a ON a.id=at.assignment_id
          LEFT JOIN secops_shift_templates t ON t.id=a.shift_template_id
    """

    def list_attendance(self, cid: str, date_from=None, date_to=None, site_id: Optional[str] = None,
                        guard_id: Optional[str] = None, status: Optional[str] = None, day=None, limit: int = 2000) -> List[dict]:
        try:
            sql = self._ATT_SELECT + " WHERE at.company_id=%s"
            params: list = [cid]
            if day:
                sql += " AND at.duty_date=%s"; params.append(L.parse_date(day))
            if date_from:
                sql += " AND at.duty_date>=%s"; params.append(L.parse_date(date_from))
            if date_to:
                sql += " AND at.duty_date<=%s"; params.append(L.parse_date(date_to))
            if site_id:
                sql += " AND at.site_id=%s"; params.append(site_id)
            if guard_id:
                sql += " AND at.guard_id=%s"; params.append(guard_id)
            if status:
                sql += " AND at.status=%s"; params.append(status)
            sql += " ORDER BY at.duty_date DESC, s.name, e.name LIMIT %s"; params.append(limit)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_attendance: %s", e); return []

    def get_attendance(self, cid: str, att_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._ATT_SELECT + " WHERE at.id=%s AND at.company_id=%s", (att_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_attendance: %s", e); return None

    def open_attendance(self, cid: str, guard_id: str) -> Optional[dict]:
        """The guard's checked-in-but-not-out row (last 2 days)."""
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._ATT_SELECT + """ WHERE at.company_id=%s AND at.guard_id=%s AND at.check_in_at IS NOT NULL
                            AND at.check_out_at IS NULL AND at.duty_date >= CURRENT_DATE - 2 ORDER BY at.check_in_at DESC LIMIT 1""",
                            (cid, guard_id))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops open_attendance: %s", e); return None

    def _hours_for(self, cid: str, check_in, check_out, shift: Optional[dict], guard: Optional[dict], duty_date, settings=None) -> dict:
        s = settings or self.get_settings(cid)
        return L.compute_hours(check_in, check_out, shift,
                               work_hours_per_day=(guard or {}).get("work_hours_per_day") or s.get("default_hours_per_day") or 8,
                               night_start=s.get("night_start"), night_end=s.get("night_end"),
                               grace_minutes=s.get("late_grace_minutes") or 0, duty_date=duty_date)

    def save_attendance(self, cid: str, data: dict, recorded_by: str = "", settings: Optional[dict] = None) -> Optional[dict]:
        """Insert or update one attendance row (by id, or by assignment_id, or by guard+date+site)."""
        guard = self.get_guard(cid, data.get("guard_id")) if data.get("guard_id") else None
        duty = L.parse_date(data.get("duty_date"))
        if not guard or not duty:
            return None
        status = data.get("status") if data.get("status") in L.ATTENDANCE_STATUSES else "present"
        ci, co = L.parse_datetime(data.get("check_in_at")), L.parse_datetime(data.get("check_out_at"))
        # time-only inputs from the sheet → combine with the duty date (check-out may roll past midnight)
        if ci is None and L.parse_time(data.get("check_in_at")):
            ci = datetime.combine(duty, L.parse_time(data.get("check_in_at")))
        if co is None and L.parse_time(data.get("check_out_at")):
            co = datetime.combine(duty, L.parse_time(data.get("check_out_at")))
            if ci and co <= ci:
                co += timedelta(days=1)
        shift = None
        if data.get("shift_template_id"):
            shift = next((s for s in self.list_shifts(cid) if s["id"] == data["shift_template_id"]), None)
        elif data.get("assignment_id"):
            a = next(iter(self.list_assignments(cid, include_cancelled=True, roster_id=None, guard_id=guard["id"], day=duty)), None)
            if a and a.get("start_time"):
                shift = {"start_time": a["start_time"], "end_time": a["end_time"], "break_minutes": a.get("break_minutes")}
        hrs = self._hours_for(cid, ci, co, shift, guard, duty, settings) if status in L.PRESENT_STATUSES else \
            {"hours_worked": 0, "night_hours": 0, "overtime_hours": 0, "late_minutes": 0}
        if status == "present" and hrs.get("late_minutes"):
            status = "late"
        try:
            with get_conn() as conn, conn.cursor() as cur:
                row = None
                if data.get("id"):
                    cur.execute("SELECT * FROM secops_attendance WHERE id=%s AND company_id=%s", (data["id"], cid))
                    row = cur.fetchone()
                if row is None and data.get("assignment_id"):
                    cur.execute("SELECT * FROM secops_attendance WHERE assignment_id=%s AND company_id=%s", (data["assignment_id"], cid))
                    row = cur.fetchone()
                if row is None:
                    cur.execute("""SELECT * FROM secops_attendance WHERE company_id=%s AND guard_id=%s AND duty_date=%s
                                   AND COALESCE(site_id,'')=COALESCE(%s,'') AND assignment_id IS NULL""",
                                (cid, guard["id"], duty, _opt(data.get("site_id"))))
                    row = cur.fetchone()
                params = (_opt(data.get("assignment_id")), _opt(data.get("site_id")) or guard.get("home_site_id"),
                          _opt(data.get("post_id")), ci, co, status, hrs["hours_worked"], hrs["night_hours"],
                          hrs["overtime_hours"], int(hrs["late_minutes"] or 0),
                          data.get("source") if data.get("source") in L.ATTENDANCE_SOURCES else "manual",
                          _opt(data.get("gps_lat")), _opt(data.get("gps_lng")), data.get("note", "") or "", recorded_by)
                if row:
                    cur.execute(
                        """UPDATE secops_attendance SET assignment_id=%s, site_id=%s, post_id=%s, check_in_at=%s, check_out_at=%s,
                               status=%s, hours_worked=%s, night_hours=%s, overtime_hours=%s, late_minutes=%s, source=%s,
                               gps_lat=COALESCE(%s, gps_lat), gps_lng=COALESCE(%s, gps_lng), note=%s, recorded_by=%s, updated_at=NOW()
                           WHERE id=%s RETURNING *""", params + (row["id"],))
                else:
                    cur.execute(
                        """INSERT INTO secops_attendance(id, company_id, guard_id, duty_date, assignment_id, site_id, post_id, check_in_at,
                               check_out_at, status, hours_worked, night_hours, overtime_hours, late_minutes, source, gps_lat, gps_lng, note, recorded_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), cid, guard["id"], duty) + params)
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops save_attendance: %s", e); return None

    def attendance_sheet(self, cid: str, site_id: str, day) -> List[dict]:
        """Scheduled assignments for a site/day merged with any attendance already recorded,
        plus unscheduled attendance (walk-in relief) for the same site/day."""
        d = L.parse_date(day) or date.today()
        assignments = self.list_assignments(cid, site_id=site_id, day=d)
        attendance = self.list_attendance(cid, site_id=site_id, day=d)
        by_assign = {a["assignment_id"]: a for a in attendance if a.get("assignment_id")}
        by_guard = {a["guard_id"]: a for a in attendance if not a.get("assignment_id")}
        rows = []
        for a in assignments:
            att = by_assign.get(a["id"]) or by_guard.pop(a["guard_id"], None)
            rows.append({"assignment": a, "attendance": att, "guard_id": a["guard_id"], "guard_name": a.get("guard_name"),
                         "guard_no": a.get("guard_no"), "post_name": a.get("post_name"), "post_id": a["post_id"],
                         "shift_name": a.get("shift_name"), "shift_template_id": a.get("shift_template_id"),
                         "start_time": a.get("start_time"), "end_time": a.get("end_time"),
                         "status": (att or {}).get("status") or "", "key": a["id"]})
        for att in by_guard.values():
            rows.append({"assignment": None, "attendance": att, "guard_id": att["guard_id"], "guard_name": att.get("guard_name"),
                         "guard_no": att.get("guard_no"), "post_name": att.get("post_name"), "post_id": att.get("post_id"),
                         "shift_name": "", "shift_template_id": None, "start_time": None, "end_time": None,
                         "status": att.get("status"), "key": att["id"]})
        return rows

    def mark_absent(self, cid: str, day) -> int:
        """Insert 'absent' rows for yesterday's scheduled assignments (published rosters) with no attendance."""
        d = L.parse_date(day)
        if not d:
            return 0
        n = 0
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""SELECT a.id, a.guard_id, a.post_id, p.site_id FROM secops_assignments a
                               JOIN secops_posts p ON p.id=a.post_id JOIN secops_rosters r ON r.id=a.roster_id
                               WHERE a.company_id=%s AND a.duty_date=%s AND a.status<>'cancelled' AND r.status='published'
                                 AND NOT EXISTS (SELECT 1 FROM secops_attendance at WHERE at.assignment_id=a.id)
                                 AND NOT EXISTS (SELECT 1 FROM secops_attendance at2 WHERE at2.company_id=a.company_id
                                                  AND at2.guard_id=a.guard_id AND at2.duty_date=a.duty_date AND at2.assignment_id IS NULL)""",
                            (cid, d))
                missing = [dict(r) for r in cur.fetchall()]
                for m in missing:
                    cur.execute("""INSERT INTO secops_attendance(id, company_id, assignment_id, guard_id, site_id, post_id, duty_date,
                                       status, source, note, recorded_by) VALUES (%s,%s,%s,%s,%s,%s,%s,'absent','system','No check-in recorded','scheduler')""",
                                (str(uuid.uuid4()), cid, m["id"], m["guard_id"], m["site_id"], m["post_id"], d))
                    n += 1
        except Exception as e:
            logger.error("secops mark_absent: %s", e)
        return n

    def record_checkin(self, cid: str, guard_id: Optional[str] = None, guard_no: Optional[str] = None,
                       employee_id: Optional[str] = None, action: str = "in", source: str = "self",
                       lat=None, lng=None, note: str = "", recorded_by: str = "", at: Optional[datetime] = None) -> dict:
        guard = None
        if guard_id:
            guard = self.get_guard(cid, guard_id)
        if not guard and guard_no:
            guard = self.guard_by_no(cid, guard_no)
        if not guard and employee_id:
            guard = self.guard_by_employee(cid, employee_id)
        if not guard:
            return {"ok": False, "error": "Guard not found", "attendance": None}
        if guard["status"] != "active":
            return {"ok": False, "error": f"Guard is {guard['status'].replace('_', ' ')}", "attendance": None}
        now = at or datetime.now()
        action = (action or "in").lower()
        src = source if source in L.ATTENDANCE_SOURCES else "self"
        if action in ("out", "checkout", "check_out"):
            open_row = self.open_attendance(cid, guard["id"])
            if not open_row:
                return {"ok": False, "error": "No open check-in to close", "attendance": None}
            shift = {"start_time": open_row.get("start_time"), "end_time": open_row.get("end_time")} if open_row.get("start_time") else None
            row = self.save_attendance(cid, {"id": open_row["id"], "guard_id": guard["id"], "duty_date": open_row["duty_date"],
                                             "assignment_id": open_row.get("assignment_id"), "site_id": open_row.get("site_id"),
                                             "post_id": open_row.get("post_id"), "check_in_at": open_row["check_in_at"],
                                             "check_out_at": now, "status": open_row["status"], "source": src,
                                             "gps_lat": lat, "gps_lng": lng, "note": note or open_row.get("note", ""),
                                             "shift_template_id": None}, recorded_by or guard.get("name", ""))
            if row and shift:  # recompute with the scheduled shift for night/late accuracy
                hrs = self._hours_for(cid, row["check_in_at"], row["check_out_at"], shift, guard, row["duty_date"])
                row = self._apply_hours(cid, row["id"], hrs) or row
            return {"ok": bool(row), "error": "" if row else "Could not save", "attendance": row}
        # check-in
        if self.open_attendance(cid, guard["id"]):
            return {"ok": False, "error": "Already checked in — check out first", "attendance": None}
        today = now.date()
        cands = [a for a in self.list_assignments(cid, guard_id=guard["id"], date_from=today - timedelta(days=1), date_to=today)
                 if a.get("end_at") and a["end_at"] + timedelta(hours=2) >= now]
        cands.sort(key=lambda a: abs((a["start_at"] - now).total_seconds()) if a.get("start_at") else 1e12)
        a = cands[0] if cands else None
        shift = {"start_time": a["start_time"], "end_time": a["end_time"], "break_minutes": a.get("break_minutes")} if a and a.get("start_time") else None
        row = self.save_attendance(cid, {"guard_id": guard["id"], "duty_date": a["duty_date"] if a else today,
                                         "assignment_id": a["id"] if a else None,
                                         "site_id": (a["site_id"] if a else guard.get("home_site_id")),
                                         "post_id": a["post_id"] if a else None, "check_in_at": now, "status": "present",
                                         "source": src, "gps_lat": lat, "gps_lng": lng, "note": note,
                                         "shift_template_id": a.get("shift_template_id") if a else None},
                                   recorded_by or guard.get("name", ""))
        if row and shift:
            hrs = self._hours_for(cid, now, None, shift, guard, row["duty_date"])
            if hrs.get("late_minutes"):
                row = self._apply_hours(cid, row["id"], hrs, status="late") or row
        return {"ok": bool(row), "error": "" if row else "Could not save", "attendance": row,
                "assignment": a, "unscheduled": a is None}

    def _apply_hours(self, cid: str, att_id: str, hrs: dict, status: Optional[str] = None) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_attendance SET hours_worked=%s, night_hours=%s, overtime_hours=%s, late_minutes=%s,
                               status=COALESCE(%s, status), updated_at=NOW() WHERE id=%s AND company_id=%s RETURNING *""",
                            (hrs["hours_worked"], hrs["night_hours"], hrs["overtime_hours"], int(hrs["late_minutes"] or 0),
                             status, att_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops _apply_hours: %s", e); return None

    # ══════════════════════════════════════════════════════════════
    #  Kit
    # ══════════════════════════════════════════════════════════════
    def list_kit_items(self, cid: str, active_only: bool = False) -> List[dict]:
        try:
            sql = """SELECT k.*, (SELECT COUNT(*) FROM secops_kit_issues i WHERE i.kit_item_id=k.id AND i.status='issued') AS outstanding
                     FROM secops_kit_items k WHERE k.company_id=%s"""
            if active_only:
                sql += " AND k.is_active=TRUE"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql + " ORDER BY k.kind, k.name", (cid,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_kit_items: %s", e); return []

    def get_kit_item(self, cid: str, item_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM secops_kit_items WHERE id=%s AND company_id=%s", (item_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_kit_item: %s", e); return None

    def create_kit_item(self, cid: str, data: dict) -> Optional[dict]:
        if not (data.get("name") or "").strip():
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""INSERT INTO secops_kit_items(id, company_id, name, kind, inventory_item_id, tracks_serial, sizes, is_active)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                            (str(uuid.uuid4()), cid, data["name"].strip(), data.get("kind") if data.get("kind") in L.KIT_KINDS else "other",
                             _opt(data.get("inventory_item_id")), _bool(data.get("tracks_serial")), data.get("sizes", ""),
                             _bool(data.get("is_active", True))))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops create_kit_item: %s", e); return None

    def update_kit_item(self, cid: str, item_id: str, data: dict) -> bool:
        if not (data.get("name") or "").strip():
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_kit_items SET name=%s, kind=%s, inventory_item_id=%s, tracks_serial=%s, sizes=%s, is_active=%s
                               WHERE id=%s AND company_id=%s""",
                            (data["name"].strip(), data.get("kind") if data.get("kind") in L.KIT_KINDS else "other",
                             _opt(data.get("inventory_item_id")), _bool(data.get("tracks_serial")), data.get("sizes", ""),
                             _bool(data.get("is_active")), item_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_kit_item: %s", e); return False

    _KIT_SELECT = """
        SELECT i.*, k.name AS item_name, k.kind, k.inventory_item_id, e.name AS guard_name, g.guard_no, s.name AS site_name
          FROM secops_kit_issues i JOIN secops_kit_items k ON k.id=i.kit_item_id
          LEFT JOIN secops_guards g ON g.id=i.guard_id
          LEFT JOIN employees e ON e.employee_id=g.employee_id AND e.company_id=g.company_id
          LEFT JOIN secops_sites s ON s.id=i.site_id
    """

    def list_kit_issues(self, cid: str, guard_id: Optional[str] = None, site_id: Optional[str] = None,
                        status: Optional[str] = None, client_owned: Optional[bool] = None, limit: int = 1000) -> List[dict]:
        try:
            sql = self._KIT_SELECT + " WHERE i.company_id=%s"
            params: list = [cid]
            if guard_id:
                sql += " AND i.guard_id=%s"; params.append(guard_id)
            if site_id:
                sql += " AND i.site_id=%s"; params.append(site_id)
            if status:
                sql += " AND i.status=%s"; params.append(status)
            if client_owned is not None:
                sql += " AND i.client_owned=%s"; params.append(client_owned)
            sql += " ORDER BY i.status='issued' DESC, i.issued_at DESC LIMIT %s"; params.append(limit)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_kit_issues: %s", e); return []

    def get_kit_issue(self, cid: str, issue_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._KIT_SELECT + " WHERE i.id=%s AND i.company_id=%s", (issue_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_kit_issue: %s", e); return None

    def outstanding_kit(self, cid: str, guard_id: Optional[str] = None, site_id: Optional[str] = None) -> List[dict]:
        return self.list_kit_issues(cid, guard_id=guard_id, site_id=site_id, status="issued")

    def _post_inventory_movement(self, cid: str, item: dict, qty, movement_type: str, reference: str, by: str, target: str) -> bool:
        """Mirror a kit issue/return into inventory when the kit item is linked to an inventory item."""
        inv_id = item.get("inventory_item_id")
        if not inv_id:
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                if not _table_exists(cur, "inventory_movements"):
                    return False
                cols = _table_columns(cur, "inventory_movements")
                if not {"item_id", "movement_type", "quantity"} <= cols:
                    return False
            from inventory_data_store import inventory_store
            return bool(inventory_store.record_movement({
                "company_id": cid, "item_id": inv_id, "movement_type": movement_type, "quantity": float(_D(qty) or 1),
                "reference_number": reference, "reason": f"Security kit {movement_type}: {item.get('name')} → {target}",
                "approved_by": by, "to_location" if movement_type == "issue" else "from_location": target}))
        except Exception as e:
            logger.warning("secops inventory movement skipped: %s", e)
            return False

    def issue_kit(self, cid: str, data: dict, issued_by: str = "") -> tuple[Optional[dict], str]:
        item = self.get_kit_item(cid, data.get("kit_item_id"))
        if not item:
            return None, "Choose a kit item"
        guard_id, site_id = _opt(data.get("guard_id")), _opt(data.get("site_id"))
        if not guard_id and not site_id:
            return None, "Issue to a guard or to a site"
        if item.get("tracks_serial") and not (data.get("serial_no") or "").strip():
            return None, "Serial number is required for this item"
        qty = _D(data.get("qty")) or Decimal(1)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secops_kit_issues(id, company_id, kit_item_id, guard_id, site_id, serial_no, size, qty, issued_at, issued_by,
                           expected_return, condition_out, status, client_owned, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'issued',%s,%s) RETURNING *""",
                    (str(uuid.uuid4()), cid, item["id"], guard_id, site_id, (data.get("serial_no") or "").strip(),
                     data.get("size", ""), qty, L.parse_datetime(data.get("issued_at")) or datetime.now(), issued_by,
                     _opt(data.get("expected_return")), data.get("condition_out") or "good", _bool(data.get("client_owned")),
                     data.get("notes", "")))
                row = dict(cur.fetchone())
        except Exception as e:
            logger.error("secops issue_kit: %s", e); return None, str(e)
        target = data.get("target_label") or (guard_id and f"guard {guard_id[:8]}") or f"site {site_id[:8]}"
        if not _bool(data.get("client_owned")):
            row["inventory_posted"] = self._post_inventory_movement(cid, item, qty, "issue", f"KIT-{row['id'][:8]}", issued_by, target)
        return row, ""

    def return_kit(self, cid: str, issue_id: str, condition_in: str = "good", status: str = "returned",
                   by: str = "", notes: str = "") -> bool:
        if status not in ("returned", "lost", "damaged"):
            return False
        issue = self.get_kit_issue(cid, issue_id)
        if not issue or issue["status"] != "issued":
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_kit_issues SET returned_at=NOW(), condition_in=%s, status=%s,
                               notes = CASE WHEN %s<>'' THEN notes || CASE WHEN notes<>'' THEN E'\\n' ELSE '' END || %s ELSE notes END
                               WHERE id=%s AND company_id=%s""", (condition_in or "", status, notes or "", notes or "", issue_id, cid))
        except Exception as e:
            logger.error("secops return_kit: %s", e); return False
        if status == "returned" and not issue.get("client_owned"):
            self._post_inventory_movement(cid, {"inventory_item_id": issue.get("inventory_item_id"), "name": issue.get("item_name")},
                                          issue.get("qty"), "return", f"KIT-{issue_id[:8]}", by,
                                          issue.get("guard_name") or issue.get("site_name") or "")
        return True

    # ══════════════════════════════════════════════════════════════
    #  Incidents
    # ══════════════════════════════════════════════════════════════
    _INC_SELECT = """
        SELECT i.*, s.name AS site_name, c.name AS client_name, c.email AS client_email, p.name AS post_name,
               e.name AS guard_name, g.guard_no
          FROM secops_incidents i JOIN secops_sites s ON s.id=i.site_id
          LEFT JOIN secops_clients c ON c.id=s.client_id
          LEFT JOIN secops_posts p ON p.id=i.post_id
          LEFT JOIN secops_guards g ON g.id=i.guard_id
          LEFT JOIN employees e ON e.employee_id=g.employee_id AND e.company_id=g.company_id
    """

    def list_incidents(self, cid: str, status: Optional[str] = None, site_id: Optional[str] = None, severity: Optional[str] = None,
                       category: Optional[str] = None, date_from=None, date_to=None, limit: int = 500) -> List[dict]:
        try:
            sql = self._INC_SELECT + " WHERE i.company_id=%s"
            params: list = [cid]
            for col, val in (("status", status), ("site_id", site_id), ("severity", severity), ("category", category)):
                if val:
                    sql += f" AND i.{col}=%s"; params.append(val)
            if date_from:
                sql += " AND i.occurred_at>=%s"; params.append(L.parse_date(date_from))
            if date_to:
                sql += " AND i.occurred_at<%s"; params.append(L.parse_date(date_to) + timedelta(days=1))
            sql += " ORDER BY i.occurred_at DESC LIMIT %s"; params.append(limit)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_incidents: %s", e); return []

    def get_incident(self, cid: str, incident_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(self._INC_SELECT + " WHERE i.id=%s AND i.company_id=%s", (incident_id, cid))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("secops get_incident: %s", e); return None

    def create_incident(self, cid: str, data: dict, reported_by: str = "") -> Optional[dict]:
        if not data.get("site_id") or not (data.get("description") or "").strip():
            return None
        occurred = L.parse_datetime(data.get("occurred_at")) or datetime.now()
        try:
            with get_conn() as conn, conn.cursor() as cur:
                no = L.format_incident_no(occurred.year, self._next_no(cur, cid, "incident", occurred.year))
                cur.execute(
                    """INSERT INTO secops_incidents(id, company_id, incident_no, site_id, post_id, guard_id, occurred_at, category, severity,
                           description, actions_taken, reported_by, reported_to_police, police_ref, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open') RETURNING *""",
                    (str(uuid.uuid4()), cid, no, data["site_id"], _opt(data.get("post_id")), _opt(data.get("guard_id")), occurred,
                     data.get("category") if data.get("category") in L.INCIDENT_CATEGORIES else "other",
                     data.get("severity") if data.get("severity") in L.SEVERITIES else "low",
                     data["description"].strip(), data.get("actions_taken", ""), data.get("reported_by") or reported_by,
                     _bool(data.get("reported_to_police")), data.get("police_ref", "")))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("secops create_incident: %s", e); return None

    def update_incident(self, cid: str, incident_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE secops_incidents SET site_id=%s, post_id=%s, guard_id=%s, occurred_at=%s, category=%s, severity=%s, description=%s,
                           actions_taken=%s, reported_by=%s, reported_to_police=%s, police_ref=%s, updated_at=NOW()
                       WHERE id=%s AND company_id=%s""",
                    (data["site_id"], _opt(data.get("post_id")), _opt(data.get("guard_id")),
                     L.parse_datetime(data.get("occurred_at")) or datetime.now(),
                     data.get("category") if data.get("category") in L.INCIDENT_CATEGORIES else "other",
                     data.get("severity") if data.get("severity") in L.SEVERITIES else "low",
                     (data.get("description") or "").strip(), data.get("actions_taken", ""), data.get("reported_by", ""),
                     _bool(data.get("reported_to_police")), data.get("police_ref", ""), incident_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops update_incident: %s", e); return False

    def set_incident_status(self, cid: str, incident_id: str, status: str) -> bool:
        if status not in L.INCIDENT_STATUSES:
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_incidents SET status=%s, closed_at = CASE WHEN %s='closed' THEN NOW() ELSE NULL END,
                               updated_at=NOW() WHERE id=%s AND company_id=%s""", (status, status, incident_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops set_incident_status: %s", e); return False

    def note_client_notified(self, cid: str, incident_id: str, note: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE secops_incidents SET client_notified_at=NOW(), client_notification_note=%s, updated_at=NOW()
                               WHERE id=%s AND company_id=%s""", (note or "", incident_id, cid))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("secops note_client_notified: %s", e); return False

    def open_incident_count(self, cid: str) -> int:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM secops_incidents WHERE company_id=%s AND status<>'closed'", (cid,))
                return int(cur.fetchone()["c"])
        except Exception as e:
            logger.error("secops open_incident_count: %s", e); return 0

    # ══════════════════════════════════════════════════════════════
    #  Payroll feed
    # ══════════════════════════════════════════════════════════════
    def list_feed(self, cid: str, period: str) -> List[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""SELECT f.*, e.name, g.guard_no, e.basic_salary FROM secops_payroll_feed f
                               JOIN secops_guards g ON g.id=f.guard_id
                               LEFT JOIN employees e ON e.employee_id=f.employee_id AND e.company_id=f.company_id
                               WHERE f.company_id=%s AND f.period=%s ORDER BY e.name""", (cid, period))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops list_feed: %s", e); return []

    def feed_periods(self, cid: str) -> List[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""SELECT period, COUNT(*) AS guards, SUM(overtime_pay) AS overtime_pay, SUM(night_allowance) AS night_allowance,
                               MIN(status) AS status, MAX(generated_at) AS generated_at FROM secops_payroll_feed
                               WHERE company_id=%s GROUP BY period ORDER BY period DESC LIMIT 24""", (cid,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("secops feed_periods: %s", e); return []

    def generate_feed(self, cid: str, period: str, force: bool = False) -> dict:
        try:
            start, end = L.parse_month(period)
        except Exception:
            return {"rows": 0, "error": "Period must be YYYY-MM"}
        existing = self.list_feed(cid, period)
        if any(r["status"] == "exported" for r in existing) and not force:
            return {"rows": len(existing), "error": "Feed already exported — regenerate with force to overwrite"}
        settings = self.get_settings(cid)
        holidays = self.list_holidays(cid, start.year)
        attendance = self.list_attendance(cid, date_from=start, date_to=end, limit=100000)
        by_guard: dict[str, list] = {}
        for a in attendance:
            by_guard.setdefault(a["guard_id"], []).append(a)
        guards = {g["id"]: g for g in self.list_guards(cid)}
        rows = []
        for gid, atts in by_guard.items():
            g = guards.get(gid)
            if not g:
                continue
            rows.append(L.payroll_feed_row(atts, g, settings, holidays))
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM secops_payroll_feed WHERE company_id=%s AND period=%s", (cid, period))
                for r in rows:
                    cur.execute(
                        """INSERT INTO secops_payroll_feed(id, company_id, period, guard_id, employee_id, days_present, days_absent, hours_worked,
                               night_hours, overtime_hours, night_allowance, overtime_pay, hourly_rate, status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft')""",
                        (str(uuid.uuid4()), cid, period, r["guard_id"], r["employee_id"], r["days_present"], r["days_absent"],
                         r["hours_worked"], r["night_hours"], r["overtime_hours"], r["night_allowance"], r["overtime_pay"], r["hourly_rate"]))
            return {"rows": len(rows), "error": ""}
        except Exception as e:
            logger.error("secops generate_feed: %s", e); return {"rows": 0, "error": str(e)}

    def mark_feed_exported(self, cid: str, period: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE secops_payroll_feed SET status='exported' WHERE company_id=%s AND period=%s", (cid, period))
                return True
        except Exception as e:
            logger.error("secops mark_feed_exported: %s", e); return False

    def push_to_payroll(self, cid: str, period: str) -> dict:
        """Write overtime / night allowance lines into the payroll module when it exposes a
        per-employee allowance table with an amount column. Otherwise the Excel export is the hand-off."""
        rows = self.list_feed(cid, period)
        if not rows:
            return {"pushed": 0, "error": "Generate the feed first"}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                if not _table_exists(cur, "employee_allowances"):
                    return {"pushed": 0, "error": "Payroll has no employee allowance table — use the Excel export"}
                cols = _table_columns(cur, "employee_allowances")
                if not {"employee_id", "allowance_name"} <= cols or "amount" not in cols:
                    return {"pushed": 0, "error": "Payroll allowance table has no amount column — use the Excel export"}
                has_cid = "company_id" in cols
                n = 0
                for r in rows:
                    for label, amount in ((f"Overtime {period}", r["overtime_pay"]), (f"Night allowance {period}", r["night_allowance"])):
                        if not amount:
                            continue
                        if has_cid:
                            cur.execute("DELETE FROM employee_allowances WHERE employee_id=%s AND allowance_name=%s AND company_id=%s",
                                        (r["employee_id"], label, cid))
                            cur.execute("INSERT INTO employee_allowances(employee_id, allowance_name, amount, company_id) VALUES (%s,%s,%s,%s)",
                                        (r["employee_id"], label, amount, cid))
                        else:
                            cur.execute("DELETE FROM employee_allowances WHERE employee_id=%s AND allowance_name=%s", (r["employee_id"], label))
                            cur.execute("INSERT INTO employee_allowances(employee_id, allowance_name, amount) VALUES (%s,%s,%s)",
                                        (r["employee_id"], label, amount))
                        n += 1
            self.mark_feed_exported(cid, period)
            return {"pushed": n, "error": ""}
        except Exception as e:
            logger.warning("secops push_to_payroll: %s", e)
            return {"pushed": 0, "error": f"Payroll push failed ({e}) — use the Excel export"}

    # ══════════════════════════════════════════════════════════════
    #  Dashboard / coverage
    # ══════════════════════════════════════════════════════════════
    def coverage_summary(self, cid: str, day=None) -> dict:
        d = L.parse_date(day) or date.today()
        sites = [s for s in self.list_sites(cid, status="active")]
        posts = self.list_posts(cid, active_only=True)
        shifts = self.list_shifts(cid, active_only=True)
        assignments = self.list_assignments(cid, day=d)
        attendance = self.list_attendance(cid, day=d)
        rosters = {r["site_id"]: r for r in self.list_rosters(cid, limit=1000) if r["period_start"] <= d <= r["period_end"]}
        slots = L.expand_roster(posts, shifts, d, d)
        out = {"day": d, "sites": [], "sites_total": len(sites), "sites_covered": 0, "posts_uncovered": 0,
               "guards_required": 0, "guards_assigned": 0, "guards_present": 0, "absent": 0, "late": 0, "on_duty_now": 0}
        now = datetime.now()
        for s in sites:
            s_slots = [x for x in slots if x["site_id"] == s["id"]]
            s_assign = [a for a in assignments if a["site_id"] == s["id"]]
            s_att = [a for a in attendance if a.get("site_id") == s["id"]]
            required = sum(x["guards_required"] for x in s_slots)
            assigned = len(s_assign)
            present = sum(1 for a in s_att if a["status"] in L.PRESENT_STATUSES)
            gaps = L.coverage_gaps(s_slots, s_assign)
            uncovered = sum(g["gap"] for g in gaps)
            row = {"site_id": s["id"], "site_name": s["name"], "client_name": s.get("client_name"), "required": required,
                   "assigned": assigned, "present": present, "uncovered": uncovered, "gaps": gaps,
                   "coverage_pct": L.coverage_pct(required, assigned),
                   "roster_id": rosters.get(s["id"], {}).get("id"), "roster_status": rosters.get(s["id"], {}).get("status"),
                   "absent": sum(1 for a in s_att if a["status"] == "absent"),
                   "late": sum(1 for a in s_att if a["status"] == "late")}
            out["sites"].append(row)
            out["guards_required"] += required
            out["guards_assigned"] += assigned
            out["guards_present"] += present
            out["posts_uncovered"] += uncovered
            out["absent"] += row["absent"]
            out["late"] += row["late"]
            if required and not uncovered:
                out["sites_covered"] += 1
        out["on_duty_now"] = sum(1 for a in attendance if a.get("check_in_at") and not a.get("check_out_at")
                                 and a["status"] in L.PRESENT_STATUSES) if d == now.date() else 0
        out["sites"].sort(key=lambda r: (-r["uncovered"], r["site_name"]))
        return out

    def dashboard_stats(self, cid: str, day=None) -> dict:
        d = L.parse_date(day) or date.today()
        cov = self.coverage_summary(cid, d)
        guards = self.list_guards(cid)
        active_guards = [g for g in guards if g["status"] == "active"]
        expiring = [g for g in guards if g["status"] != "exited" and g["expiring"]]
        incidents = self.list_incidents(cid, limit=10)
        return {"coverage": cov, "guards_total": len(active_guards), "guards_on_leave": sum(1 for g in guards if g["status"] == "on_leave"),
                "expiring": expiring[:10], "expiring_count": len(expiring),
                "open_incidents": self.open_incident_count(cid), "recent_incidents": incidents[:6],
                "kit_outstanding": len(self.outstanding_kit(cid)),
                "draft_rosters": [r for r in self.list_rosters(cid, status="draft", limit=10)]}

    def coverage_gap_report(self, cid: str, date_from, date_to, site_id: Optional[str] = None) -> List[dict]:
        start = L.parse_date(date_from) or date.today()
        end = L.parse_date(date_to) or start
        if (end - start).days > 92:
            end = start + timedelta(days=92)
        posts = self.list_posts(cid, site_id=site_id, active_only=True)
        sites = {s["id"]: s for s in self.list_sites(cid)}
        posts = [p for p in posts if sites.get(p["site_id"], {}).get("status") == "active"]
        shifts = self.list_shifts(cid, active_only=True)
        slots = L.expand_roster(posts, shifts, start, end)
        assignments = self.list_assignments(cid, site_id=site_id, date_from=start, date_to=end)
        gaps = L.coverage_gaps(slots, assignments)
        for g in gaps:
            g["site_name"] = sites.get(g["site_id"], {}).get("name", "")
            g["client_name"] = sites.get(g["site_id"], {}).get("client_name", "")
        return gaps

    # ══════════════════════════════════════════════════════════════
    #  Reports (rows for HTML + Excel)
    # ══════════════════════════════════════════════════════════════
    def report_coverage(self, cid: str, date_from, date_to, site_id=None, client_id=None) -> List[dict]:
        start = L.parse_date(date_from) or date.today()
        end = L.parse_date(date_to) or start
        if (end - start).days > 92:
            end = start + timedelta(days=92)
        sites = {s["id"]: s for s in self.list_sites(cid, status="active", client_id=client_id)}
        posts = [p for p in self.list_posts(cid, site_id=site_id, active_only=True) if p["site_id"] in sites]
        shifts = self.list_shifts(cid, active_only=True)
        slots = L.expand_roster(posts, shifts, start, end)
        assignments = self.list_assignments(cid, site_id=site_id, date_from=start, date_to=end)
        attendance = self.list_attendance(cid, site_id=site_id, date_from=start, date_to=end, limit=100000)
        agg: dict[tuple, dict] = {}
        for s in slots:
            k = (s["site_id"], s["duty_date"])
            r = agg.setdefault(k, {"site_name": sites[s["site_id"]]["name"], "client_name": sites[s["site_id"]].get("client_name"),
                                   "duty_date": s["duty_date"], "required": 0, "assigned": 0, "present": 0, "absent": 0})
            r["required"] += s["guards_required"]
        for a in assignments:
            k = (a["site_id"], a["duty_date"])
            if k in agg:
                agg[k]["assigned"] += 1
        for a in attendance:
            k = (a.get("site_id"), a["duty_date"])
            if k in agg:
                if a["status"] in L.PRESENT_STATUSES:
                    agg[k]["present"] += 1
                elif a["status"] == "absent":
                    agg[k]["absent"] += 1
        rows = sorted(agg.values(), key=lambda r: (r["duty_date"], r["site_name"]))
        for r in rows:
            r["uncovered"] = max(0, r["required"] - r["assigned"])
            r["coverage_pct"] = L.coverage_pct(r["required"], r["assigned"])
        return rows

    def report_attendance_register(self, cid: str, date_from, date_to, site_id=None, guard_id=None) -> List[dict]:
        rows = self.list_attendance(cid, date_from=date_from, date_to=date_to, site_id=site_id, guard_id=guard_id, limit=100000)
        rows.sort(key=lambda r: (r.get("site_name") or "", r["duty_date"], r.get("guard_name") or ""))
        return [{"site_name": r.get("site_name"), "duty_date": r["duty_date"], "guard_no": r.get("guard_no"), "guard_name": r.get("guard_name"),
                 "post_name": r.get("post_name"), "shift_name": r.get("shift_name"), "status": r["status"],
                 "check_in_at": r.get("check_in_at"), "check_out_at": r.get("check_out_at"),
                 "hours_worked": r["hours_worked"], "night_hours": r["night_hours"], "overtime_hours": r["overtime_hours"],
                 "late_minutes": r.get("late_minutes") or 0, "source": r.get("source")} for r in rows]

    def report_absenteeism(self, cid: str, date_from, date_to, site_id=None, guard_id=None) -> List[dict]:
        rows = self.list_attendance(cid, date_from=date_from, date_to=date_to, site_id=site_id, guard_id=guard_id, limit=100000)
        agg: dict[str, dict] = {}
        for r in rows:
            g = agg.setdefault(r["guard_id"], {"guard_no": r.get("guard_no"), "guard_name": r.get("guard_name"), "scheduled": 0,
                                               "present": 0, "late": 0, "absent": 0, "leave": 0, "late_minutes": 0})
            g["scheduled"] += 1
            if r["status"] in L.PRESENT_STATUSES:
                g["present"] += 1
            if r["status"] == "late":
                g["late"] += 1
            if r["status"] == "absent":
                g["absent"] += 1
            if r["status"] == "leave":
                g["leave"] += 1
            g["late_minutes"] += int(r.get("late_minutes") or 0)
        out = []
        for g in agg.values():
            g["absence_pct"] = L.q2(Decimal(g["absent"]) * 100 / Decimal(g["scheduled"])) if g["scheduled"] else Decimal(0)
            out.append(g)
        return sorted(out, key=lambda r: (-r["absent"], -r["late"], r["guard_name"] or ""))

    def report_hours(self, cid: str, date_from, date_to, site_id=None, guard_id=None) -> List[dict]:
        rows = self.list_attendance(cid, date_from=date_from, date_to=date_to, site_id=site_id, guard_id=guard_id, limit=100000)
        settings = self.get_settings(cid)
        start = L.parse_date(date_from) or date.today()
        holidays = self.list_holidays(cid, start.year)
        guards = {g["id"]: g for g in self.list_guards(cid)}
        by_guard: dict[str, list] = {}
        for r in rows:
            by_guard.setdefault(r["guard_id"], []).append(r)
        out = []
        for gid, atts in by_guard.items():
            g = guards.get(gid)
            if not g:
                continue
            f = L.payroll_feed_row(atts, g, settings, holidays)
            f["guard_no"] = g.get("guard_no")
            out.append(f)
        return sorted(out, key=lambda r: (-r["hours_worked"], r["name"] or ""))

    def report_kit_outstanding(self, cid: str, guard_id=None, site_id=None, client_owned: Optional[bool] = None) -> List[dict]:
        rows = self.outstanding_kit(cid, guard_id=guard_id, site_id=site_id)
        if client_owned is not None:
            rows = [r for r in rows if bool(r.get("client_owned")) == client_owned]
        today = date.today()
        return [{"holder": r.get("guard_name") or r.get("site_name") or "", "holder_type": "guard" if r.get("guard_id") else "site",
                 "guard_no": r.get("guard_no"), "site_name": r.get("site_name"), "item_name": r.get("item_name"), "kind": r.get("kind"),
                 "serial_no": r.get("serial_no"), "size": r.get("size"), "qty": r.get("qty"), "issued_at": r.get("issued_at"),
                 "expected_return": r.get("expected_return"), "overdue": bool(r.get("expected_return") and r["expected_return"] < today),
                 "client_owned": "yes" if r.get("client_owned") else "no", "condition_out": r.get("condition_out")} for r in rows]

    def report_licence_expiry(self, cid: str, days: int = 90) -> List[dict]:
        today = date.today()
        out = []
        for g in self.list_guards(cid):
            if g["status"] == "exited":
                continue
            for field, label in L.DOCUMENT_FIELDS:
                exp = L.parse_date(g.get(field))
                if exp is None:
                    if field == "licence_expiry":
                        out.append({"guard_no": g["guard_no"], "guard_name": g.get("name"), "grade": g["grade"], "document": label,
                                    "expiry": None, "days": None, "status": "missing", "home_site": g.get("home_site_name")})
                    continue
                st = L.licence_status(exp, today, days)
                if st in ("expired", "expiring"):
                    out.append({"guard_no": g["guard_no"], "guard_name": g.get("name"), "grade": g["grade"], "document": label,
                                "expiry": exp, "days": (exp - today).days, "status": st, "home_site": g.get("home_site_name")})
        return sorted(out, key=lambda r: (r["days"] if r["days"] is not None else 9999))

    def report_incidents(self, cid: str, date_from, date_to, site_id=None, category=None, severity=None) -> List[dict]:
        rows = self.list_incidents(cid, site_id=site_id, category=category, severity=severity, date_from=date_from, date_to=date_to, limit=5000)
        return [{"incident_no": r["incident_no"], "occurred_at": r["occurred_at"], "site_name": r.get("site_name"), "client_name": r.get("client_name"),
                 "category": r["category"], "severity": r["severity"], "status": r["status"], "guard_name": r.get("guard_name"),
                 "reported_to_police": "yes" if r.get("reported_to_police") else "no", "police_ref": r.get("police_ref"),
                 "client_notified": "yes" if r.get("client_notified_at") else "no", "description": (r.get("description") or "")[:120]} for r in rows]

    def report_incident_summary(self, cid: str, date_from, date_to, site_id=None) -> List[dict]:
        rows = self.list_incidents(cid, site_id=site_id, date_from=date_from, date_to=date_to, limit=5000)
        agg: dict[tuple, dict] = {}
        for r in rows:
            k = (r.get("site_name") or "", r["category"])
            a = agg.setdefault(k, {"site_name": k[0], "category": r["category"], "total": 0, "low": 0, "medium": 0, "high": 0, "critical": 0, "open": 0})
            a["total"] += 1
            a[r["severity"]] = a.get(r["severity"], 0) + 1
            if r["status"] != "closed":
                a["open"] += 1
        return sorted(agg.values(), key=lambda r: (r["site_name"], -r["total"]))

    def report_billing(self, cid: str, date_from, date_to, client_id=None, site_id=None) -> List[dict]:
        """Billable guard-hours (present/late/relief attendance) × client rate per site per month."""
        rows = self.list_attendance(cid, date_from=date_from, date_to=date_to, site_id=site_id, limit=100000)
        sites = {s["id"]: s for s in self.list_sites(cid, client_id=client_id)}
        agg: dict[tuple, dict] = {}
        for r in rows:
            s = sites.get(r.get("site_id"))
            if not s or r["status"] not in L.PRESENT_STATUSES:
                continue
            k = (s["id"], r["duty_date"].strftime("%Y-%m"))
            a = agg.setdefault(k, {"client_name": s.get("client_name") or "", "site_name": s["name"], "period": k[1],
                                   "guard_days": 0, "hours": Decimal(0), "night_hours": Decimal(0), "overtime_hours": Decimal(0),
                                   "rate": _D(s.get("billing_rate_per_guard_hour")), "amount": Decimal(0)})
            a["guard_days"] += 1
            a["hours"] += _D(r["hours_worked"])
            a["night_hours"] += _D(r["night_hours"])
            a["overtime_hours"] += _D(r["overtime_hours"])
        out = []
        for a in agg.values():
            a["amount"] = L.billable_amount(a["hours"], a["rate"])
            for k in ("hours", "night_hours", "overtime_hours"):
                a[k] = L.q2(a[k])
            out.append(a)
        return sorted(out, key=lambda r: (r["client_name"], r["site_name"], r["period"]))

    # ══════════════════════════════════════════════════════════════
    #  Misc
    # ══════════════════════════════════════════════════════════════
    def companies(self) -> List[str]:
        ids: set = set()
        try:
            with get_conn() as conn, conn.cursor() as cur:
                for t in ("secops_sites", "secops_guards"):
                    cur.execute(f"SELECT DISTINCT company_id FROM {t}")
                    ids |= {r["company_id"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("secops companies: %s", e)
        return sorted(ids)

    def guard_for_user(self, cid: str, username: str = "", email: str = "", full_name: str = "") -> Optional[dict]:
        emp = None
        try:
            from hrm_data_store import hrm_store
            emp = hrm_store.get_employee_for_user(cid, username=username or "", email=email or "")
            if not emp and full_name:
                emp = hrm_store.get_employee_for_user(cid, username=full_name)
        except Exception as e:
            logger.warning("secops guard_for_user: hrm lookup failed: %s", e)
        if not emp:
            return None
        return self.guard_by_employee(cid, emp["employee_id"])

    def guard_duties(self, cid: str, guard_id: str, days_ahead: int = 7) -> List[dict]:
        today = date.today()
        return self.list_assignments(cid, guard_id=guard_id, date_from=today, date_to=today + timedelta(days=days_ahead), published_only=True)


secops_store = SecOpsDataStore()


# ── Public module-level API ───────────────────────────────────────
def guard_for_user(company_id: str, username: str = "", email: str = "", full_name: str = "") -> Optional[dict]:
    return secops_store.guard_for_user(company_id, username, email, full_name)


def record_checkin(company_id: str, guard_id: Optional[str] = None, guard_no: Optional[str] = None,
                   employee_id: Optional[str] = None, action: str = "in", source: str = "self",
                   lat=None, lng=None, note: str = "", recorded_by: str = "") -> dict:
    return secops_store.record_checkin(company_id, guard_id=guard_id, guard_no=guard_no, employee_id=employee_id,
                                       action=action, source=source, lat=lat, lng=lng, note=note, recorded_by=recorded_by)


def coverage_summary(company_id: str, day=None) -> dict:
    return secops_store.coverage_summary(company_id, day)


def open_incident_count(company_id: str) -> int:
    return secops_store.open_incident_count(company_id)
