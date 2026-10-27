"""
Bank Reconciliation Data Store — PostgreSQL backend.

Tables (all company-scoped):
  bank_accounts, bank_statements, bank_statement_lines, bank_reconciliations,
  bank_adjustments, bank_reconciled_ledger_lines

The general ledger (journal_entries / journal_entry_lines) and the payments
table are READ here but never altered: reconciled ledger lines are remembered
in bank_reconciled_ledger_lines, mobile-money payments are flagged through the
payments store's own public method.

Public helpers for other modules:
    bank_balances(company_id) -> list[dict]
    unreconciled_count(company_id) -> int
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import bank_recon_logic as L
from bank_recon_logic import D, q2
from db import get_conn, get_cursor

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bank_accounts (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    name            TEXT NOT NULL,
    bank_name       TEXT NOT NULL DEFAULT '',
    account_number  TEXT NOT NULL DEFAULT '',
    currency        TEXT NOT NULL DEFAULT 'ETB',
    gl_account_code TEXT NOT NULL DEFAULT '',
    opening_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
    opening_date    DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bank_accounts_company ON bank_accounts(company_id);

CREATE TABLE IF NOT EXISTS bank_statements (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    bank_account_id TEXT NOT NULL,
    period_start    DATE,
    period_end      DATE,
    opening_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
    closing_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
    filename        TEXT NOT NULL DEFAULT '',
    imported_by     TEXT NOT NULL DEFAULT '',
    imported_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    status          TEXT NOT NULL DEFAULT 'imported',   -- imported|in_progress|reconciled
    line_count      INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bank_statements_company ON bank_statements(company_id, bank_account_id);

CREATE TABLE IF NOT EXISTS bank_statement_lines (
    id              TEXT PRIMARY KEY,
    statement_id    TEXT NOT NULL,
    bank_account_id TEXT NOT NULL,
    company_id      TEXT NOT NULL DEFAULT 'default',
    txn_date        DATE NOT NULL,
    value_date      DATE,
    description     TEXT NOT NULL DEFAULT '',
    reference       TEXT NOT NULL DEFAULT '',
    debit           NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit          NUMERIC(18,2) NOT NULL DEFAULT 0,
    balance         NUMERIC(18,2),
    external_id     TEXT,
    row_hash        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'unmatched',  -- unmatched|matched|excluded
    matched_kind    TEXT,                                -- journal|payment|adjustment
    matched_id      TEXT,
    matched_at      TIMESTAMP,
    matched_by      TEXT,
    line_number     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bank_lines_statement ON bank_statement_lines(statement_id);
CREATE INDEX IF NOT EXISTS idx_bank_lines_account ON bank_statement_lines(company_id, bank_account_id, status);
CREATE INDEX IF NOT EXISTS idx_bank_lines_hash ON bank_statement_lines(bank_account_id, row_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_lines_external
    ON bank_statement_lines(bank_account_id, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS bank_reconciliations (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL DEFAULT 'default',
    bank_account_id        TEXT NOT NULL,
    statement_id           TEXT NOT NULL,
    as_of                  DATE,
    statement_balance      NUMERIC(18,2) NOT NULL DEFAULT 0,
    ledger_balance         NUMERIC(18,2) NOT NULL DEFAULT 0,
    outstanding_deposits   NUMERIC(18,2) NOT NULL DEFAULT 0,
    outstanding_payments   NUMERIC(18,2) NOT NULL DEFAULT 0,
    unexplained_difference NUMERIC(18,2) NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL DEFAULT 'open',   -- open|balanced|closed
    closed_by              TEXT,
    closed_at              TIMESTAMP,
    notes                  TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bank_recon_company ON bank_reconciliations(company_id, bank_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_recon_statement ON bank_reconciliations(statement_id);

CREATE TABLE IF NOT EXISTS bank_adjustments (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    reconciliation_id TEXT NOT NULL,
    bank_account_id   TEXT NOT NULL DEFAULT '',
    statement_line_id TEXT,
    kind              TEXT NOT NULL DEFAULT 'other',  -- bank_charge|interest|fx|correction|other
    amount            NUMERIC(18,2) NOT NULL DEFAULT 0,  -- signed: + raises bank balance
    gl_account_code   TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    adj_date          DATE,
    journal_entry_id  TEXT,
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bank_adjustments_company ON bank_adjustments(company_id, bank_account_id);
CREATE INDEX IF NOT EXISTS idx_bank_adjustments_recon ON bank_adjustments(reconciliation_id);

CREATE TABLE IF NOT EXISTS bank_reconciled_ledger_lines (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    bank_account_id   TEXT NOT NULL,
    entry_id          TEXT NOT NULL,
    line_id           TEXT NOT NULL,
    statement_line_id TEXT,
    reconciled_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    reconciled_by     TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_recon_ledger_line ON bank_reconciled_ledger_lines(line_id);
CREATE INDEX IF NOT EXISTS idx_bank_recon_ledger_account ON bank_reconciled_ledger_lines(company_id, bank_account_id);
"""


def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC."""
    return value if value not in ("", None) else None


def _str(value) -> str:
    return "" if value is None else str(value).strip()


def _iso(d) -> Optional[str]:
    dd = L.parse_date(d)
    return dd.isoformat() if dd else None


def ensure_schema():
    """Create the module's tables. Idempotent; never raises."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("bank_recon schema ready")
    except Exception as e:
        logger.error("bank_recon schema init failed: %s", e)


