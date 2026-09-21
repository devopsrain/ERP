"""
ERCA Tax Outputs — PostgreSQL data store.

Tables (all company-scoped):
  erca_company_profile   taxpayer header data (TIN, VAT reg no, tax centre …)
  erca_vat_returns       monthly VAT declarations computed from vat_income /
                         vat_expenses / vat_capital (read-only access to those)
  erca_withholding       withholding register (one row per payment)
  erca_invoice_series    gapless number series (invoice / receipt / credit note /
                         withholding receipt)
  erca_invoices          issued documents with sha256 hash chain
  erca_number_gaps_audit voided numbers — retained, never reused

Python API (also used by the routes):
  next_number(company_id, series_code)          allocate the next number
  issue_invoice(company_id, series_code, **data)
  compute_vat_return(company_id, year, month)
  withholding_for(gross, has_tin, transaction_type)  (re-exported from erca_forms)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from db import get_conn, get_cursor
import erca_forms as forms
from erca_forms import withholding_for  # noqa: F401  (public API re-export)

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS erca_company_profile (
    company_id        TEXT PRIMARY KEY DEFAULT 'default',
    tin               TEXT NOT NULL DEFAULT '',
    vat_reg_no        TEXT NOT NULL DEFAULT '',
    taxpayer_name     TEXT NOT NULL DEFAULT '',
    taxpayer_name_am  TEXT NOT NULL DEFAULT '',
    tax_centre        TEXT NOT NULL DEFAULT '',
    region            TEXT NOT NULL DEFAULT '',
    city              TEXT NOT NULL DEFAULT '',
    sub_city          TEXT NOT NULL DEFAULT '',
    woreda            TEXT NOT NULL DEFAULT '',
    house_no          TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT 'A',       -- A|B|C
    updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS erca_vat_returns (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    period_year     INT NOT NULL,
    period_month    INT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',    -- draft|finalized|filed
    lines           JSONB NOT NULL DEFAULT '{}'::jsonb,
    totals          JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    finalized_by    TEXT,
    finalized_at    TIMESTAMP,
    filed_on        DATE,
    erca_reference  TEXT,
    UNIQUE(company_id, period_year, period_month)
);
CREATE INDEX IF NOT EXISTS idx_erca_vat_returns_company ON erca_vat_returns(company_id);

CREATE TABLE IF NOT EXISTS erca_withholding (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    payee_name        TEXT NOT NULL DEFAULT '',
    payee_tin         TEXT NOT NULL DEFAULT '',
    has_tin           BOOLEAN NOT NULL DEFAULT TRUE,
    transaction_type  TEXT NOT NULL DEFAULT 'goods',  -- goods|services|import|other
    gross_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    withheld_rate     NUMERIC(6,4) NOT NULL DEFAULT 0,
    withheld_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    invoice_ref       TEXT NOT NULL DEFAULT '',
    receipt_no        TEXT,
    expense_id        TEXT,
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erca_withholding_company_date ON erca_withholding(company_id, payment_date);

CREATE TABLE IF NOT EXISTS erca_invoice_series (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    series_code   TEXT NOT NULL,
    prefix        TEXT NOT NULL DEFAULT '',
    next_number   BIGINT NOT NULL DEFAULT 1,
    pad           INT NOT NULL DEFAULT 6,
    kind          TEXT NOT NULL DEFAULT 'invoice',   -- invoice|receipt|credit_note|withholding_receipt
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    machine_id    TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(company_id, series_code)
);

CREATE TABLE IF NOT EXISTS erca_invoices (
    id                    TEXT PRIMARY KEY,
    company_id            TEXT NOT NULL DEFAULT 'default',
    series_id             TEXT NOT NULL,
    number                TEXT NOT NULL,
    kind                  TEXT NOT NULL DEFAULT 'invoice',
    issued_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    customer_name         TEXT NOT NULL DEFAULT '',
    customer_tin          TEXT NOT NULL DEFAULT '',
    items                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    subtotal              NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_amount            NUMERIC(18,2) NOT NULL DEFAULT 0,
    total                 NUMERIC(18,2) NOT NULL DEFAULT 0,
    withholding_expected  NUMERIC(18,2) NOT NULL DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'issued',   -- issued|voided
    void_reason           TEXT,
    voided_at             TIMESTAMP,
    linked_income_id      TEXT,
    created_by            TEXT NOT NULL DEFAULT '',
    created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
    hash                  TEXT NOT NULL DEFAULT '',
    UNIQUE(company_id, number)
);
CREATE INDEX IF NOT EXISTS idx_erca_invoices_company_issued ON erca_invoices(company_id, issued_at);
CREATE INDEX IF NOT EXISTS idx_erca_invoices_series ON erca_invoices(series_id, created_at);

CREATE TABLE IF NOT EXISTS erca_number_gaps_audit (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    series_id   TEXT NOT NULL,
    number      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

DEFAULT_SERIES = (
    ("INV", "INV-", "invoice"),
    ("RCT", "RCT-", "receipt"),
    ("CN",  "CN-",  "credit_note"),
    ("WHT", "WHT-", "withholding_receipt"),
)

PROFILE_FIELDS = ("tin", "vat_reg_no", "taxpayer_name", "taxpayer_name_am", "tax_centre",
                  "region", "city", "sub_city", "woreda", "house_no", "phone", "email", "category")


def _opt(v):
    """'' -> None (Postgres rejects '' for DATE/NUMERIC/BOOL)."""
    return None if v in ("", None) else v


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "y")


def _jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(str(type(o)))


def _dumps(o) -> str:
    return json.dumps(o, default=_jsonable)


def _row(r) -> Optional[dict]:
    if not r:
        return None
    d = dict(r)
    for k in ("lines", "totals", "items"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("erca schema ready")
    except Exception as e:
        logger.error("erca schema init failed: %s", e)


class ErcaDataStore:

    def __init__(self):
        self._columns_cache: Dict[str, set] = {}

    def ensure_schema(self):
        ensure_schema()

    # ── helpers ─────────────────────────────────────────────────────────
    def _table_columns(self, table_name: str) -> set:
        cols = self._columns_cache.get(table_name)
        if cols:
            return cols
        try:
            with get_cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                            (table_name,))
                cols = {r["column_name"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("_table_columns(%s): %s", table_name, e)
            cols = set()
        if cols:
            self._columns_cache[table_name] = cols
        return cols

    # ── company profile ─────────────────────────────────────────────────
    def get_profile(self, company_id: str) -> dict:
        base = {k: "" for k in PROFILE_FIELDS}
        base.update(company_id=company_id, category="A")
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM erca_company_profile WHERE company_id=%s", (company_id,))
                r = cur.fetchone()
                if r:
                    base.update(dict(r))
        except Exception as e:
            logger.error("get_profile: %s", e)
        return base

    def save_profile(self, company_id: str, data: dict) -> bool:
        vals = {k: _txt(data.get(k)) for k in PROFILE_FIELDS}
        vals["tin"] = forms.normalize_tin(vals["tin"])
        if vals["category"] not in forms.TAXPAYER_CATEGORIES:
            vals["category"] = "A"
        cols = ", ".join(PROFILE_FIELDS)
        sets = ", ".join(f"{k}=EXCLUDED.{k}" for k in PROFILE_FIELDS)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO erca_company_profile(company_id,{cols}) "
                        f"VALUES (%s,{','.join(['%s'] * len(PROFILE_FIELDS))}) "
                        f"ON CONFLICT (company_id) DO UPDATE SET {sets}, updated_at=NOW()",
                        [company_id] + [vals[k] for k in PROFILE_FIELDS])
            return True
        except Exception as e:
            logger.error("save_profile: %s", e)
            return False

    # ── VAT returns (computed from vat_income / vat_expenses / vat_capital) ──
    def _period_rows(self, cur, table: str, company_id: str, start: date, end: date) -> List[dict]:
        cols = self._table_columns(table)
        if not cols:
            return []
        if table == "vat_income":
            date_expr = "COALESCE(income_date, contract_date)" if "income_date" in cols else "contract_date"
        elif table == "vat_expenses":
            date_expr = "expense_date"
        else:
            date_expr = "investment_date"
        active = " AND is_active" if "is_active" in cols else ""
        cur.execute(f"SELECT * FROM {table} WHERE company_id=%s AND {date_expr}>=%s AND {date_expr}<=%s{active}",
                    (company_id, start, end))
        return [dict(r) for r in cur.fetchall()]

    def compute_vat_return(self, company_id: str, year: int, month: int, force: bool = False) -> Optional[dict]:
        """(Re)compute the draft return for a period. Finalized/filed returns are
        never overwritten unless force=True (which also resets them to draft)."""
        year, month = int(year), int(month)
        pb = forms.period_bounds(year, month)
        try:
            existing = self.get_vat_return_by_period(company_id, year, month)
            if existing and existing["status"] != "draft" and not force:
                return existing
            with get_conn() as conn:
                with conn.cursor() as cur:
                    inc = self._period_rows(cur, "vat_income", company_id, pb["start"], pb["end"])
                    exp = self._period_rows(cur, "vat_expenses", company_id, pb["start"], pb["end"])
                    cap = self._period_rows(cur, "vat_capital", company_id, pb["start"], pb["end"])
                    py, pm = forms.previous_period(year, month)
                    cur.execute("SELECT totals FROM erca_vat_returns WHERE company_id=%s AND period_year=%s AND period_month=%s",
                                (company_id, py, pm))
                    prev = _row(cur.fetchone())
                    cbf = (prev or {}).get("totals", {}).get("net_creditable", 0) if prev else 0
                    result = forms.build_vat_return_lines(inc, exp, cap, cbf)
                    result["totals"]["income_rows"] = len(inc)
                    result["totals"]["expense_rows"] = len(exp)
                    result["totals"]["capital_rows"] = len(cap)
                    rid = existing["id"] if existing else str(uuid.uuid4())
                    cur.execute(
                        """INSERT INTO erca_vat_returns(id,company_id,period_year,period_month,status,lines,totals,computed_at)
                           VALUES (%s,%s,%s,%s,'draft',%s::jsonb,%s::jsonb,NOW())
                           ON CONFLICT (company_id,period_year,period_month) DO UPDATE
                             SET lines=EXCLUDED.lines, totals=EXCLUDED.totals, computed_at=NOW(), status='draft',
                                 finalized_by=NULL, finalized_at=NULL, filed_on=NULL, erca_reference=NULL
                           RETURNING *""",
                        (rid, company_id, year, month, _dumps(result["lines"]), _dumps(result["totals"])))
                    return _row(cur.fetchone())
        except Exception as e:
            logger.error("compute_vat_return: %s", e)
            return None

    def get_vat_returns(self, company_id: str) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM erca_vat_returns WHERE company_id=%s ORDER BY period_year DESC, period_month DESC",
                            (company_id,))
                return [_row(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_vat_returns: %s", e)
            return []

    def get_vat_return(self, company_id: str, return_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM erca_vat_returns WHERE id=%s AND company_id=%s", (return_id, company_id))
                return _row(cur.fetchone())
        except Exception as e:
            logger.error("get_vat_return: %s", e)
            return None

    def get_vat_return_by_period(self, company_id: str, year: int, month: int) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM erca_vat_returns WHERE company_id=%s AND period_year=%s AND period_month=%s",
                            (company_id, int(year), int(month)))
                return _row(cur.fetchone())
        except Exception as e:
            logger.error("get_vat_return_by_period: %s", e)
            return None

    def finalize_vat_return(self, company_id: str, return_id: str, actor: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE erca_vat_returns SET status='finalized', finalized_by=%s, finalized_at=NOW()
                                   WHERE id=%s AND company_id=%s AND status='draft'""", (actor, return_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("finalize_vat_return: %s", e)
            return False

    def mark_vat_return_filed(self, company_id: str, return_id: str, filed_on, erca_reference: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE erca_vat_returns SET status='filed', filed_on=%s, erca_reference=%s
                                   WHERE id=%s AND company_id=%s AND status IN ('finalized','filed')""",
                                (_opt(filed_on) or date.today(), _txt(erca_reference), return_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("mark_vat_return_filed: %s", e)
            return False

    def reopen_vat_return(self, company_id: str, return_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE erca_vat_returns SET status='draft', finalized_by=NULL, finalized_at=NULL
                                   WHERE id=%s AND company_id=%s AND status='finalized'""", (return_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("reopen_vat_return: %s", e)
            return False

    # ── withholding register ────────────────────────────────────────────
    def get_withholding(self, company_id: str, year: int = None, month: int = None) -> List[dict]:
        try:
            sql = "SELECT * FROM erca_withholding WHERE company_id=%s"
            params: list = [company_id]
            if year and month:
                pb = forms.period_bounds(int(year), int(month))
                sql += " AND payment_date>=%s AND payment_date<=%s"
                params += [pb["start"], pb["end"]]
            sql += " ORDER BY payment_date DESC, created_at DESC"
            with get_cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_withholding: %s", e)
            return []

    def get_withholding_entry(self, company_id: str, wid: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM erca_withholding WHERE id=%s AND company_id=%s", (wid, company_id))
                r = cur.fetchone()
                return dict(r) if r else None
        except Exception as e:
            logger.error("get_withholding_entry: %s", e)
            return None

    def _withholding_values(self, data: dict) -> dict:
        tin = forms.normalize_tin(data.get("payee_tin"))
        has_tin = bool(tin) if data.get("has_tin") in (None, "") else _bool(data.get("has_tin"))
        if not tin:
            has_tin = False
        tt = _txt(data.get("transaction_type")).lower() or "goods"
        if tt not in forms.TRANSACTION_TYPES:
            tt = "other"
        calc = withholding_for(data.get("gross_amount"), has_tin, tt)
        rate = calc["rate"] if data.get("withheld_rate") in (None, "", "auto") else Decimal(str(data["withheld_rate"]))
        if rate > 1:
            rate = rate / Decimal(100)
        amount = forms.money(calc["gross"] * rate)
        return {
            "payee_name": _txt(data.get("payee_name")), "payee_tin": tin, "has_tin": has_tin,
            "transaction_type": tt, "gross_amount": calc["gross"], "withheld_rate": rate,
            "withheld_amount": amount,
            "payment_date": _opt(data.get("payment_date")) or date.today(),
            "invoice_ref": _txt(data.get("invoice_ref")), "expense_id": _opt(data.get("expense_id")),
        }

    def add_withholding(self, company_id: str, data: dict) -> Optional[dict]:
        v = self._withholding_values(data)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    receipt_no = None
                    if v["withheld_amount"] > 0:
                        receipt_no = self._allocate(cur, company_id, "WHT")
                    cur.execute(
                        """INSERT INTO erca_withholding(id,company_id,payee_name,payee_tin,has_tin,transaction_type,
                           gross_amount,withheld_rate,withheld_amount,payment_date,invoice_ref,receipt_no,expense_id,created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, v["payee_name"], v["payee_tin"], v["has_tin"],
                         v["transaction_type"], v["gross_amount"], v["withheld_rate"], v["withheld_amount"],
                         v["payment_date"], v["invoice_ref"], receipt_no, v["expense_id"], _txt(data.get("created_by"))))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("add_withholding: %s", e)
            return None

    def update_withholding(self, company_id: str, wid: str, data: dict) -> bool:
        v = self._withholding_values(data)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT receipt_no FROM erca_withholding WHERE id=%s AND company_id=%s FOR UPDATE",
                                (wid, company_id))
                    r = cur.fetchone()
                    if not r:
                        return False
                    receipt_no = r["receipt_no"]
                    if receipt_no is None and v["withheld_amount"] > 0:
                        receipt_no = self._allocate(cur, company_id, "WHT")
                    cur.execute(
                        """UPDATE erca_withholding SET payee_name=%s,payee_tin=%s,has_tin=%s,transaction_type=%s,
                           gross_amount=%s,withheld_rate=%s,withheld_amount=%s,payment_date=%s,invoice_ref=%s,
                           receipt_no=%s,expense_id=%s WHERE id=%s AND company_id=%s""",
                        (v["payee_name"], v["payee_tin"], v["has_tin"], v["transaction_type"], v["gross_amount"],
                         v["withheld_rate"], v["withheld_amount"], v["payment_date"], v["invoice_ref"],
                         receipt_no, v["expense_id"], wid, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_withholding: %s", e)
            return False

    def delete_withholding(self, company_id: str, wid: str, actor: str = "") -> bool:
        """Deleting keeps the receipt number in the gaps audit (never reused)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM erca_withholding WHERE id=%s AND company_id=%s RETURNING receipt_no",
                                (wid, company_id))
                    r = cur.fetchone()
                    if not r:
                        return False
                    if r["receipt_no"]:
                        cur.execute("SELECT id FROM erca_invoice_series WHERE company_id=%s AND series_code='WHT'", (company_id,))
                        s = cur.fetchone()
                        cur.execute("INSERT INTO erca_number_gaps_audit(id,company_id,series_id,number,reason,actor) VALUES (%s,%s,%s,%s,%s,%s)",
                                    (str(uuid.uuid4()), company_id, s["id"] if s else "", r["receipt_no"],
                                     "withholding entry deleted", actor))
                    return True
        except Exception as e:
            logger.error("delete_withholding: %s", e)
            return False

    def withholding_month_summary(self, company_id: str, year: int, month: int) -> dict:
        return forms.withholding_summary(self.get_withholding(company_id, year, month))

    # ── invoice series ──────────────────────────────────────────────────
    def ensure_default_series(self, company_id: str) -> None:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for code, prefix, kind in DEFAULT_SERIES:
                        cur.execute(
                            """INSERT INTO erca_invoice_series(id,company_id,series_code,prefix,kind)
                               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (company_id,series_code) DO NOTHING""",
                            (str(uuid.uuid4()), company_id, code, prefix, kind))
        except Exception as e:
            logger.error("ensure_default_series: %s", e)

    def get_series(self, company_id: str, active_only: bool = False) -> List[dict]:
        try:
            sql = "SELECT * FROM erca_invoice_series WHERE company_id=%s"
            if active_only:
                sql += " AND is_active"
            sql += " ORDER BY kind, series_code"
            with get_cursor() as cur:
                cur.execute(sql, (company_id,))
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["next_formatted"] = forms.format_number(r["prefix"], r["next_number"], r["pad"])
            return rows
        except Exception as e:
            logger.error("get_series: %s", e)
            return []

    def get_series_one(self, company_id: str, series_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM erca_invoice_series WHERE id=%s AND company_id=%s", (series_id, company_id))
                r = cur.fetchone()
                if not r:
                    return None
                d = dict(r)
                d["next_formatted"] = forms.format_number(d["prefix"], d["next_number"], d["pad"])
                return d
        except Exception as e:
            logger.error("get_series_one: %s", e)
            return None

    def create_series(self, company_id: str, data: dict) -> Optional[dict]:
        code = _txt(data.get("series_code")).upper()
        kind = _txt(data.get("kind")) or "invoice"
        if not code or kind not in forms.INVOICE_KINDS:
            return None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO erca_invoice_series(id,company_id,series_code,prefix,next_number,pad,kind,is_active,machine_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, code, _txt(data.get("prefix")),
                         int(_opt(data.get("next_number")) or 1), int(_opt(data.get("pad")) or 6), kind,
                         _bool(data.get("is_active", True)), _opt(_txt(data.get("machine_id")))))
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_series: %s", e)
            return None

    def update_series(self, company_id: str, series_id: str, data: dict) -> bool:
        """Prefix/pad/machine/active are editable. next_number may only move
        FORWARD (never backwards — that would re-issue numbers)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT next_number FROM erca_invoice_series WHERE id=%s AND company_id=%s FOR UPDATE",
                                (series_id, company_id))
                    r = cur.fetchone()
                    if not r:
                        return False
                    nn = int(_opt(data.get("next_number")) or r["next_number"])
                    if nn < r["next_number"]:
                        nn = r["next_number"]
                    cur.execute(
                        """UPDATE erca_invoice_series SET prefix=%s,pad=%s,machine_id=%s,is_active=%s,next_number=%s
                           WHERE id=%s AND company_id=%s""",
                        (_txt(data.get("prefix")), int(_opt(data.get("pad")) or 6),
                         _opt(_txt(data.get("machine_id"))), _bool(data.get("is_active")), nn, series_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("update_series: %s", e)
            return False

    def toggle_series(self, company_id: str, series_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE erca_invoice_series SET is_active=NOT is_active WHERE id=%s AND company_id=%s",
                                (series_id, company_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("toggle_series: %s", e)
            return False

    # ── numbering ───────────────────────────────────────────────────────
    def _allocate(self, cur, company_id: str, series_code: str) -> str:
        """Gapless, concurrency-safe allocation. The UPDATE takes a row lock on
        the series so concurrent callers serialise; it MUST run inside the same
        transaction as the INSERT that consumes the number."""
        cur.execute(
            """UPDATE erca_invoice_series SET next_number = next_number + 1
               WHERE company_id=%s AND series_code=%s AND is_active
               RETURNING next_number - 1 AS n, prefix, pad, id, kind""",
            (company_id, series_code.upper()))
        r = cur.fetchone()
        if not r:
            raise ValueError(f"No active series '{series_code}' for company {company_id}")
        return forms.format_number(r["prefix"], r["n"], r["pad"])

    def next_number(self, company_id: str, series_code: str, conn=None) -> Optional[str]:
        """Allocate and return the next number. Pass an open `conn` to allocate
        inside your own transaction (recommended — a number allocated without
        being persisted becomes a gap that must be explained to ERCA)."""
        try:
            if conn is not None:
                with conn.cursor() as cur:
                    return self._allocate(cur, company_id, series_code)
            with get_conn() as c:
                with c.cursor() as cur:
                    return self._allocate(cur, company_id, series_code)
        except Exception as e:
            logger.error("next_number: %s", e)
            return None

    def peek_next_number(self, company_id: str, series_code: str) -> Optional[str]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT prefix,next_number,pad FROM erca_invoice_series WHERE company_id=%s AND series_code=%s",
                            (company_id, series_code.upper()))
                r = cur.fetchone()
                return forms.format_number(r["prefix"], r["next_number"], r["pad"]) if r else None
        except Exception as e:
            logger.error("peek_next_number: %s", e)
            return None

    # ── invoices ────────────────────────────────────────────────────────
    def issue_invoice(self, company_id: str, series_code: str, **data) -> Optional[dict]:
        """Allocate a number, compute totals + hash chain and insert — atomically.

        data: customer_name, customer_tin, items [{description, qty, unit_price, vat_rate}],
              issued_at, linked_income_id, created_by, withholding_expected (optional,
              auto-computed from the subtotal when omitted)
        """
        items = forms.normalize_items(data.get("items") or [])
        if not items:
            logger.error("issue_invoice: no line items")
            return None
        tot = forms.invoice_totals(items)
        tin = forms.normalize_tin(data.get("customer_tin"))
        wht = data.get("withholding_expected")
        if wht in (None, "", "auto"):
            # what the customer is expected to withhold from us on this payment
            wht = withholding_for(tot["subtotal"], True, data.get("withholding_type") or "goods")["amount"]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, kind FROM erca_invoice_series WHERE company_id=%s AND series_code=%s AND is_active",
                                (company_id, series_code.upper()))
                    s = cur.fetchone()
                    if not s:
                        raise ValueError(f"No active series '{series_code}'")
                    number = self._allocate(cur, company_id, series_code)      # locks the series row
                    cur.execute("SELECT hash FROM erca_invoices WHERE series_id=%s ORDER BY created_at DESC, number DESC LIMIT 1",
                                (s["id"],))
                    prev = cur.fetchone()
                    issued_at = _opt(data.get("issued_at")) or datetime.now()
                    if isinstance(issued_at, str):
                        issued_at = datetime.fromisoformat(issued_at)
                    inv = {
                        "number": number, "kind": s["kind"], "issued_at": issued_at,
                        "customer_name": _txt(data.get("customer_name")), "customer_tin": tin,
                        "items": items, "subtotal": tot["subtotal"], "vat_amount": tot["vat_amount"], "total": tot["total"],
                    }
                    h = forms.chain_hash(prev["hash"] if prev else None, forms.invoice_hash_payload(inv))
                    cur.execute(
                        """INSERT INTO erca_invoices(id,company_id,series_id,number,kind,issued_at,customer_name,customer_tin,
                           items,subtotal,vat_amount,total,withholding_expected,status,linked_income_id,created_by,hash)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,'issued',%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, s["id"], number, s["kind"], issued_at, inv["customer_name"], tin,
                         _dumps(items), tot["subtotal"], tot["vat_amount"], tot["total"], forms.money(wht),
                         _opt(data.get("linked_income_id")), _txt(data.get("created_by")), h))
                    return _row(cur.fetchone())
        except Exception as e:
            logger.error("issue_invoice: %s", e)
            return None

    def get_invoices(self, company_id: str, kind: str = None, series_id: str = None, status: str = None,
                     start: date = None, end: date = None, limit: int = 500) -> List[dict]:
        try:
            sql = ("SELECT i.*, s.series_code, s.machine_id FROM erca_invoices i "
                   "LEFT JOIN erca_invoice_series s ON s.id=i.series_id WHERE i.company_id=%s")
            params: list = [company_id]
            for col, val in (("i.kind", kind), ("i.series_id", series_id), ("i.status", status)):
                if val:
                    sql += f" AND {col}=%s"
                    params.append(val)
            if start:
                sql += " AND i.issued_at >= %s"; params.append(start)
            if end:
                sql += " AND i.issued_at < %s::date + INTERVAL '1 day'"; params.append(end)
            sql += " ORDER BY i.issued_at DESC, i.number DESC LIMIT %s"
            params.append(int(limit))
            with get_cursor() as cur:
                cur.execute(sql, params)
                return [_row(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_invoices: %s", e)
            return []

    def get_invoice(self, company_id: str, invoice_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("""SELECT i.*, s.series_code, s.machine_id FROM erca_invoices i
                               LEFT JOIN erca_invoice_series s ON s.id=i.series_id
                               WHERE i.id=%s AND i.company_id=%s""", (invoice_id, company_id))
                return _row(cur.fetchone())
        except Exception as e:
            logger.error("get_invoice: %s", e)
            return None

    def void_invoice(self, company_id: str, invoice_id: str, reason: str, actor: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE erca_invoices SET status='voided', void_reason=%s, voided_at=NOW()
                                   WHERE id=%s AND company_id=%s AND status='issued' RETURNING series_id, number""",
                                (_txt(reason) or "voided", invoice_id, company_id))
                    r = cur.fetchone()
                    if not r:
                        return False
                    cur.execute("INSERT INTO erca_number_gaps_audit(id,company_id,series_id,number,reason,actor) VALUES (%s,%s,%s,%s,%s,%s)",
                                (str(uuid.uuid4()), company_id, r["series_id"], r["number"], _txt(reason) or "voided", actor))
                    return True
        except Exception as e:
            logger.error("void_invoice: %s", e)
            return False

    def get_gaps_audit(self, company_id: str, limit: int = 200) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("""SELECT g.*, s.series_code FROM erca_number_gaps_audit g
                               LEFT JOIN erca_invoice_series s ON s.id=g.series_id
                               WHERE g.company_id=%s ORDER BY g.created_at DESC LIMIT %s""", (company_id, limit))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_gaps_audit: %s", e)
            return []

    def audit_export(self, company_id: str, start: date, end: date) -> dict:
        """All invoices in range (chronological per series) + chain verification
        + voided numbers, ready for Excel/JSON output."""
        invoices = self.get_invoices(company_id, start=start, end=end, limit=100000)
        invoices.sort(key=lambda i: (i.get("series_id") or "", i.get("created_at") or datetime.min, i.get("number") or ""))
        chain = forms.verify_chain(invoices) if invoices else {"ok": True, "checked": 0, "broken": []}
        gaps = [g for g in self.get_gaps_audit(company_id, 100000)
                if not start or not g.get("created_at") or g["created_at"].date() >= start]
        return {"company_id": company_id, "range": {"start": start, "end": end},
                "generated_at": datetime.now(), "invoice_count": len(invoices),
                "chain": chain, "invoices": invoices, "voided": gaps, "profile": self.get_profile(company_id)}

    # ── dashboard ───────────────────────────────────────────────────────
    def dashboard(self, company_id: str, today: date = None) -> dict:
        today = today or date.today()
        y, m = forms.current_period(today)
        pb = forms.period_bounds(y, m)
        out = {"period": pb, "position": None, "unfiled": [], "withholding": forms.withholding_summary([]),
               "invoice_counts": {}, "series": [], "recent_invoices": [], "profile": self.get_profile(company_id)}
        try:
            rets = self.get_vat_returns(company_id)
            filed = {(r["period_year"], r["period_month"]) for r in rets if r["status"] == "filed"}
            out["unfiled"] = forms.unfiled_periods(filed, today, 6)
            cur_ret = next((r for r in rets if (r["period_year"], r["period_month"]) == (y, m)), None)
            out["position"] = cur_ret
            out["withholding"] = self.withholding_month_summary(company_id, today.year, today.month)
            with get_cursor() as cur:
                cur.execute("SELECT kind, status, COUNT(*) AS c FROM erca_invoices WHERE company_id=%s GROUP BY kind, status",
                            (company_id,))
                counts: Dict[str, dict] = {}
                for r in cur.fetchall():
                    counts.setdefault(r["kind"], {"issued": 0, "voided": 0})[r["status"]] = int(r["c"])
                out["invoice_counts"] = counts
            out["series"] = self.get_series(company_id, active_only=True)
            out["recent_invoices"] = self.get_invoices(company_id, limit=8)
        except Exception as e:
            logger.error("dashboard: %s", e)
        return out


erca_store = ErcaDataStore()


# ── module-level Python API ─────────────────────────────────────────────────
def next_number(company_id: str, series_code: str, conn=None) -> Optional[str]:
    return erca_store.next_number(company_id, series_code, conn=conn)


def issue_invoice(company_id: str, series_code: str, **data) -> Optional[dict]:
    return erca_store.issue_invoice(company_id, series_code, **data)


def compute_vat_return(company_id: str, year: int, month: int) -> Optional[dict]:
    return erca_store.compute_vat_return(company_id, year, month)
