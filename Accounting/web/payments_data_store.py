"""
Mobile-money payments data store — PostgreSQL backend.

Tables: payment_accounts, payments, payment_statement_imports,
        payment_provider_settings, payment_inbound_processed

Public Python API (importable by other modules):
    record_payment(company_id, **fields)      -> dict | None  (dict['_duplicate'] when already stored)
    find_matches(company_id, payment)         -> [candidate dicts with score/reasons]
    link_payment(payment_id, matched_type, matched_id, by, company_id=None) -> bool

Reconciliation matches payments against the VAT module's income
(``vat_income``) and expense (``vat_expenses``) records. Those tables have
drifted across deployments (income_date / tender_id / payment_mode are
optional), so every query here checks information_schema first.

Inbound provider notifications are received by the generic
``/webhooks/inbound/{source}`` endpoint (separate module) and logged into
``webhook_inbound_log``; ``process_inbound_notifications()`` reads that
table IF it exists, parses rows via payment_providers and creates payments.
Processed rows are tracked in our own ``payment_inbound_processed`` table so
we never depend on the exact columns of the log table.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db import get_conn, get_cursor
from payment_providers import (
    DIRECTIONS, NOTIFYING_PROVIDERS, PROVIDERS, STATUSES, NormalizedPayment,
    amount_window, date_window, get_adapter, normalize_msisdn,
    parse_amount, parse_datetime, pick_auto_match, rank_candidates,
)

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_accounts (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    provider        TEXT NOT NULL DEFAULT 'telebirr',   -- telebirr|cbebirr|mpesa|bank|cash
    name            TEXT NOT NULL,
    account_number  TEXT NOT NULL DEFAULT '',           -- short code / till / merchant id / bank account
    currency        TEXT NOT NULL DEFAULT 'ETB',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    opening_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_accounts_company ON payment_accounts(company_id);

CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    account_id      TEXT,
    provider        TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'in',          -- in|out
    amount          NUMERIC(18,2) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'ETB',
    fee             NUMERIC(18,2) NOT NULL DEFAULT 0,
    payer_name      TEXT NOT NULL DEFAULT '',
    payer_msisdn    TEXT,
    payee_name      TEXT NOT NULL DEFAULT '',
    payee_msisdn    TEXT,
    provider_txn_id TEXT,
    reference       TEXT NOT NULL DEFAULT '',
    narration       TEXT NOT NULL DEFAULT '',
    paid_at         TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'completed',   -- pending|completed|failed|reversed
    source          TEXT NOT NULL DEFAULT 'manual',      -- manual|statement_import|notification|api
    raw             JSONB,
    matched_type    TEXT,                                -- income|expense|invoice|NULL
    matched_id      TEXT,
    matched_at      TIMESTAMP,
    matched_by      TEXT,
    reconciled      BOOLEAN NOT NULL DEFAULT FALSE,
    reconciled_at   TIMESTAMP,
    reconciled_by   TEXT,
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_provider_txn
    ON payments(company_id, provider, provider_txn_id) WHERE provider_txn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_company ON payments(company_id);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at);
CREATE INDEX IF NOT EXISTS idx_payments_unreconciled ON payments(company_id, reconciled) WHERE reconciled = FALSE;
CREATE INDEX IF NOT EXISTS idx_payments_matched ON payments(matched_type, matched_id);

CREATE TABLE IF NOT EXISTS payment_statement_imports (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    account_id      TEXT,
    provider        TEXT NOT NULL DEFAULT '',
    filename        TEXT NOT NULL DEFAULT '',
    rows_total      INTEGER NOT NULL DEFAULT 0,
    rows_imported   INTEGER NOT NULL DEFAULT 0,
    rows_duplicate  INTEGER NOT NULL DEFAULT 0,
    rows_error      INTEGER NOT NULL DEFAULT 0,
    errors          TEXT NOT NULL DEFAULT '',
    imported_by     TEXT NOT NULL DEFAULT '',
    imported_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_imports_company ON payment_statement_imports(company_id);

CREATE TABLE IF NOT EXISTS payment_provider_settings (
    company_id      TEXT NOT NULL DEFAULT 'default',
    provider        TEXT NOT NULL,
    settings        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- non-secret: short code, merchant name, callback secret ref
    updated_by      TEXT NOT NULL DEFAULT '',
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, provider)
);

CREATE TABLE IF NOT EXISTS payment_inbound_processed (
    log_id          TEXT PRIMARY KEY,                    -- webhook_inbound_log.id (as text)
    source          TEXT NOT NULL DEFAULT '',
    payment_id      TEXT,
    outcome         TEXT NOT NULL DEFAULT '',            -- created|duplicate|rejected|invalid|error
    detail          TEXT NOT NULL DEFAULT '',
    processed_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

MATCH_TYPES = ("income", "expense", "invoice")


def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC/TIMESTAMP."""
    return value if value not in ("", None) else None