class BankReconDataStore:

    def __init__(self):
        self._columns_cache: Dict[str, set] = {}

    def ensure_schema(self):
        ensure_schema()

    # ── schema guards ────────────────────────────────────────────
    def _table_columns(self, table_name: str) -> set:
        cols = self._columns_cache.get(table_name)
        if cols:
            return cols
        try:
            with get_cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table_name,))
                cols = {r["column_name"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("_table_columns(%s): %s", table_name, e)
            cols = set()
        if cols:
            self._columns_cache[table_name] = cols
        return cols

    def ledger_available(self) -> bool:
        need_je = {"entry_id", "company_id", "entry_date", "description", "reference_number", "status"}
        need_jl = {"line_id", "entry_id", "account_code", "debit_amount", "credit_amount"}
        return need_je <= self._table_columns("journal_entries") and need_jl <= self._table_columns("journal_entry_lines")

    def payments_available(self) -> bool:
        return {"id", "company_id", "provider", "amount", "direction", "paid_at", "reconciled"} <= self._table_columns("payments")

    # ── Bank accounts ────────────────────────────────────────────
    def get_accounts(self, company_id: str, active_only: bool = False) -> List[dict]:
        try:
            sql = "SELECT * FROM bank_accounts WHERE company_id=%s"
            if active_only:
                sql += " AND is_active=TRUE"
            sql += " ORDER BY is_active DESC, bank_name, name"
            with get_cursor() as cur:
                cur.execute(sql, (company_id,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_accounts: %s", e); return []

    def get_account(self, account_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_accounts WHERE id=%s AND company_id=%s", (account_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_account: %s", e); return None

    def create_account(self, company_id: str, data: dict) -> Optional[dict]:
        name = _str(data.get("name"))
        if not name:
            return None
        try:
            aid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO bank_accounts(id,company_id,name,bank_name,account_number,currency,
                           gl_account_code,opening_balance,opening_date,is_active,notes,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (aid, company_id, name, _str(data.get("bank_name")), _str(data.get("account_number")),
                         _str(data.get("currency")) or "ETB", _str(data.get("gl_account_code")),
                         str(q2(data.get("opening_balance"))), _iso(data.get("opening_date")),
                         str(data.get("is_active", "1")).lower() not in ("0", "false", "off", "no"),
                         _str(data.get("notes")), _str(data.get("created_by"))))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_account: %s", e); return None

    def update_account(self, account_id: str, company_id: str, data: dict) -> bool:
        name = _str(data.get("name"))
        if not name:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE bank_accounts SET name=%s,bank_name=%s,account_number=%s,currency=%s,
                           gl_account_code=%s,opening_balance=%s,opening_date=%s,notes=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""",
                        (name, _str(data.get("bank_name")), _str(data.get("account_number")),
                         _str(data.get("currency")) or "ETB", _str(data.get("gl_account_code")),
                         str(q2(data.get("opening_balance"))), _iso(data.get("opening_date")),
                         _str(data.get("notes")), account_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_account: %s", e); return False

    def toggle_account(self, account_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE bank_accounts SET is_active=NOT is_active, updated_at=NOW() "
                                "WHERE id=%s AND company_id=%s", (account_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("toggle_account: %s", e); return False

    def get_gl_accounts(self, company_id: str, asset_only: bool = False) -> List[dict]:
        """Chart-of-accounts rows for the GL pickers (bank GL → asset accounts)."""
        try:
            sql = ("SELECT account_code, account_name, account_type FROM chart_of_accounts "
                   "WHERE company_id=%s AND is_active=TRUE")
            if asset_only:
                sql += " AND LOWER(account_type)='asset'"
            sql += " ORDER BY account_code"
            with get_cursor() as cur:
                cur.execute(sql, (company_id,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_gl_accounts: %s", e); return []

    def _account_name(self, cur, company_id: str, code: str) -> str:
        try:
            cur.execute("SELECT account_name FROM chart_of_accounts WHERE company_id=%s AND account_code=%s",
                        (company_id, code))
            row = cur.fetchone()
            return row["account_name"] if row else ""
        except Exception:
            return ""

    # ── Statements ───────────────────────────────────────────────
    def get_statements(self, company_id: str, account_id: Optional[str] = None, limit: int = 200) -> List[dict]:
        try:
            sql = """SELECT s.*, a.name AS account_name, a.bank_name, a.account_number, a.currency,
                            r.status AS recon_status, r.unexplained_difference, r.id AS reconciliation_id,
                            (SELECT COUNT(*) FROM bank_statement_lines l WHERE l.statement_id=s.id AND l.status='unmatched') AS unmatched_count
                     FROM bank_statements s
                     JOIN bank_accounts a ON a.id=s.bank_account_id
                     LEFT JOIN bank_reconciliations r ON r.statement_id=s.id
                     WHERE s.company_id=%s"""
            params: list = [company_id]
            if account_id:
                sql += " AND s.bank_account_id=%s"; params.append(account_id)
            sql += " ORDER BY s.period_end DESC NULLS LAST, s.imported_at DESC LIMIT %s"
            params.append(limit)
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_statements: %s", e); return []

    def get_statement(self, statement_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    """SELECT s.*, a.name AS account_name, a.bank_name, a.account_number, a.currency,
                              a.gl_account_code, a.opening_balance AS account_opening_balance,
                              a.opening_date AS account_opening_date
                       FROM bank_statements s JOIN bank_accounts a ON a.id=s.bank_account_id
                       WHERE s.id=%s AND s.company_id=%s""", (statement_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_statement: %s", e); return None

    def existing_dedupe_keys(self, account_id: str) -> Tuple[set, set]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT external_id, row_hash FROM bank_statement_lines WHERE bank_account_id=%s", (account_id,))
                rows = cur.fetchall()
                return ({r["external_id"] for r in rows if r["external_id"]}, {r["row_hash"] for r in rows if r["row_hash"]})
        except Exception as e:
            logger.error("existing_dedupe_keys: %s", e); return set(), set()

    def import_statement(self, company_id: str, account_id: str, filename: str, raw_rows: List[dict],
                         by: str = "", closing_balance: Any = None, opening_balance: Any = None,
                         period_start: Any = None, period_end: Any = None) -> dict:
        """Normalise + dedupe + persist a statement. Returns a report dict."""
        report = {"total": 0, "imported": 0, "duplicates": 0, "errors": [], "statement_id": None,
                  "columns": {}, "filename": filename}
        account = self.get_account(account_id, company_id)
        if not account:
            report["errors"].append("Bank account not found"); return report
        if not raw_rows:
            report["errors"].append("The file contained no data rows"); return report
        mapping = L.detect_columns(list(raw_rows[0].keys()))
        report["columns"] = {k: str(v) for k, v in mapping.items()}
        lines, errors = L.normalise_rows(raw_rows, mapping)
        report["errors"].extend(errors)
        report["total"] = len(lines) + len(errors)
        if not lines:
            return report
        ext_ids, hashes = self.existing_dedupe_keys(account_id)
        lines, dropped = L.dedupe_lines(lines, ext_ids, hashes)
        report["duplicates"] = dropped
        if not lines:
            return report
        lines.sort(key=lambda l: (l["txn_date"], l.get("balance") is None))
        p_start = L.parse_date(period_start) or min(l["txn_date"] for l in lines)
        p_end = L.parse_date(period_end) or max(l["txn_date"] for l in lines)
        last_with_balance = next((l for l in reversed(lines) if l.get("balance") is not None), None)
        if _opt(closing_balance) is not None:
            closing = q2(closing_balance)
        elif last_with_balance:
            closing = q2(last_with_balance["balance"])
        elif _opt(opening_balance) is not None:
            closing = q2(D(opening_balance) + L.statement_totals(lines)["net"])
        else:
            closing = Decimal(0)  # unknown — user can edit on the statement page
        opening = q2(opening_balance) if _opt(opening_balance) is not None else L.implied_opening_balance(closing, lines)
        sid = str(uuid.uuid4())
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO bank_statements(id,company_id,bank_account_id,period_start,period_end,
                           opening_balance,closing_balance,filename,imported_by,status,line_count)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'imported',%s)""",
                        (sid, company_id, account_id, p_start.isoformat(), p_end.isoformat(),
                         str(opening), str(closing), filename[:200], by, len(lines)))
                    for n, l in enumerate(lines, 1):
                        cur.execute(
                            """INSERT INTO bank_statement_lines(id,statement_id,bank_account_id,company_id,txn_date,
                               value_date,description,reference,debit,credit,balance,external_id,row_hash,line_number)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), sid, account_id, company_id, l["txn_date"].isoformat(),
                             l["value_date"].isoformat() if l.get("value_date") else None,
                             l["description"][:500], l["reference"][:120], str(l["debit"]), str(l["credit"]),
                             str(l["balance"]) if l.get("balance") is not None else None,
                             l.get("external_id"), l["row_hash"], n))
                    report["imported"] = len(lines)
                    report["statement_id"] = sid
        except Exception as e:
            logger.error("import_statement: %s", e)
            report["errors"].append(f"Could not save the statement: {e}")
            report["imported"] = 0
            report["statement_id"] = None
        return report

    def update_statement_balances(self, statement_id: str, company_id: str, opening: Any, closing: Any) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE bank_statements SET opening_balance=%s, closing_balance=%s WHERE id=%s AND company_id=%s",
                                (str(q2(opening)), str(q2(closing)), statement_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_statement_balances: %s", e); return False

    def set_statement_status(self, statement_id: str, company_id: str, status: str) -> bool:
        if status not in L.STATEMENT_STATUSES:
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE bank_statements SET status=%s WHERE id=%s AND company_id=%s",
                                (status, statement_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("set_statement_status: %s", e); return False

    def delete_statement(self, statement_id: str, company_id: str) -> Tuple[bool, str]:
        """Only statements with no matched lines and no closed reconciliation can be removed."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT status FROM bank_reconciliations WHERE statement_id=%s", (statement_id,))
                    r = cur.fetchone()
                    if r and r["status"] == "closed":
                        return False, "Reconciliation is closed"
                    cur.execute("SELECT COUNT(*) AS c FROM bank_statement_lines WHERE statement_id=%s AND status='matched'",
                                (statement_id,))
                    if cur.fetchone()["c"]:
                        return False, "Statement has matched lines — unmatch them first"
                    cur.execute("DELETE FROM bank_adjustments WHERE reconciliation_id IN "
                                "(SELECT id FROM bank_reconciliations WHERE statement_id=%s)", (statement_id,))
                    cur.execute("DELETE FROM bank_reconciliations WHERE statement_id=%s", (statement_id,))
                    cur.execute("DELETE FROM bank_statement_lines WHERE statement_id=%s AND company_id=%s", (statement_id, company_id))
                    cur.execute("DELETE FROM bank_statements WHERE id=%s AND company_id=%s", (statement_id, company_id))
                    return cur.rowcount > 0, ""
        except Exception as e:
            logger.error("delete_statement: %s", e); return False, str(e)

    def get_statement_lines(self, statement_id: str, company_id: str, status: Optional[str] = None) -> List[dict]:
        try:
            sql = "SELECT * FROM bank_statement_lines WHERE statement_id=%s AND company_id=%s"
            params: list = [statement_id, company_id]
            if status:
                sql += " AND status=%s"; params.append(status)
            sql += " ORDER BY txn_date, line_number"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_statement_lines: %s", e); return []

    def get_statement_line(self, line_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_statement_lines WHERE id=%s AND company_id=%s", (line_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_statement_line: %s", e); return None

    def unmatched_lines(self, company_id: str, account_id: Optional[str] = None) -> List[dict]:
        try:
            sql = """SELECT l.*, a.name AS account_name, a.bank_name FROM bank_statement_lines l
                     JOIN bank_accounts a ON a.id=l.bank_account_id
                     WHERE l.company_id=%s AND l.status='unmatched'"""
            params: list = [company_id]
            if account_id:
                sql += " AND l.bank_account_id=%s"; params.append(account_id)
            sql += " ORDER BY l.txn_date"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("unmatched_lines: %s", e); return []

    # ── General ledger (read-only) ───────────────────────────────
    def ledger_lines(self, company_id: str, gl_code: str, date_from: Any = None, date_to: Any = None,
                     unreconciled_only: bool = True, account_id: Optional[str] = None) -> List[dict]:
        """journal_entry_lines on the bank GL account, joined to their entries.
        entry_date is TEXT (ISO) in the shared schema; ISO-string comparison
        also works if a deployment made it a DATE."""
        if not gl_code or not self.ledger_available():
            return []
        try:
            sql = """SELECT jl.line_id, jl.entry_id, jl.account_code, jl.description,
                            jl.debit_amount AS debit, jl.credit_amount AS credit,
                            je.entry_date, je.description AS entry_description, je.reference_number AS reference,
                            je.status AS entry_status,
                            r.statement_line_id AS reconciled_against, r.reconciled_at
                     FROM journal_entry_lines jl
                     JOIN journal_entries je ON je.entry_id=jl.entry_id
                     LEFT JOIN bank_reconciled_ledger_lines r ON r.line_id=jl.line_id
                     WHERE je.company_id=%s AND jl.account_code=%s
                       AND COALESCE(jl.is_active,TRUE)=TRUE AND COALESCE(je.is_active,TRUE)=TRUE
                       AND COALESCE(je.status,'posted') <> 'void'"""
            params: list = [company_id, gl_code]
            f, t = _iso(date_from), _iso(date_to)
            if f:
                sql += " AND je.entry_date >= %s"; params.append(f)
            if t:
                sql += " AND je.entry_date <= %s"; params.append(t)
            if unreconciled_only:
                sql += " AND r.line_id IS NULL"
            sql += " ORDER BY je.entry_date, jl.entry_id, jl.line_number"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["debit"] = q2(r["debit"]); r["credit"] = q2(r["credit"])
                r["entry_date"] = L.parse_date(r.get("entry_date")) or r.get("entry_date")
            return rows
        except Exception as e:
            logger.error("ledger_lines: %s", e); return []

    def ledger_balance(self, company_id: str, account: dict, as_of: Any = None) -> Decimal:
        """opening_balance + Σ(dr − cr) on the GL account after the opening date, up to as_of."""
        opening = q2(account.get("opening_balance"))
        gl = account.get("gl_account_code") or ""
        if not gl or not self.ledger_available():
            return opening
        try:
            sql = """SELECT COALESCE(SUM(jl.debit_amount),0) AS dr, COALESCE(SUM(jl.credit_amount),0) AS cr
                     FROM journal_entry_lines jl JOIN journal_entries je ON je.entry_id=jl.entry_id
                     WHERE je.company_id=%s AND jl.account_code=%s
                       AND COALESCE(jl.is_active,TRUE)=TRUE AND COALESCE(je.is_active,TRUE)=TRUE
                       AND COALESCE(je.status,'posted') <> 'void'"""
            params: list = [company_id, gl]
            od = _iso(account.get("opening_date"))
            if od:
                sql += " AND je.entry_date > %s"; params.append(od)
            t = _iso(as_of)
            if t:
                sql += " AND je.entry_date <= %s"; params.append(t)
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                r = cur.fetchone()
            return q2(opening + D(r["dr"]) - D(r["cr"]))
        except Exception as e:
            logger.error("ledger_balance: %s", e); return opening

    # ── Payments (mobile money / bank transfers) — read + flag only ─
    def payment_candidates(self, company_id: str, date_from: Any = None, date_to: Any = None) -> List[dict]:
        if not self.payments_available():
            return []
        try:
            sql = """SELECT id, provider, direction, amount, currency, paid_at, reference, narration,
                            payer_name, payee_name, provider_txn_id, status
                     FROM payments WHERE company_id=%s AND reconciled=FALSE AND status='completed'"""
            params: list = [company_id]
            f, t = _iso(date_from), _iso(date_to)
            if f:
                sql += " AND paid_at >= %s"; params.append(f)
            if t:
                sql += " AND paid_at < (%s::date + 1)"; params.append(t)
            sql += " ORDER BY paid_at LIMIT 500"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
            out = []
            for r in rows:
                amt = q2(r["amount"])
                out.append(dict(r, entry_date=r["paid_at"].date() if isinstance(r["paid_at"], datetime) else r["paid_at"],
                                debit=amt if r["direction"] == "in" else Decimal(0),
                                credit=amt if r["direction"] != "in" else Decimal(0),
                                description=(r.get("narration") or "") + " " + (r.get("payer_name") or r.get("payee_name") or ""),
                                reference=r.get("reference") or r.get("provider_txn_id") or ""))
            return out
        except Exception as e:
            logger.error("payment_candidates: %s", e); return []

    def _flag_payment(self, payment_id: str, company_id: str, reconciled: bool, by: str) -> bool:
        try:
            from payments_data_store import payments_store
            return bool(payments_store.set_reconciled(payment_id, company_id, reconciled, by))
        except Exception as e:
            logger.warning("payments store unavailable (%s) — payment %s not flagged", e, payment_id)
            return False

    # ── Matching ─────────────────────────────────────────────────
    def match_line(self, line_id: str, company_id: str, kind: str, target_id: str, by: str = "",
                   entry_id: str = "") -> Tuple[bool, str]:
        """Match one statement line to a journal line (kind=journal, target=line_id,
        entry_id required) or a payment (kind=payment, target=payment id)."""
        if kind not in ("journal", "payment"):
            return False, "Invalid match kind"
        line = self.get_statement_line(line_id, company_id)
        if not line:
            return False, "Statement line not found"
        if line["status"] != "unmatched":
            return False, "Line is already matched or excluded"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if kind == "journal":
                        if not entry_id:
                            cur.execute("SELECT entry_id FROM journal_entry_lines WHERE line_id=%s", (target_id,))
                            r = cur.fetchone()
                            if not r:
                                return False, "Ledger line not found"
                            entry_id = r["entry_id"]
                        cur.execute("SELECT 1 FROM bank_reconciled_ledger_lines WHERE line_id=%s", (target_id,))
                        if cur.fetchone():
                            return False, "Ledger line is already reconciled"
                        cur.execute(
                            """INSERT INTO bank_reconciled_ledger_lines(id,company_id,bank_account_id,entry_id,line_id,
                               statement_line_id,reconciled_by) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), company_id, line["bank_account_id"], entry_id, target_id, line_id, by))
                    cur.execute(
                        """UPDATE bank_statement_lines SET status='matched', matched_kind=%s, matched_id=%s,
                           matched_at=NOW(), matched_by=%s WHERE id=%s AND company_id=%s""",
                        (kind, target_id, by, line_id, company_id))
                    cur.execute("UPDATE bank_statements SET status='in_progress' WHERE id=%s AND status='imported'",
                                (line["statement_id"],))
            if kind == "payment":
                self._flag_payment(target_id, company_id, True, by)
            return True, ""
        except Exception as e:
            logger.error("match_line: %s", e); return False, str(e)

    def unmatch_line(self, line_id: str, company_id: str, by: str = "") -> Tuple[bool, str]:
        line = self.get_statement_line(line_id, company_id)
        if not line:
            return False, "Statement line not found"
        if line["status"] == "unmatched":
            return True, ""
        if line.get("matched_kind") == "adjustment":
            return False, "Delete the adjustment instead"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bank_reconciled_ledger_lines WHERE statement_line_id=%s", (line_id,))
                    cur.execute(
                        """UPDATE bank_statement_lines SET status='unmatched', matched_kind=NULL, matched_id=NULL,
                           matched_at=NULL, matched_by=NULL WHERE id=%s AND company_id=%s""", (line_id, company_id))
            if line.get("matched_kind") == "payment" and line.get("matched_id"):
                self._flag_payment(line["matched_id"], company_id, False, by)
            return True, ""
        except Exception as e:
            logger.error("unmatch_line: %s", e); return False, str(e)

    def exclude_line(self, line_id: str, company_id: str, by: str = "") -> Tuple[bool, str]:
        line = self.get_statement_line(line_id, company_id)
        if not line:
            return False, "Statement line not found"
        if line["status"] == "matched":
            return False, "Unmatch the line first"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE bank_statement_lines SET status='excluded', matched_kind=NULL, matched_id=NULL, "
                                "matched_at=NOW(), matched_by=%s WHERE id=%s AND company_id=%s", (by, line_id, company_id))
            return True, ""
        except Exception as e:
            logger.error("exclude_line: %s", e); return False, str(e)

    def run_auto_match(self, statement_id: str, company_id: str, by: str = "auto-match") -> dict:
        """Score unmatched statement lines against unreconciled ledger lines (and
        unreconciled payments) and apply the unambiguous matches."""
        res = {"scanned": 0, "matched": 0, "payments": 0, "skipped": 0, "error": None}
        st = self.get_statement(statement_id, company_id)
        if not st:
            res["error"] = "Statement not found"; return res
        lines = self.get_statement_lines(statement_id, company_id, "unmatched")
        res["scanned"] = len(lines)
        if not lines:
            return res
        window = (st["period_start"], st["period_end"])
        ledger = self.ledger_lines(company_id, st.get("gl_account_code") or "", None, _shift(window[1], 10))
        for m in L.auto_match(lines, ledger):
            ok, _ = self.match_line(m["stmt"]["id"], company_id, "journal", m["ledger"]["line_id"], by, m["ledger"]["entry_id"])
            if ok:
                res["matched"] += 1
        still = self.get_statement_lines(statement_id, company_id, "unmatched")
        if still:
            pays = self.payment_candidates(company_id, _shift(window[0], -10), _shift(window[1], 10))
            for m in L.auto_match(still, pays):
                ok, _ = self.match_line(m["stmt"]["id"], company_id, "payment", m["ledger"]["id"], by)
                if ok:
                    res["payments"] += 1
        res["skipped"] = res["scanned"] - res["matched"] - res["payments"]
        return res

    # ── Reconciliation ───────────────────────────────────────────
    def get_reconciliation(self, statement_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_reconciliations WHERE statement_id=%s AND company_id=%s", (statement_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_reconciliation: %s", e); return None

    def refresh_reconciliation(self, statement_id: str, company_id: str) -> Optional[dict]:
        """(Re)compute the reconciliation figures for a statement and upsert the row.
        Returns the reconciliation dict enriched with the working figures
        (adjusted balances, unmatched statement totals, line lists)."""
        st = self.get_statement(statement_id, company_id)
        if not st:
            return None
        account = {"opening_balance": st.get("account_opening_balance"), "opening_date": st.get("account_opening_date"),
                   "gl_account_code": st.get("gl_account_code")}
        as_of = st.get("period_end") or date.today()
        ledger_bal = self.ledger_balance(company_id, account, as_of)
        outstanding = self.ledger_lines(company_id, account["gl_account_code"] or "", None, as_of, True)
        ot = L.outstanding_totals(outstanding)
        unmatched = self.get_statement_lines(statement_id, company_id, "unmatched")
        ut = L.statement_totals(unmatched)
        figures = L.reconcile(st["closing_balance"], ledger_bal, ot["deposits"], ot["payments"], ut["credit"], ut["debit"])
        existing = self.get_reconciliation(statement_id, company_id)
        status = existing["status"] if existing else "open"
        if status != "closed":
            status = "balanced" if figures["is_balanced"] else "open"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if existing:
                        cur.execute(
                            """UPDATE bank_reconciliations SET as_of=%s, statement_balance=%s, ledger_balance=%s,
                               outstanding_deposits=%s, outstanding_payments=%s, unexplained_difference=%s,
                               status=%s, updated_at=NOW() WHERE id=%s RETURNING *""",
                            (_iso(as_of), str(figures["statement_balance"]), str(figures["ledger_balance"]),
                             str(figures["outstanding_deposits"]), str(figures["outstanding_payments"]),
                             str(figures["difference"]), status, existing["id"]))
                    else:
                        cur.execute(
                            """INSERT INTO bank_reconciliations(id,company_id,bank_account_id,statement_id,as_of,
                               statement_balance,ledger_balance,outstanding_deposits,outstanding_payments,
                               unexplained_difference,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                            (str(uuid.uuid4()), company_id, st["bank_account_id"], statement_id, _iso(as_of),
                             str(figures["statement_balance"]), str(figures["ledger_balance"]),
                             str(figures["outstanding_deposits"]), str(figures["outstanding_payments"]),
                             str(figures["difference"]), status))
                    row = dict(cur.fetchone())
        except Exception as e:
            logger.error("refresh_reconciliation: %s", e)
            row = existing or {"id": None, "statement_id": statement_id, "status": status, "notes": "",
                               "closed_by": None, "closed_at": None}
        row.update(figures)
        row["outstanding_lines"] = outstanding
        row["unmatched_lines"] = unmatched
        row["adjustments"] = self.get_adjustments(row.get("id"), company_id) if row.get("id") else []
        return row

    def set_reconciliation_status(self, statement_id: str, company_id: str, status: str, by: str = "",
                                  notes: Optional[str] = None) -> Tuple[bool, str]:
        if status not in L.RECON_STATUSES:
            return False, "Invalid status"
        recon = self.refresh_reconciliation(statement_id, company_id)
        if not recon or not recon.get("id"):
            return False, "Reconciliation not found"
        if status == "closed" and not recon["is_balanced"] and not _str(notes if notes is not None else recon.get("notes")):
            return False, "Difference is not zero — add a note explaining it before closing"
        if status == "balanced" and not recon["is_balanced"]:
            return False, "Difference is not zero"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if status == "closed":
                        cur.execute("UPDATE bank_reconciliations SET status='closed', closed_by=%s, closed_at=NOW(), "
                                    "notes=COALESCE(%s, notes), updated_at=NOW() WHERE id=%s", (by, notes, recon["id"]))
                        cur.execute("UPDATE bank_statements SET status='reconciled' WHERE id=%s", (statement_id,))
                    else:
                        cur.execute("UPDATE bank_reconciliations SET status=%s, closed_by=NULL, closed_at=NULL, "
                                    "notes=COALESCE(%s, notes), updated_at=NOW() WHERE id=%s", (status, notes, recon["id"]))
                        cur.execute("UPDATE bank_statements SET status='in_progress' WHERE id=%s AND status='reconciled'", (statement_id,))
            return True, ""
        except Exception as e:
            logger.error("set_reconciliation_status: %s", e); return False, str(e)

    # ── Adjustments ──────────────────────────────────────────────
    def get_adjustments(self, reconciliation_id: Optional[str], company_id: str) -> List[dict]:
        if not reconciliation_id:
            return []
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_adjustments WHERE reconciliation_id=%s AND company_id=%s ORDER BY created_at",
                            (reconciliation_id, company_id))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_adjustments: %s", e); return []

    def create_adjustment(self, statement_id: str, company_id: str, data: dict, by: str = "") -> Tuple[Optional[dict], str]:
        """Record a bank-only item (charge, interest, …). Posts a balanced journal
        entry through the journal store when the GL is available; otherwise the
        adjustment is recorded alone and the caller flashes that."""
        kind = data.get("kind") if data.get("kind") in L.ADJUSTMENT_KINDS else "other"
        amount = abs(q2(data.get("amount")))
        if amount == 0:
            return None, "Amount must be greater than zero"
        st = self.get_statement(statement_id, company_id)
        if not st:
            return None, "Statement not found"
        recon = self.get_reconciliation(statement_id, company_id) or self.refresh_reconciliation(statement_id, company_id)
        if not recon or not recon.get("id"):
            return None, "Reconciliation could not be created"
        if recon.get("status") == "closed":
            return None, "Reconciliation is closed"
        line = None
        if _opt(data.get("statement_line_id")):
            line = self.get_statement_line(data["statement_line_id"], company_id)
            if not line or line["statement_id"] != statement_id:
                return None, "Statement line not found"
            if line["status"] != "unmatched":
                return None, "Statement line is already matched"
            # the line dictates the direction
            effect = "decrease" if q2(line["debit"]) > 0 else "increase"
            if kind in ("bank_charge", "interest") and L.adjustment_effect(kind) != effect:
                return None, "Adjustment kind does not match the direction of the statement line"
        else:
            effect = data.get("effect")
        signed = L.signed_adjustment_amount(kind, amount, effect)
        other_gl = _str(data.get("gl_account_code"))
        description = _str(data.get("description")) or (L.ADJUSTMENT_LABELS.get(kind, kind) +
                                                         (f" — {line['description']}" if line else ""))
        adj_date = L.parse_date(data.get("adj_date")) or (line["txn_date"] if line else st.get("period_end")) or date.today()
        adj_id = str(uuid.uuid4())
        journal_id = None
        posted_bank_line_id = None
        bank_gl = st.get("gl_account_code") or ""
        if bank_gl and other_gl and self.ledger_available():
            journal_id, posted_bank_line_id = self._post_adjustment_entry(
                company_id, kind, amount, bank_gl, other_gl, description, effect, adj_date, by, st)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO bank_adjustments(id,company_id,reconciliation_id,bank_account_id,statement_line_id,
                           kind,amount,gl_account_code,description,adj_date,journal_entry_id,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (adj_id, company_id, recon["id"], st["bank_account_id"], line["id"] if line else None,
                         kind, str(signed), other_gl, description[:500], adj_date.isoformat(), journal_id, by))
                    row = dict(cur.fetchone())
                    if line:
                        cur.execute(
                            """UPDATE bank_statement_lines SET status='matched', matched_kind='adjustment', matched_id=%s,
                               matched_at=NOW(), matched_by=%s WHERE id=%s""", (adj_id, by, line["id"]))
                    if posted_bank_line_id:
                        cur.execute(
                            """INSERT INTO bank_reconciled_ledger_lines(id,company_id,bank_account_id,entry_id,line_id,
                               statement_line_id,reconciled_by) VALUES(%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (line_id) DO NOTHING""",
                            (str(uuid.uuid4()), company_id, st["bank_account_id"], journal_id, posted_bank_line_id,
                             line["id"] if line else None, by))
                    cur.execute("UPDATE bank_statements SET status='in_progress' WHERE id=%s AND status='imported'", (statement_id,))
            row["posted"] = bool(journal_id)
            return row, ""
        except Exception as e:
            logger.error("create_adjustment: %s", e); return None, str(e)

    def _post_adjustment_entry(self, company_id, kind, amount, bank_gl, other_gl, description, effect, adj_date, by, st
                               ) -> Tuple[Optional[str], Optional[str]]:
        """Write the journal entry via the journal store's public method and return
        (entry_id, line_id of the bank-side line) — or (None, None) on failure."""
        try:
            with get_cursor() as cur:
                bank_name = self._account_name(cur, company_id, bank_gl)
                other_name = self._account_name(cur, company_id, other_gl)
            lines = L.adjustment_journal_lines(kind, amount, bank_gl, other_gl, description, effect, bank_name, other_name)
            if not lines:
                return None, None
            payload = [{"account_code": l["account_code"], "account_name": l["account_name"], "description": l["description"],
                        "debit_amount": float(l["debit_amount"]), "credit_amount": float(l["credit_amount"])} for l in lines]
            entry_id = str(uuid.uuid4())
            ref = f"BANK-{L.ADJUSTMENT_LABELS.get(kind, kind).upper().replace(' ', '-')[:12]}-{adj_date.strftime('%Y%m%d')}"
            from journal_entry_data_store import journal_store
            entry_id = journal_store.save_journal_entry(
                {"entry_id": entry_id, "company_id": company_id, "entry_date": adj_date.isoformat(),
                 "description": f"{description} ({st.get('bank_name') or ''} {st.get('account_number') or ''})".strip(),
                 "reference_number": ref, "total_debit": float(amount), "total_credit": float(amount),
                 "created_by": by or "bank-recon", "status": "posted"}, payload)
            with get_cursor() as cur:
                cur.execute("SELECT line_id FROM journal_entry_lines WHERE entry_id=%s AND account_code=%s ORDER BY line_number LIMIT 1",
                            (entry_id, bank_gl))
                r = cur.fetchone()
            return entry_id, (r["line_id"] if r else None)
        except Exception as e:
            logger.warning("adjustment journal entry not posted: %s", e)
            return None, None

    def delete_adjustment(self, adjustment_id: str, company_id: str) -> Tuple[bool, str]:
        """Remove an adjustment that was NOT posted to the ledger (posted ones need a reversing entry)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM bank_adjustments WHERE id=%s AND company_id=%s", (adjustment_id, company_id))
                    adj = cur.fetchone()
                    if not adj:
                        return False, "Adjustment not found"
                    if adj["journal_entry_id"]:
                        return False, "Adjustment was posted to the ledger — reverse the journal entry instead"
                    cur.execute("SELECT status FROM bank_reconciliations WHERE id=%s", (adj["reconciliation_id"],))
                    r = cur.fetchone()
                    if r and r["status"] == "closed":
                        return False, "Reconciliation is closed"
                    if adj["statement_line_id"]:
                        cur.execute("UPDATE bank_statement_lines SET status='unmatched', matched_kind=NULL, matched_id=NULL, "
                                    "matched_at=NULL, matched_by=NULL WHERE id=%s", (adj["statement_line_id"],))
                    cur.execute("DELETE FROM bank_adjustments WHERE id=%s", (adjustment_id,))
                    return True, ""
        except Exception as e:
            logger.error("delete_adjustment: %s", e); return False, str(e)

    # ── Reports ──────────────────────────────────────────────────
    def ageing(self, company_id: str, account_id: Optional[str] = None, as_of: Optional[date] = None) -> dict:
        as_of = as_of or date.today()
        stmt_items = self.unmatched_lines(company_id, account_id)
        accounts = [a for a in self.get_accounts(company_id, active_only=True) if not account_id or a["id"] == account_id]
        ledger_items: List[dict] = []
        for a in accounts:
            for ln in self.ledger_lines(company_id, a.get("gl_account_code") or "", None, as_of, True):
                ledger_items.append(dict(ln, account_name=a["name"], bank_name=a["bank_name"], txn_date=ln["entry_date"]))
        return {
            "as_of": as_of,
            "statement": L.age_items(stmt_items, as_of, "txn_date"),
            "ledger": L.age_items(ledger_items, as_of, "txn_date"),
            "statement_total": L.statement_totals(stmt_items),
            "ledger_total": L.outstanding_totals(ledger_items),
            "buckets": L.AGEING_BUCKETS,
        }

    def charges_summary(self, company_id: str, date_from: Any = None, date_to: Any = None) -> dict:
        out = {"rows": [], "by_kind": {}, "by_month": [], "total_charges": Decimal(0), "total_interest": Decimal(0),
               "total_other": Decimal(0), "count": 0}
        try:
            sql = """SELECT j.*, a.name AS account_name, a.bank_name FROM bank_adjustments j
                     JOIN bank_accounts a ON a.id=j.bank_account_id WHERE j.company_id=%s"""
            params: list = [company_id]
            f, t = _iso(date_from), _iso(date_to)
            if f:
                sql += " AND j.adj_date >= %s"; params.append(f)
            if t:
                sql += " AND j.adj_date <= %s"; params.append(t)
            sql += " ORDER BY j.adj_date DESC, j.created_at DESC"
            with get_cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("charges_summary: %s", e); rows = []
        by_kind: Dict[str, Dict[str, Any]] = {}
        by_month: Dict[str, Dict[str, Decimal]] = {}
        for r in rows:
            amt = q2(r["amount"])
            k = by_kind.setdefault(r["kind"], {"kind": r["kind"], "label": L.ADJUSTMENT_LABELS.get(r["kind"], r["kind"]),
                                                "count": 0, "amount": Decimal(0)})
            k["count"] += 1; k["amount"] = q2(k["amount"] + amt)
            m = (r["adj_date"].strftime("%Y-%m") if r.get("adj_date") else "—")
            mm = by_month.setdefault(m, {"month": m, "charges": Decimal(0), "interest": Decimal(0), "other": Decimal(0)})
            if r["kind"] == "bank_charge":
                mm["charges"] = q2(mm["charges"] + abs(amt)); out["total_charges"] = q2(out["total_charges"] + abs(amt))
            elif r["kind"] == "interest":
                mm["interest"] = q2(mm["interest"] + amt); out["total_interest"] = q2(out["total_interest"] + amt)
            else:
                mm["other"] = q2(mm["other"] + amt); out["total_other"] = q2(out["total_other"] + amt)
        out["rows"] = rows
        out["count"] = len(rows)
        out["by_kind"] = by_kind
        out["by_month"] = sorted(by_month.values(), key=lambda x: x["month"])
        return out

    def cash_position(self, company_id: str) -> dict:
        """Per bank account: ledger balance today vs. closing balance of the last statement."""
        accounts = self.get_accounts(company_id, active_only=True)
        rows = []
        total_ledger = total_bank = Decimal(0)
        for a in accounts:
            last = self._last_statement(a["id"], company_id)
            ledger_bal = self.ledger_balance(company_id, a, None)
            bank_bal = q2(last["closing_balance"]) if last else None
            unmatched = self._count_unmatched(a["id"], company_id)
            rows.append({
                "account": a, "ledger_balance": ledger_bal, "statement_balance": bank_bal,
                "statement_date": last["period_end"] if last else None,
                "statement_status": last["status"] if last else None,
                "difference": q2(bank_bal - ledger_bal) if bank_bal is not None else None,
                "unmatched": unmatched,
                "days_since": L.days_since(last["period_end"]) if last and last.get("period_end") else None,
            })
            total_ledger = q2(total_ledger + ledger_bal)
            if bank_bal is not None:
                total_bank = q2(total_bank + bank_bal)
        return {"rows": rows, "total_ledger": total_ledger, "total_bank": total_bank,
                "difference": q2(total_bank - total_ledger), "as_of": date.today()}

    def _last_statement(self, account_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_statements WHERE bank_account_id=%s AND company_id=%s "
                            "ORDER BY period_end DESC NULLS LAST, imported_at DESC LIMIT 1", (account_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("_last_statement: %s", e); return None

    def _count_unmatched(self, account_id: str, company_id: str) -> int:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM bank_statement_lines WHERE bank_account_id=%s AND company_id=%s "
                            "AND status='unmatched'", (account_id, company_id))
                return int(cur.fetchone()["c"])
        except Exception as e:
            logger.error("_count_unmatched: %s", e); return 0

    def _last_closed(self, account_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_reconciliations WHERE bank_account_id=%s AND company_id=%s AND status='closed' "
                            "ORDER BY as_of DESC NULLS LAST LIMIT 1", (account_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("_last_closed: %s", e); return None

    # ── Dashboard ────────────────────────────────────────────────
    @staticmethod
    def empty_dashboard() -> dict:
        return {"accounts": [], "account_count": 0, "statements": 0, "unmatched": 0, "open_recons": 0,
                "total_ledger": Decimal(0), "total_bank": Decimal(0), "difference": Decimal(0),
                "recent_statements": [], "ledger_available": True, "payments_available": False}

    def get_dashboard(self, company_id: str) -> dict:
        d = self.empty_dashboard()
        d["ledger_available"] = self.ledger_available()
        d["payments_available"] = self.payments_available()
        pos = self.cash_position(company_id)
        for r in pos["rows"]:
            a = r["account"]
            last_closed = self._last_closed(a["id"], company_id)
            open_recon = self._open_recon(a["id"], company_id)
            d["accounts"].append({
                "id": a["id"], "name": a["name"], "bank_name": a["bank_name"], "account_number": a["account_number"],
                "currency": a["currency"], "gl_account_code": a["gl_account_code"],
                "ledger_balance": r["ledger_balance"], "statement_balance": r["statement_balance"],
                "last_statement_date": r["statement_date"], "days_since_statement": r["days_since"],
                "last_reconciled": last_closed["as_of"] if last_closed else None,
                "days_since_reconciled": L.days_since(last_closed["as_of"]) if last_closed and last_closed.get("as_of") else None,
                "unmatched": r["unmatched"], "difference": r["difference"],
                "open_statement_id": open_recon["statement_id"] if open_recon else None,
            })
            d["unmatched"] += r["unmatched"]
            if open_recon:
                d["open_recons"] += 1
        d["account_count"] = len(d["accounts"])
        d["total_ledger"], d["total_bank"], d["difference"] = pos["total_ledger"], pos["total_bank"], pos["difference"]
        d["recent_statements"] = self.get_statements(company_id, None, 8)
        d["statements"] = self._count_statements(company_id)
        return d

    def _open_recon(self, account_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM bank_reconciliations WHERE bank_account_id=%s AND company_id=%s AND status<>'closed' "
                            "ORDER BY as_of DESC NULLS LAST LIMIT 1", (account_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("_open_recon: %s", e); return None

    def _count_statements(self, company_id: str) -> int:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM bank_statements WHERE company_id=%s", (company_id,))
                return int(cur.fetchone()["c"])
        except Exception as e:
            logger.error("_count_statements: %s", e); return 0


def _shift(d: Any, days: int):
    dd = L.parse_date(d)
    return (dd + timedelta(days=days)) if dd else None


bank_store = BankReconDataStore()


# ── Public API for other modules ─────────────────────────────────

def bank_balances(company_id: str) -> List[dict]:
    """[{account_id, name, bank_name, account_number, currency, ledger_balance,
    statement_balance, statement_date, difference}] for every active bank account."""
    try:
        pos = bank_store.cash_position(company_id)
        return [{"account_id": r["account"]["id"], "name": r["account"]["name"], "bank_name": r["account"]["bank_name"],
                 "account_number": r["account"]["account_number"], "currency": r["account"]["currency"],
                 "ledger_balance": r["ledger_balance"], "statement_balance": r["statement_balance"],
                 "statement_date": r["statement_date"], "difference": r["difference"]} for r in pos["rows"]]
    except Exception as e:
        logger.error("bank_balances: %s", e); return []


def unreconciled_count(company_id: str) -> int:
    """Number of unmatched statement lines across all bank accounts of the company."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM bank_statement_lines WHERE company_id=%s AND status='unmatched'", (company_id,))
            return int(cur.fetchone()["c"])
    except Exception as e:
        logger.error("unreconciled_count: %s", e); return 0