def _num(value, default=Decimal("0")):
    amt = parse_amount(value)
    return amt if amt is not None else default


def _json(value) -> Optional[psycopg2.extras.Json]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {"_raw": value}
    return psycopg2.extras.Json(value, dumps=lambda o: json.dumps(o, default=str))


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("payments schema ready")
    except Exception as e:
        logger.error("payments schema init failed: %s", e)


class PaymentsDataStore:

    def __init__(self):
        self._columns_cache: Dict[str, set] = {}

    def ensure_schema(self):
        ensure_schema()

    # ── information_schema helpers ─────────────────────────────
    def _table_columns(self, table_name: str) -> set:
        cols = self._columns_cache.get(table_name)
        if cols:
            return cols
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name=%s",
                    (table_name,))
                cols = {r["column_name"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("_table_columns(%s) failed: %s", table_name, e)
            cols = set()
        if cols:
            self._columns_cache[table_name] = cols
        return cols

    def table_exists(self, table_name: str) -> bool:
        return bool(self._table_columns(table_name))

    # ── Accounts ───────────────────────────────────────────────
    def get_accounts(self, company_id: str, active_only: bool = False, provider: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM payment_accounts WHERE company_id=%s"
            params: List[Any] = [company_id]
            if active_only:
                sql += " AND is_active = TRUE"
            if provider:
                sql += " AND provider=%s"; params.append(provider)
            sql += " ORDER BY provider, name"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_accounts: %s", e); return []

    def get_account(self, account_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM payment_accounts WHERE id=%s AND company_id=%s",
                            (account_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_account: %s", e); return None

    def default_account_for(self, company_id: str, provider: str) -> Optional[dict]:
        accts = self.get_accounts(company_id, active_only=True, provider=provider)
        return accts[0] if accts else None

    def create_account(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            provider = (data.get("provider") or "telebirr").lower()
            if provider not in PROVIDERS:
                raise ValueError(f"invalid provider {provider!r}")
            aid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO payment_accounts(id,company_id,provider,name,account_number,currency,
                               is_active,opening_balance,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (aid, company_id, provider, (data.get("name") or "").strip() or provider.title(),
                         (data.get("account_number") or data.get("short_code") or "").strip(),
                         (data.get("currency") or "ETB").upper(),
                         str(data.get("is_active", "1")).lower() in ("1", "true", "on", "yes"),
                         _num(data.get("opening_balance")), data.get("notes") or ""))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_account: %s", e); return None

    def update_account(self, account_id: str, company_id: str, data: dict) -> bool:
        try:
            provider = (data.get("provider") or "telebirr").lower()
            if provider not in PROVIDERS:
                raise ValueError(f"invalid provider {provider!r}")
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE payment_accounts SET provider=%s,name=%s,account_number=%s,currency=%s,
                               opening_balance=%s,notes=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""",
                        (provider, (data.get("name") or "").strip() or provider.title(),
                         (data.get("account_number") or data.get("short_code") or "").strip(),
                         (data.get("currency") or "ETB").upper(), _num(data.get("opening_balance")),
                         data.get("notes") or "", account_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_account: %s", e); return False

    def toggle_account(self, account_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE payment_accounts SET is_active = NOT is_active, updated_at=NOW() "
                                "WHERE id=%s AND company_id=%s", (account_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("toggle_account: %s", e); return False

    def account_balances(self, company_id: str) -> List[dict]:
        """Every account with opening + completed movements (in − fee, −(out + fee))."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT a.*,
                              COALESCE(SUM(CASE WHEN p.direction='in' THEN p.amount - p.fee
                                                ELSE -(p.amount + p.fee) END), 0) AS movement,
                              COUNT(p.id) AS txn_count,
                              COALESCE(SUM(CASE WHEN p.reconciled = FALSE THEN 1 ELSE 0 END), 0) AS unreconciled
                       FROM payment_accounts a
                       LEFT JOIN payments p ON p.account_id = a.id AND p.status = 'completed'
                       WHERE a.company_id=%s
                       GROUP BY a.id ORDER BY a.is_active DESC, a.provider, a.name""",
                    (company_id,))
                rows = []
                for r in cur.fetchall():
                    d = dict(r)
                    d["balance"] = Decimal(d.get("opening_balance") or 0) + Decimal(d.get("movement") or 0)
                    rows.append(d)
                return rows
        except Exception as e:
            logger.error("account_balances: %s", e); return []

    # ── Payments ───────────────────────────────────────────────
    def get_payment(self, payment_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT p.*, a.name AS account_name, a.account_number
                       FROM payments p LEFT JOIN payment_accounts a ON a.id = p.account_id
                       WHERE p.id=%s AND p.company_id=%s""", (payment_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_payment: %s", e); return None

    def find_by_txn(self, company_id: str, provider: str, provider_txn_id: str) -> Optional[dict]:
        if not provider_txn_id:
            return None
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM payments WHERE company_id=%s AND provider=%s "
                            "AND UPPER(provider_txn_id)=UPPER(%s)",
                            (company_id, provider, str(provider_txn_id).strip()))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("find_by_txn: %s", e); return None

    def existing_txn_keys(self, company_id: str, provider: str) -> set:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT provider_txn_id FROM payments WHERE company_id=%s AND provider=%s "
                            "AND provider_txn_id IS NOT NULL", (company_id, provider))
                return {(provider, str(r["provider_txn_id"]).strip().upper()) for r in cur.fetchall()}
        except Exception as e:
            logger.error("existing_txn_keys: %s", e); return set()

    def record_payment(self, company_id: str, **f) -> Optional[dict]:
        """
        Insert a payment. Returns the row; when the (company, provider,
        provider_txn_id) already exists the EXISTING row is returned with
        ``_duplicate=True`` and nothing is written. Returns None on error.
        """
        try:
            provider = (f.get("provider") or "").lower()
            if provider not in PROVIDERS:
                raise ValueError(f"invalid provider {provider!r}")
            direction = (f.get("direction") or "in").lower()
            if direction not in DIRECTIONS:
                raise ValueError(f"invalid direction {direction!r}")
            status = (f.get("status") or "completed").lower()
            if status not in STATUSES:
                status = "completed"
            amount = parse_amount(f.get("amount"))
            if amount is None or amount <= 0:
                raise ValueError("amount must be greater than zero")
            txn = (str(f.get("provider_txn_id")).strip() or None) if f.get("provider_txn_id") not in (None, "") else None
            if txn:
                existing = self.find_by_txn(company_id, provider, txn)
                if existing:
                    existing["_duplicate"] = True
                    return existing
            paid_at = f.get("paid_at")
            paid_at = parse_datetime(paid_at) if paid_at not in (None, "") else None
            account_id = _opt(f.get("account_id"))
            if not account_id:
                acct = self.default_account_for(company_id, provider)
                account_id = acct["id"] if acct else None
            matched_type = _opt(f.get("matched_type"))
            matched_id = _opt(f.get("matched_id"))
            if matched_type not in MATCH_TYPES or not matched_id:
                matched_type, matched_id = None, None
            pid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO payments(id,company_id,account_id,provider,direction,amount,currency,fee,
                               payer_name,payer_msisdn,payee_name,payee_msisdn,provider_txn_id,reference,narration,
                               paid_at,status,source,raw,matched_type,matched_id,matched_at,matched_by,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                  CASE WHEN %s IS NULL THEN NULL ELSE NOW() END,%s,%s)
                           ON CONFLICT DO NOTHING RETURNING *""",
                        (pid, company_id, account_id, provider, direction, amount,
                         (f.get("currency") or "ETB").upper(), _num(f.get("fee")),
                         (f.get("payer_name") or "").strip(), normalize_msisdn(f.get("payer_msisdn")) or _opt(f.get("payer_msisdn")),
                         (f.get("payee_name") or "").strip(), normalize_msisdn(f.get("payee_msisdn")) or _opt(f.get("payee_msisdn")),
                         txn, (f.get("reference") or "").strip(), (f.get("narration") or "").strip(),
                         paid_at or datetime.now(), status,
                         f.get("source") if f.get("source") in ("manual", "statement_import", "notification", "api") else "manual",
                         _json(f.get("raw")), matched_type, matched_id, matched_id,
                         (f.get("matched_by") or f.get("created_by") or "") if matched_id else None,
                         f.get("created_by") or ""))
                    row = cur.fetchone()
            if row:
                return dict(row)
            existing = self.find_by_txn(company_id, provider, txn) if txn else None
            if existing:
                existing["_duplicate"] = True
            return existing
        except Exception as e:
            logger.error("record_payment: %s", e)
            return None

    def record_normalized(self, company_id: str, np_: NormalizedPayment, source: str,
                          account_id: str = None, created_by: str = "") -> Optional[dict]:
        rec = np_.to_record()
        rec.update(source=source, account_id=account_id, created_by=created_by)
        return self.record_payment(company_id, **rec)

    def list_payments(self, company_id: str, filters: dict = None, limit: int = 500) -> List[dict]:
        f = filters or {}
        try:
            sql = ("SELECT p.*, a.name AS account_name FROM payments p "
                   "LEFT JOIN payment_accounts a ON a.id = p.account_id WHERE p.company_id=%s")
            params: List[Any] = [company_id]
            if f.get("provider"):
                sql += " AND p.provider=%s"; params.append(f["provider"])
            if f.get("direction"):
                sql += " AND p.direction=%s"; params.append(f["direction"])
            if f.get("status"):
                sql += " AND p.status=%s"; params.append(f["status"])
            if f.get("account_id"):
                sql += " AND p.account_id=%s"; params.append(f["account_id"])
            if f.get("reconciled") in ("yes", "true", "1", True):
                sql += " AND p.reconciled = TRUE"
            elif f.get("reconciled") in ("no", "false", "0", False):
                sql += " AND p.reconciled = FALSE"
            if f.get("matched") == "yes":
                sql += " AND p.matched_id IS NOT NULL"
            elif f.get("matched") == "no":
                sql += " AND p.matched_id IS NULL"
            if f.get("date_from"):
                sql += " AND p.paid_at >= %s"; params.append(f["date_from"])
            if f.get("date_to"):
                sql += " AND p.paid_at < (%s::date + INTERVAL '1 day')"; params.append(f["date_to"])
            if f.get("q"):
                like = f"%{f['q'].strip()}%"
                sql += (" AND (p.payer_name ILIKE %s OR p.payee_name ILIKE %s OR p.payer_msisdn ILIKE %s "
                        "OR p.payee_msisdn ILIKE %s OR p.provider_txn_id ILIKE %s OR p.reference ILIKE %s "
                        "OR p.narration ILIKE %s)")
                params += [like] * 7
            sql += " ORDER BY p.paid_at DESC NULLS LAST, p.created_at DESC LIMIT %s"
            params.append(int(limit))
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_payments: %s", e); return []

    def recent_payments(self, company_id: str, limit: int = 10) -> List[dict]:
        return self.list_payments(company_id, {}, limit=limit)

    def unmatched_payments(self, company_id: str, limit: int = 50) -> List[dict]:
        return self.list_payments(company_id, {"status": "completed", "matched": "no", "reconciled": "no"}, limit=limit)

    def set_status(self, payment_id: str, company_id: str, status: str, by: str = "") -> bool:
        if status not in STATUSES:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE payments SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                                (status, payment_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("set_status: %s", e); return False

    def reverse_payment(self, payment_id: str, company_id: str, by: str = "") -> bool:
        """Mark reversed and drop any match / reconciliation flag."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE payments SET status='reversed', reconciled=FALSE, reconciled_at=NULL, reconciled_by=NULL,
                               matched_type=NULL, matched_id=NULL, matched_at=NULL, matched_by=NULL, updated_at=NOW()
                           WHERE id=%s AND company_id=%s AND status <> 'reversed'""", (payment_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("reverse_payment: %s", e); return False

    def set_reconciled(self, payment_id: str, company_id: str, reconciled: bool, by: str = "") -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if reconciled:
                        cur.execute("UPDATE payments SET reconciled=TRUE, reconciled_at=NOW(), reconciled_by=%s, "
                                    "updated_at=NOW() WHERE id=%s AND company_id=%s", (by, payment_id, company_id))
                    else:
                        cur.execute("UPDATE payments SET reconciled=FALSE, reconciled_at=NULL, reconciled_by=NULL, "
                                    "updated_at=NOW() WHERE id=%s AND company_id=%s", (payment_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("set_reconciled: %s", e); return False

    def link_payment(self, payment_id: str, matched_type: str, matched_id: str, by: str = "",
                     company_id: str = None, mark_reconciled: bool = True) -> bool:
        if matched_type not in MATCH_TYPES or not matched_id:
            return False
        try:
            sql = ("UPDATE payments SET matched_type=%s, matched_id=%s, matched_at=NOW(), matched_by=%s, "
                   "updated_at=NOW()")
            params: List[Any] = [matched_type, str(matched_id).strip(), by]
            if mark_reconciled:
                sql += ", reconciled=TRUE, reconciled_at=NOW(), reconciled_by=%s"; params.append(by)
            sql += " WHERE id=%s"; params.append(payment_id)
            if company_id:
                sql += " AND company_id=%s"; params.append(company_id)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("link_payment: %s", e); return False

    def unlink_payment(self, payment_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE payments SET matched_type=NULL, matched_id=NULL, matched_at=NULL, matched_by=NULL,
                               reconciled=FALSE, reconciled_at=NULL, reconciled_by=NULL, updated_at=NOW()
                           WHERE id=%s AND company_id=%s""", (payment_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("unlink_payment: %s", e); return False

    # ── Dashboard ──────────────────────────────────────────────
    def get_stats(self, company_id: str) -> dict:
        empty_p = {p: {"today_in": Decimal(0), "today_out": Decimal(0), "month_in": Decimal(0),
                       "month_out": Decimal(0), "count": 0} for p in PROVIDERS}
        stats = {"by_provider": empty_p, "today_in": Decimal(0), "today_out": Decimal(0),
                 "month_in": Decimal(0), "month_out": Decimal(0), "total_count": 0,
                 "unreconciled": 0, "unmatched": 0, "pending": 0, "total_balance": Decimal(0)}
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT provider, direction,
                              COALESCE(SUM(CASE WHEN paid_at::date = CURRENT_DATE THEN amount ELSE 0 END),0) AS today,
                              COALESCE(SUM(CASE WHEN date_trunc('month', paid_at) = date_trunc('month', CURRENT_DATE)
                                                THEN amount ELSE 0 END),0) AS month,
                              COUNT(*) AS n
                       FROM payments WHERE company_id=%s AND status='completed'
                       GROUP BY provider, direction""", (company_id,))
                for r in cur.fetchall():
                    p = stats["by_provider"].setdefault(r["provider"], {
                        "today_in": Decimal(0), "today_out": Decimal(0), "month_in": Decimal(0),
                        "month_out": Decimal(0), "count": 0})
                    d = r["direction"]
                    p[f"today_{d}"] += Decimal(r["today"]); p[f"month_{d}"] += Decimal(r["month"])
                    p["count"] += int(r["n"])
                    stats[f"today_{d}"] += Decimal(r["today"]); stats[f"month_{d}"] += Decimal(r["month"])
                    stats["total_count"] += int(r["n"])
                cur.execute(
                    """SELECT COUNT(*) FILTER (WHERE status='completed' AND reconciled = FALSE) AS unreconciled,
                              COUNT(*) FILTER (WHERE status='completed' AND matched_id IS NULL) AS unmatched,
                              COUNT(*) FILTER (WHERE status='pending') AS pending
                       FROM payments WHERE company_id=%s""", (company_id,))
                r = cur.fetchone() or {}
                stats["unreconciled"] = int(r.get("unreconciled") or 0)
                stats["unmatched"] = int(r.get("unmatched") or 0)
                stats["pending"] = int(r.get("pending") or 0)
        except Exception as e:
            logger.error("get_stats: %s", e)
        return stats

    # ── Reconciliation ─────────────────────────────────────────
    def _income_candidates(self, company_id: str, payment: dict) -> List[dict]:
        cols = self._table_columns("vat_income")
        if not cols:
            return []
        lo, hi = amount_window(payment.get("amount"))
        if lo is None:
            return []
        date_col = ("COALESCE(income_date, contract_date)" if "income_date" in cols
                    else ("contract_date" if "contract_date" in cols else None))
        select = ["income_id AS id", "description", "gross_amount AS amount", "customer_name AS counterparty",
                  "invoice_number"]
        if date_col:
            select.append(f"{date_col} AS date")
        for opt in ("tender_id", "payment_mode", "income_date", "contract_date"):
            if opt in cols:
                select.append(opt)
        sql = f"SELECT {', '.join(select)} FROM vat_income WHERE company_id=%s AND gross_amount BETWEEN %s AND %s"
        params: List[Any] = [company_id, lo, hi]
        if "is_active" in cols:
            sql += " AND is_active = TRUE"
        d_lo, d_hi = date_window(payment.get("paid_at"))
        if date_col and d_lo:
            sql += f" AND ({date_col} IS NULL OR {date_col} BETWEEN %s AND %s)"; params += [d_lo, d_hi]
        sql += (" AND income_id NOT IN (SELECT matched_id FROM payments WHERE company_id=%s "
                "AND matched_type='income' AND matched_id IS NOT NULL AND id <> %s) LIMIT 50")
        params += [company_id, payment.get("id") or ""]
        try:
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                out = []
                for r in cur.fetchall():
                    d = dict(r)
                    ref = " ".join(str(x) for x in (d.get("invoice_number"), d.get("tender_id")) if x)
                    out.append({"type": "income", "id": d["id"], "amount": d.get("amount"), "date": d.get("date"),
                                "description": d.get("description") or "", "counterparty": d.get("counterparty") or "",
                                "reference": ref, "invoice_number": d.get("invoice_number") or "",
                                "tender_id": d.get("tender_id") or "", "payment_mode": d.get("payment_mode") or ""})
                return out
        except Exception as e:
            logger.error("_income_candidates: %s", e); return []

    def _expense_candidates(self, company_id: str, payment: dict) -> List[dict]:
        cols = self._table_columns("vat_expenses")
        if not cols:
            return []
        lo, hi = amount_window(payment.get("amount"))
        if lo is None:
            return []
        date_col = "expense_date" if "expense_date" in cols else None
        select = ["expense_id AS id", "description", "gross_amount AS amount", "supplier_name AS counterparty",
                  "receipt_number"]
        if date_col:
            select.append(f"{date_col} AS date")
        sql = f"SELECT {', '.join(select)} FROM vat_expenses WHERE company_id=%s AND gross_amount BETWEEN %s AND %s"
        params: List[Any] = [company_id, lo, hi]
        if "is_active" in cols:
            sql += " AND is_active = TRUE"
        d_lo, d_hi = date_window(payment.get("paid_at"))
        if date_col and d_lo:
            sql += f" AND ({date_col} IS NULL OR {date_col} BETWEEN %s AND %s)"; params += [d_lo, d_hi]
        sql += (" AND expense_id NOT IN (SELECT matched_id FROM payments WHERE company_id=%s "
                "AND matched_type='expense' AND matched_id IS NOT NULL AND id <> %s) LIMIT 50")
        params += [company_id, payment.get("id") or ""]
        try:
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [{"type": "expense", "id": r["id"], "amount": r.get("amount"), "date": r.get("date"),
                         "description": r.get("description") or "", "counterparty": r.get("counterparty") or "",
                         "reference": r.get("receipt_number") or "", "invoice_number": r.get("receipt_number") or "",
                         "tender_id": "", "payment_mode": ""} for r in cur.fetchall()]
        except Exception as e:
            logger.error("_expense_candidates: %s", e); return []

    def find_matches(self, company_id: str, payment: dict, limit: int = 5) -> List[dict]:
        """Scored income (direction in) / expense (direction out) candidates, best first."""
        if not payment:
            return []
        if (payment.get("direction") or "in") == "in":
            cands = self._income_candidates(company_id, payment)
        else:
            cands = self._expense_candidates(company_id, payment)
        return rank_candidates(payment, cands)[:limit]

    def reconcile_queue(self, company_id: str, limit: int = 50) -> List[dict]:
        queue = []
        for p in self.unmatched_payments(company_id, limit=limit):
            p["candidates"] = self.find_matches(company_id, p)
            queue.append(p)
        return queue

    def auto_match(self, company_id: str, by: str = "auto-match") -> dict:
        """Link every unmatched payment that has one clear high-confidence candidate."""
        result = {"scanned": 0, "linked": 0, "skipped": 0}
        for p in self.unmatched_payments(company_id, limit=500):
            result["scanned"] += 1
            best = pick_auto_match(self.find_matches(company_id, p))
            if best and self.link_payment(p["id"], best["type"], best["id"], by, company_id):
                result["linked"] += 1
            else:
                result["skipped"] += 1
        return result

    def companies_with_unmatched(self) -> List[str]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT DISTINCT company_id FROM payments WHERE status='completed' AND matched_id IS NULL")
                return [r["company_id"] for r in cur.fetchall()]
        except Exception as e:
            logger.error("companies_with_unmatched: %s", e); return []

    def matched_record(self, company_id: str, matched_type: str, matched_id: str) -> Optional[dict]:
        """Best-effort lookup of the linked income/expense for the detail page."""
        try:
            if matched_type == "income" and self.table_exists("vat_income"):
                sql = "SELECT * FROM vat_income WHERE income_id=%s AND company_id=%s"
            elif matched_type == "expense" and self.table_exists("vat_expenses"):
                sql = "SELECT * FROM vat_expenses WHERE expense_id=%s AND company_id=%s"
            else:
                return None
            with get_cursor() as cur:
                cur.execute(sql, (matched_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("matched_record: %s", e); return None

    # ── Statement imports ──────────────────────────────────────
    def import_statement(self, company_id: str, provider: str, account_id: Optional[str],
                         filename: str, rows: List[dict], by: str = "") -> dict:
        """Parse statement rows via the provider adapter and record them (dedup on txn id)."""
        report = {"total": 0, "imported": 0, "duplicates": 0, "errors": [], "import_id": None,
                  "provider": provider, "filename": filename}
        try:
            adapter = get_adapter(provider)
        except KeyError as e:
            report["errors"].append(str(e)); return report
        seen: set = set()
        for idx, row in enumerate(rows):
            rownum = idx + 2  # header is row 1
            if not any(v not in (None, "") and not (isinstance(v, float) and v != v) for v in row.values()):
                continue  # blank row
            report["total"] += 1
            try:
                np_ = adapter.parse_statement_row(row)
                if not np_.is_valid():
                    report["errors"].append(f"Row {rownum}: no positive amount"); continue
                key = np_.duplicate_key()
                if key and key in seen:
                    report["duplicates"] += 1; continue
                if key:
                    seen.add(key)
                rec = self.record_normalized(company_id, np_, "statement_import", account_id, by)
                if rec is None:
                    report["errors"].append(f"Row {rownum}: could not be saved"); continue
                if rec.get("_duplicate"):
                    report["duplicates"] += 1
                else:
                    report["imported"] += 1
            except Exception as e:
                report["errors"].append(f"Row {rownum}: {e}")
        report["import_id"] = self._log_import(company_id, provider, account_id, filename, report, by)
        return report

    def _log_import(self, company_id, provider, account_id, filename, report, by) -> Optional[str]:
        try:
            iid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO payment_statement_imports(id,company_id,account_id,provider,filename,rows_total,
                               rows_imported,rows_duplicate,rows_error,errors,imported_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (iid, company_id, _opt(account_id), provider, filename or "", report["total"],
                         report["imported"], report["duplicates"], len(report["errors"]),
                         "\n".join(report["errors"][:50]), by))
            return iid
        except Exception as e:
            logger.error("_log_import: %s", e); return None

    def get_imports(self, company_id: str, limit: int = 20) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT i.*, a.name AS account_name FROM payment_statement_imports i
                       LEFT JOIN payment_accounts a ON a.id = i.account_id
                       WHERE i.company_id=%s ORDER BY i.imported_at DESC LIMIT %s""", (company_id, limit))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_imports: %s", e); return []

    # ── Provider settings ──────────────────────────────────────
    def get_settings(self, company_id: str, provider: str) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT settings, updated_at, updated_by FROM payment_provider_settings "
                            "WHERE company_id=%s AND provider=%s", (company_id, provider))
                row = cur.fetchone()
                if not row:
                    return {}
                s = row["settings"] or {}
                if isinstance(s, str):
                    s = json.loads(s)
                s = dict(s)
                s["_updated_at"] = row.get("updated_at"); s["_updated_by"] = row.get("updated_by")
                return s
        except Exception as e:
            logger.error("get_settings: %s", e); return {}

    def all_settings(self, company_id: str) -> Dict[str, dict]:
        return {p: self.get_settings(company_id, p) for p in PROVIDERS}

    def save_settings(self, company_id: str, provider: str, settings: dict, by: str = "") -> bool:
        if provider not in PROVIDERS:
            return False
        clean = {k: v for k, v in (settings or {}).items() if not str(k).startswith("_")}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO payment_provider_settings(company_id,provider,settings,updated_by,updated_at)
                           VALUES(%s,%s,%s,%s,NOW())
                           ON CONFLICT (company_id, provider) DO UPDATE
                               SET settings=EXCLUDED.settings, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                        (company_id, provider, psycopg2.extras.Json(clean), by))
            return True
        except Exception as e:
            logger.error("save_settings: %s", e); return False

    # ── Inbound notifications (webhook_inbound_log) ────────────
    _LOG_TABLE = "webhook_inbound_log"

    def inbound_log_available(self) -> bool:
        return self.table_exists(self._LOG_TABLE)

    def pending_inbound_count(self) -> Optional[int]:
        cols = self._table_columns(self._LOG_TABLE)
        if not cols or "source" not in cols or "id" not in cols:
            return None
        try:
            with get_cursor() as cur:
                cur.execute(
                    f"""SELECT COUNT(*) AS n FROM {self._LOG_TABLE} l
                        WHERE LOWER(l.source) IN %s
                          AND NOT EXISTS (SELECT 1 FROM payment_inbound_processed x WHERE x.log_id = l.id::text)""",
                    (tuple(NOTIFYING_PROVIDERS),))
                return int((cur.fetchone() or {}).get("n") or 0)
        except Exception as e:
            logger.error("pending_inbound_count: %s", e); return None

    def _fetch_unprocessed_inbound(self, limit: int) -> Tuple[List[dict], set]:
        cols = self._table_columns(self._LOG_TABLE)
        if not cols or "source" not in cols or "id" not in cols:
            return [], cols
        order = "received_at" if "received_at" in cols else ("created_at" if "created_at" in cols else "id")
        try:
            with get_cursor() as cur:
                cur.execute(
                    f"""SELECT l.* FROM {self._LOG_TABLE} l
                        WHERE LOWER(l.source) IN %s
                          AND NOT EXISTS (SELECT 1 FROM payment_inbound_processed x WHERE x.log_id = l.id::text)
                        ORDER BY l.{order} LIMIT %s""",
                    (tuple(NOTIFYING_PROVIDERS), int(limit)))
                return [dict(r) for r in cur.fetchall()], cols
        except Exception as e:
            logger.error("_fetch_unprocessed_inbound: %s", e); return [], cols

    def _mark_inbound(self, log_id: Any, source: str, outcome: str, detail: str = "",
                      payment_id: str = None, log_cols: set = ()):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO payment_inbound_processed(log_id,source,payment_id,outcome,detail)
                           VALUES(%s,%s,%s,%s,%s) ON CONFLICT (log_id) DO UPDATE
                               SET outcome=EXCLUDED.outcome, detail=EXCLUDED.detail,
                                   payment_id=EXCLUDED.payment_id, processed_at=NOW()""",
                        (str(log_id), source, payment_id, outcome, (detail or "")[:1000]))
        except Exception as e:
            logger.error("_mark_inbound: %s", e)
        # Best effort: also flag the row in the generic log table if it has such columns.
        sets = []
        if "processed_at" in log_cols:
            sets.append("processed_at=NOW()")
        if "processed" in log_cols:
            sets.append("processed=TRUE")
        if "processed_by" in log_cols:
            sets.append("processed_by='payments'")
        if sets:
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(f"UPDATE {self._LOG_TABLE} SET {', '.join(sets)} WHERE id::text=%s", (str(log_id),))
            except Exception as e:
                logger.debug("inbound log flag update skipped: %s", e)

    @staticmethod
    def _log_field(row: dict, *names: str):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    def process_inbound_notifications(self, limit: int = 200) -> dict:
        """
        Turn unprocessed webhook_inbound_log rows (source telebirr/cbebirr/mpesa)
        into payments. Tolerates the table being absent (returns table_present=False).
        """
        summary = {"table_present": False, "scanned": 0, "created": 0, "duplicates": 0,
                   "rejected": 0, "invalid": 0, "errors": 0, "messages": []}
        if not self.inbound_log_available():
            summary["messages"].append("webhook_inbound_log table not present yet — nothing to process")
            return summary
        summary["table_present"] = True
        rows, cols = self._fetch_unprocessed_inbound(limit)
        settings_cache: Dict[Tuple[str, str], dict] = {}
        for row in rows:
            summary["scanned"] += 1
            log_id = row.get("id")
            source = str(row.get("source") or "").lower()
            try:
                adapter = get_adapter(source)
                company_id = str(self._log_field(row, "company_id", "tenant_id") or "default")
                headers = self._log_field(row, "headers", "request_headers", "http_headers") or {}
                if isinstance(headers, str):
                    try:
                        headers = json.loads(headers)
                    except ValueError:
                        headers = {}
                body = self._log_field(row, "body", "payload", "raw_body", "raw", "data", "content", "request_body")
                key = (company_id, adapter.provider)
                if key not in settings_cache:
                    settings_cache[key] = self.get_settings(company_id, adapter.provider)
                cfg = settings_cache[key]
                secret_ref = (cfg.get("callback_secret_ref") or "").strip()
                secret = os.environ.get(secret_ref) if secret_ref else None
                if str(cfg.get("require_signature", "")).lower() in ("1", "true", "on", "yes"):
                    raw_body = body if isinstance(body, (bytes, str)) else json.dumps(body or {}, separators=(",", ":"), sort_keys=True)
                    if not adapter.verify_signature(headers, raw_body, secret):
                        summary["rejected"] += 1
                        self._mark_inbound(log_id, source, "rejected", "signature verification failed", log_cols=cols)
                        continue
                np_ = adapter.parse_notification(headers, body)
                if np_ is None or not np_.is_valid():
                    summary["invalid"] += 1
                    self._mark_inbound(log_id, source, "invalid", "no payment could be parsed from the body", log_cols=cols)
                    continue
                if isinstance(np_.raw, dict):
                    np_.raw.setdefault("_inbound_log_id", str(log_id))
                acct = self.default_account_for(company_id, adapter.provider)
                rec = self.record_normalized(company_id, np_, "notification",
                                             acct["id"] if acct else None, "webhook")
                if rec is None:
                    summary["errors"] += 1
                    self._mark_inbound(log_id, source, "error", "record_payment failed", log_cols=cols)
                elif rec.get("_duplicate"):
                    summary["duplicates"] += 1
                    self._mark_inbound(log_id, source, "duplicate", f"already stored as {rec['id']}",
                                       payment_id=rec["id"], log_cols=cols)
                else:
                    summary["created"] += 1
                    self._mark_inbound(log_id, source, "created", "", payment_id=rec["id"], log_cols=cols)
            except Exception as e:
                summary["errors"] += 1
                logger.error("process_inbound_notifications row %s: %s", log_id, e)
                self._mark_inbound(log_id, source, "error", str(e), log_cols=cols)
        if summary["scanned"]:
            summary["messages"].append(
                f"{summary['scanned']} notification(s): {summary['created']} created, "
                f"{summary['duplicates']} duplicate, {summary['rejected']} rejected, "
                f"{summary['invalid']} unparseable, {summary['errors']} error(s)")
        else:
            summary["messages"].append("No unprocessed provider notifications")
        return summary

    def recent_inbound_outcomes(self, limit: int = 20) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM payment_inbound_processed ORDER BY processed_at DESC LIMIT %s", (limit,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("recent_inbound_outcomes: %s", e); return []


payments_store = PaymentsDataStore()


# ── Module-level API ─────────────────────────────────────────────

def record_payment(company_id: str, **fields) -> Optional[dict]:
    """Create a payment for ``company_id``. See PaymentsDataStore.record_payment."""
    return payments_store.record_payment(company_id, **fields)


def find_matches(company_id: str, payment: dict, limit: int = 5) -> List[dict]:
    """Scored income/expense candidates for a payment dict (or row)."""
    return payments_store.find_matches(company_id, payment, limit=limit)


def link_payment(payment_id: str, matched_type: str, matched_id: str, by: str = "",
                 company_id: str = None) -> bool:
    """Link a payment to an income/expense/invoice record and mark it reconciled."""
    return payments_store.link_payment(payment_id, matched_type, matched_id, by, company_id)
