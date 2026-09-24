"""
Commercial (Sales Order & Marketing) data store — PostgreSQL backend.

Tables (all carry ``company_id TEXT NOT NULL DEFAULT 'default'``):

  master data   commercial_customers, commercial_products, commercial_price_lists,
                commercial_price_list_items, commercial_discounts, commercial_territories,
                commercial_sales_reps, commercial_settings, commercial_sequences
  documents     commercial_proformas(+_lines), commercial_sales_orders(+_so_lines,
                _so_approvals, _reservations), commercial_mo_requests,
                commercial_delivery_instructions(+_di_lines), commercial_dispatches,
                commercial_invoices(+_lines), commercial_receipts, commercial_returns(+_lines)
  analytics     commercial_forecasts, commercial_commissions
  marketing     commercial_campaigns, commercial_marketing_events, commercial_leads,
                commercial_competitors
  audit         commercial_events

Public Python API (safe to call from other modules — never raises):

  sales_order_by_no(company_id, so_no)              -> dict | None (header + lines + approvals)
  customer_balance(company_id, customer_id)         -> Decimal (invoiced − paid − credited)
  record_payment(company_id, invoice_no, amount, method, reference, actor="") -> receipt dict | None
  open_orders_summary(company_id)                   -> {count, value, by_status:{...}, overdue_deliveries}

Document numbers are gapless per company and calendar year and are allocated
with ``UPDATE commercial_sequences ... RETURNING`` inside the same transaction
as the INSERT that consumes them (PFI-2026-000001, SO-2026-000001, ...).

Integration with sibling modules (manufacturing, quality, ERCA, VAT, inventory,
payments, approvals) is via lazy imports inside try/except and
``information_schema``-guarded queries, so this module works stand-alone.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from db import get_conn

import commercial_logic as L
from commercial_logic import D, q2, q3, opt, to_int, as_bool

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commercial_sequences (
    company_id  TEXT NOT NULL DEFAULT 'default',
    doc_type    TEXT NOT NULL,
    year        INT  NOT NULL,
    next_number INT  NOT NULL DEFAULT 1,
    PRIMARY KEY (company_id, doc_type, year)
);

CREATE TABLE IF NOT EXISTS commercial_settings (
    company_id            TEXT PRIMARY KEY,
    so_approvers          JSONB NOT NULL DEFAULT '[]'::jsonb,
    default_price_list_id TEXT,
    header_info           TEXT NOT NULL DEFAULT '',
    footer_info           TEXT NOT NULL DEFAULT '',
    iso_doc_no            TEXT NOT NULL DEFAULT '',
    invoice_series_code   TEXT NOT NULL DEFAULT 'INV',
    allow_over_credit     BOOLEAN NOT NULL DEFAULT FALSE,
    default_vat_rate      NUMERIC(6,4) NOT NULL DEFAULT 0.15,
    default_credit_days   INT NOT NULL DEFAULT 30,
    updated_at            TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commercial_territories (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_territories_company ON commercial_territories(company_id);

CREATE TABLE IF NOT EXISTS commercial_sales_reps (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL DEFAULT 'default',
    name           TEXT NOT NULL,
    username       TEXT,
    territory_id   TEXT,
    commission_pct NUMERIC(6,2) NOT NULL DEFAULT 0,
    phone          TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_reps_company ON commercial_sales_reps(company_id);

CREATE TABLE IF NOT EXISTS commercial_customers (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    code              TEXT NOT NULL DEFAULT '',
    name              TEXT NOT NULL,
    tin               TEXT NOT NULL DEFAULT '',
    contact_person    TEXT NOT NULL DEFAULT '',
    phone             TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    address           TEXT NOT NULL DEFAULT '',
    region            TEXT NOT NULL DEFAULT '',
    territory_id      TEXT,
    sales_rep_id      TEXT,
    segment           TEXT NOT NULL DEFAULT 'other',
    credit_limit      NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit_terms_days INT NOT NULL DEFAULT 0,
    portal_user_id    TEXT,
    notes             TEXT NOT NULL DEFAULT '',
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_customers_company ON commercial_customers(company_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_customers_code ON commercial_customers(company_id, code) WHERE code <> '';

CREATE TABLE IF NOT EXISTS commercial_products (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL DEFAULT 'default',
    item_code    TEXT NOT NULL,
    product_code TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    size_mm2     TEXT NOT NULL DEFAULT '',
    color        TEXT NOT NULL DEFAULT '',
    unit         TEXT NOT NULL DEFAULT 'm',
    packaging    TEXT NOT NULL DEFAULT '',
    list_price   NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'ETB',
    vat_rate     NUMERIC(6,4) NOT NULL DEFAULT 0.15,
    category     TEXT NOT NULL DEFAULT '',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, item_code)
);

CREATE TABLE IF NOT EXISTS commercial_price_lists (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    valid_from  DATE,
    valid_to    DATE,
    currency    TEXT NOT NULL DEFAULT 'ETB',
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_pl_company ON commercial_price_lists(company_id);

CREATE TABLE IF NOT EXISTS commercial_price_list_items (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    price_list_id TEXT NOT NULL,
    item_code     TEXT NOT NULL,
    unit_price    NUMERIC(18,2) NOT NULL DEFAULT 0,
    min_qty       NUMERIC(18,3) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comm_pli_list ON commercial_price_list_items(price_list_id);

CREATE TABLE IF NOT EXISTS commercial_discounts (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'percent',      -- percent|amount
    value           NUMERIC(18,2) NOT NULL DEFAULT 0,
    applies_to      TEXT NOT NULL DEFAULT 'order',        -- customer|segment|product|order
    target          TEXT NOT NULL DEFAULT '',
    min_order_value NUMERIC(18,2) NOT NULL DEFAULT 0,
    valid_from      DATE,
    valid_to        DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_discounts_company ON commercial_discounts(company_id);

CREATE TABLE IF NOT EXISTS commercial_proformas (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL DEFAULT 'default',
    proforma_no    TEXT NOT NULL,
    customer_id    TEXT,
    customer_name  TEXT NOT NULL DEFAULT '',
    customer_tin   TEXT NOT NULL DEFAULT '',
    proforma_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_until    DATE,
    payment_method TEXT NOT NULL DEFAULT '',
    payment_info   TEXT NOT NULL DEFAULT '',
    currency       TEXT NOT NULL DEFAULT 'ETB',
    subtotal       NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount_total NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_total      NUMERIC(18,2) NOT NULL DEFAULT 0,
    grand_total    NUMERIC(18,2) NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'draft',   -- draft|sent|accepted|expired|converted
    sales_order_id TEXT,
    notes          TEXT NOT NULL DEFAULT '',
    prepared_by    TEXT NOT NULL DEFAULT '',
    checked_by     TEXT NOT NULL DEFAULT '',
    approved_by    TEXT NOT NULL DEFAULT '',
    header_info    TEXT NOT NULL DEFAULT '',
    footer_info    TEXT NOT NULL DEFAULT '',
    iso_doc_no     TEXT NOT NULL DEFAULT '',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, proforma_no)
);

CREATE TABLE IF NOT EXISTS commercial_proforma_lines (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    proforma_id TEXT NOT NULL,
    line_no     INT NOT NULL DEFAULT 1,
    ref_no      TEXT NOT NULL DEFAULT '',
    item_code   TEXT NOT NULL DEFAULT '',
    size        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '',
    unit        TEXT NOT NULL DEFAULT '',
    quantity    NUMERIC(18,3) NOT NULL DEFAULT 0,
    unit_price  NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    line_total  NUMERIC(18,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comm_pfl_doc ON commercial_proforma_lines(proforma_id);

CREATE TABLE IF NOT EXISTS commercial_sales_orders (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL DEFAULT 'default',
    so_no               TEXT NOT NULL,
    customer_id         TEXT,
    customer_name       TEXT NOT NULL DEFAULT '',
    proforma_id         TEXT,
    order_date          DATE NOT NULL DEFAULT CURRENT_DATE,
    required_date       DATE,
    status              TEXT NOT NULL DEFAULT 'draft',
    currency            TEXT NOT NULL DEFAULT 'ETB',
    subtotal            NUMERIC(18,2) NOT NULL DEFAULT 0,
    order_discount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount_total      NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_rate            NUMERIC(6,4) NOT NULL DEFAULT 0.15,
    vat_total           NUMERIC(18,2) NOT NULL DEFAULT 0,
    grand_total         NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit_sale         BOOLEAN NOT NULL DEFAULT FALSE,
    payment_terms       TEXT NOT NULL DEFAULT '',
    sales_rep_id        TEXT,
    territory_id        TEXT,
    prepared_by         TEXT NOT NULL DEFAULT '',
    checked_by          TEXT NOT NULL DEFAULT '',
    approved_by         TEXT NOT NULL DEFAULT '',
    verification_note   TEXT NOT NULL DEFAULT '',
    starting_reading    NUMERIC(18,3),
    ending_reading      NUMERIC(18,3),
    difference_reading  NUMERIC(18,3),
    copy_finance        BOOLEAN NOT NULL DEFAULT TRUE,
    copy_sales          BOOLEAN NOT NULL DEFAULT TRUE,
    copy_first          BOOLEAN NOT NULL DEFAULT TRUE,
    header_info         TEXT NOT NULL DEFAULT '',
    footer_info         TEXT NOT NULL DEFAULT '',
    iso_doc_no          TEXT NOT NULL DEFAULT '',
    approval_request_id TEXT,
    credit_override_by  TEXT NOT NULL DEFAULT '',
    notes               TEXT NOT NULL DEFAULT '',
    submitted_at        TIMESTAMP,
    approved_at         TIMESTAMP,
    closed_at           TIMESTAMP,
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, so_no)
);
CREATE INDEX IF NOT EXISTS idx_comm_so_company_status ON commercial_sales_orders(company_id, status);
CREATE INDEX IF NOT EXISTS idx_comm_so_customer ON commercial_sales_orders(customer_id);

CREATE TABLE IF NOT EXISTS commercial_so_lines (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    so_id         TEXT NOT NULL,
    line_no       INT NOT NULL DEFAULT 1,
    item_code     TEXT NOT NULL DEFAULT '',
    product_code  TEXT NOT NULL DEFAULT '',
    size          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    color         TEXT NOT NULL DEFAULT '',
    unit          TEXT NOT NULL DEFAULT '',
    packaging     TEXT NOT NULL DEFAULT '',
    quantity      NUMERIC(18,3) NOT NULL DEFAULT 0,
    unit_price    NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    line_total    NUMERIC(18,2) NOT NULL DEFAULT 0,
    reserved_qty  NUMERIC(18,3) NOT NULL DEFAULT 0,
    delivered_qty NUMERIC(18,3) NOT NULL DEFAULT 0,
    invoiced_qty  NUMERIC(18,3) NOT NULL DEFAULT 0,
    mo_no         TEXT NOT NULL DEFAULT '',
    mo_request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_comm_so_lines_so ON commercial_so_lines(so_id);

CREATE TABLE IF NOT EXISTS commercial_so_approvals (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL DEFAULT 'default',
    so_id      TEXT NOT NULL,
    seq        INT NOT NULL,
    role_label TEXT NOT NULL DEFAULT '',
    approver   TEXT NOT NULL DEFAULT '',        -- designated username or role
    decided_by TEXT NOT NULL DEFAULT '',
    decision   TEXT NOT NULL DEFAULT '',        -- ''|approved|rejected
    decided_at TIMESTAMP,
    comment    TEXT NOT NULL DEFAULT '',
    UNIQUE (so_id, seq)
);

CREATE TABLE IF NOT EXISTS commercial_reservations (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    so_id       TEXT NOT NULL,
    so_line_id  TEXT NOT NULL,
    item_code   TEXT NOT NULL DEFAULT '',
    quantity    NUMERIC(18,3) NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',   -- active|released|consumed
    reserved_by TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    released_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_comm_res_item ON commercial_reservations(company_id, item_code, status);

CREATE TABLE IF NOT EXISTS commercial_mo_requests (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL DEFAULT 'default',
    request_no       TEXT NOT NULL,
    so_id            TEXT,
    so_line_id       TEXT,
    so_no            TEXT NOT NULL DEFAULT '',
    customer_name    TEXT NOT NULL DEFAULT '',
    product_code     TEXT NOT NULL DEFAULT '',
    item_code        TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    size             TEXT NOT NULL DEFAULT '',
    color            TEXT NOT NULL DEFAULT '',
    unit             TEXT NOT NULL DEFAULT '',
    quantity         NUMERIC(18,3) NOT NULL DEFAULT 0,
    packing          TEXT NOT NULL DEFAULT '',
    cutting_length   TEXT NOT NULL DEFAULT '',
    delivery_date    DATE,
    comment          TEXT NOT NULL DEFAULT '',
    to_factory       TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'requested',   -- requested|sent|accepted|in_production|completed|cancelled
    mo_no            TEXT NOT NULL DEFAULT '',
    mfg_order_id     TEXT,
    prepared_by      TEXT NOT NULL DEFAULT '',
    checked_by       TEXT NOT NULL DEFAULT '',
    approved_by      TEXT NOT NULL DEFAULT '',
    manager          TEXT NOT NULL DEFAULT '',
    header_info      TEXT NOT NULL DEFAULT '',
    footer_info      TEXT NOT NULL DEFAULT '',
    iso_doc_no       TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, request_no)
);
CREATE INDEX IF NOT EXISTS idx_comm_mor_so ON commercial_mo_requests(so_id);

CREATE TABLE IF NOT EXISTS commercial_delivery_instructions (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    di_no         TEXT NOT NULL,
    so_id         TEXT,
    so_no         TEXT NOT NULL DEFAULT '',
    customer_id   TEXT,
    customer_name TEXT NOT NULL DEFAULT '',
    di_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    deliver_to    TEXT NOT NULL DEFAULT '',
    warehouse     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',     -- open|dispatched|completed|cancelled
    notes         TEXT NOT NULL DEFAULT '',
    prepared_by   TEXT NOT NULL DEFAULT '',
    checked_by    TEXT NOT NULL DEFAULT '',
    approved_by   TEXT NOT NULL DEFAULT '',
    header_info   TEXT NOT NULL DEFAULT '',
    footer_info   TEXT NOT NULL DEFAULT '',
    iso_doc_no    TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, di_no)
);
CREATE INDEX IF NOT EXISTS idx_comm_di_so ON commercial_delivery_instructions(so_id);

CREATE TABLE IF NOT EXISTS commercial_di_lines (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL DEFAULT 'default',
    di_id        TEXT NOT NULL,
    so_line_id   TEXT,
    line_no      INT NOT NULL DEFAULT 1,
    product_code TEXT NOT NULL DEFAULT '',
    item_code    TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    size         TEXT NOT NULL DEFAULT '',
    color        TEXT NOT NULL DEFAULT '',
    unit         TEXT NOT NULL DEFAULT '',
    packaging    TEXT NOT NULL DEFAULT '',
    quantity     NUMERIC(18,3) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comm_di_lines_di ON commercial_di_lines(di_id);

CREATE TABLE IF NOT EXISTS commercial_dispatches (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    dispatch_no   TEXT NOT NULL,
    di_id         TEXT NOT NULL,
    so_id         TEXT,
    vehicle       TEXT NOT NULL DEFAULT '',
    driver        TEXT NOT NULL DEFAULT '',
    driver_phone  TEXT NOT NULL DEFAULT '',
    dispatched_at TIMESTAMP,
    received_by   TEXT NOT NULL DEFAULT '',
    delivered_at  TIMESTAMP,
    accepted_at   TIMESTAMP,
    acceptance_note TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'planned',   -- planned|dispatched|delivered|accepted|rejected
    notes         TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, dispatch_no)
);
CREATE INDEX IF NOT EXISTS idx_comm_dispatch_di ON commercial_dispatches(di_id);

CREATE TABLE IF NOT EXISTS commercial_invoices (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    invoice_no      TEXT NOT NULL,
    so_id           TEXT,
    so_no           TEXT NOT NULL DEFAULT '',
    customer_id     TEXT,
    customer_name   TEXT NOT NULL DEFAULT '',
    customer_tin    TEXT NOT NULL DEFAULT '',
    invoice_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    due_date        DATE,
    currency        TEXT NOT NULL DEFAULT 'ETB',
    subtotal        NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount_total  NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_rate        NUMERIC(6,4) NOT NULL DEFAULT 0.15,
    vat_total       NUMERIC(18,2) NOT NULL DEFAULT 0,
    grand_total     NUMERIC(18,2) NOT NULL DEFAULT 0,
    paid_total      NUMERIC(18,2) NOT NULL DEFAULT 0,
    credited_total  NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit_sale     BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'issued',  -- issued|partially_paid|paid|overdue|cancelled
    sales_rep_id    TEXT,
    territory_id    TEXT,
    vat_income_id   TEXT,
    erca_invoice_id TEXT,
    erca_number     TEXT NOT NULL DEFAULT '',
    prepared_by     TEXT NOT NULL DEFAULT '',
    checked_by      TEXT NOT NULL DEFAULT '',
    approved_by     TEXT NOT NULL DEFAULT '',
    header_info     TEXT NOT NULL DEFAULT '',
    footer_info     TEXT NOT NULL DEFAULT '',
    iso_doc_no      TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, invoice_no)
);
CREATE INDEX IF NOT EXISTS idx_comm_inv_company_status ON commercial_invoices(company_id, status);
CREATE INDEX IF NOT EXISTS idx_comm_inv_customer ON commercial_invoices(customer_id);

CREATE TABLE IF NOT EXISTS commercial_invoice_lines (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    invoice_id  TEXT NOT NULL,
    so_line_id  TEXT,
    line_no     INT NOT NULL DEFAULT 1,
    item_code   TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    size        TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '',
    unit        TEXT NOT NULL DEFAULT '',
    quantity    NUMERIC(18,3) NOT NULL DEFAULT 0,
    unit_price  NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    line_total  NUMERIC(18,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comm_inv_lines_inv ON commercial_invoice_lines(invoice_id);

CREATE TABLE IF NOT EXISTS commercial_receipts (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    receipt_no  TEXT NOT NULL,
    invoice_id  TEXT NOT NULL,
    amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    method      TEXT NOT NULL DEFAULT 'cash',   -- cash|bank|telebirr|cbebirr|mpesa|cheque
    reference   TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMP NOT NULL DEFAULT NOW(),
    received_by TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',  -- manual|payments_module|api
    payment_id  TEXT,
    UNIQUE (company_id, receipt_no)
);
CREATE INDEX IF NOT EXISTS idx_comm_receipts_inv ON commercial_receipts(invoice_id);

CREATE TABLE IF NOT EXISTS commercial_returns (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL DEFAULT 'default',
    rn_no          TEXT NOT NULL,
    invoice_id     TEXT,
    so_id          TEXT,
    customer_id    TEXT,
    customer_name  TEXT NOT NULL DEFAULT '',
    return_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    reason         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'draft',   -- draft|approved|credited|rejected
    credit_note_no TEXT NOT NULL DEFAULT '',
    subtotal       NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_total      NUMERIC(18,2) NOT NULL DEFAULT 0,
    amount         NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes          TEXT NOT NULL DEFAULT '',
    prepared_by    TEXT NOT NULL DEFAULT '',
    approved_by    TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, rn_no)
);

CREATE TABLE IF NOT EXISTS commercial_return_lines (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    return_id   TEXT NOT NULL,
    line_no     INT NOT NULL DEFAULT 1,
    item_code   TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    unit        TEXT NOT NULL DEFAULT '',
    quantity    NUMERIC(18,3) NOT NULL DEFAULT 0,
    unit_price  NUMERIC(18,2) NOT NULL DEFAULT 0,
    line_total  NUMERIC(18,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comm_ret_lines_ret ON commercial_return_lines(return_id);

CREATE TABLE IF NOT EXISTS commercial_forecasts (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL DEFAULT 'default',
    period         TEXT NOT NULL,
    item_code      TEXT,
    territory_id   TEXT,
    forecast_qty   NUMERIC(18,3) NOT NULL DEFAULT 0,
    forecast_value NUMERIC(18,2) NOT NULL DEFAULT 0,
    method         TEXT NOT NULL DEFAULT 'moving_avg',  -- moving_avg|linear|manual
    detail         JSONB,
    generated_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    generated_by   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_comm_forecasts_period ON commercial_forecasts(company_id, period);

CREATE TABLE IF NOT EXISTS commercial_commissions (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL DEFAULT 'default',
    sales_rep_id      TEXT NOT NULL,
    invoice_id        TEXT NOT NULL,
    base_amount       NUMERIC(18,2) NOT NULL DEFAULT 0,
    pct               NUMERIC(6,2) NOT NULL DEFAULT 0,
    commission_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    period            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'accrued',   -- accrued|paid
    paid_at           TIMESTAMP,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (invoice_id, sales_rep_id)
);

CREATE TABLE IF NOT EXISTS commercial_campaigns (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL DEFAULT 'default',
    name               TEXT NOT NULL,
    channel            TEXT NOT NULL DEFAULT '',
    segment            TEXT NOT NULL DEFAULT '',
    start_date         DATE,
    end_date           DATE,
    budget             NUMERIC(18,2) NOT NULL DEFAULT 0,
    spent              NUMERIC(18,2) NOT NULL DEFAULT 0,
    leads              INT NOT NULL DEFAULT 0,
    conversions        INT NOT NULL DEFAULT 0,
    revenue_attributed NUMERIC(18,2) NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'planned',
    owner              TEXT NOT NULL DEFAULT '',
    notes              TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_campaigns_company ON commercial_campaigns(company_id);

CREATE TABLE IF NOT EXISTS commercial_marketing_events (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    campaign_id TEXT,
    name        TEXT NOT NULL,
    event_date  DATE,
    location    TEXT NOT NULL DEFAULT '',
    attendees   INT NOT NULL DEFAULT 0,
    leads       INT NOT NULL DEFAULT 0,
    cost        NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commercial_leads (
    id                    TEXT PRIMARY KEY,
    company_id            TEXT NOT NULL DEFAULT 'default',
    campaign_id           TEXT,
    name                  TEXT NOT NULL,
    company               TEXT NOT NULL DEFAULT '',
    phone                 TEXT NOT NULL DEFAULT '',
    email                 TEXT NOT NULL DEFAULT '',
    segment               TEXT NOT NULL DEFAULT '',
    source                TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'new',   -- new|contacted|qualified|won|lost
    est_value             NUMERIC(18,2) NOT NULL DEFAULT 0,
    owner                 TEXT NOT NULL DEFAULT '',
    notes                 TEXT NOT NULL DEFAULT '',
    converted_customer_id TEXT,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_leads_company ON commercial_leads(company_id);

CREATE TABLE IF NOT EXISTS commercial_competitors (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL DEFAULT 'default',
    name           TEXT NOT NULL,
    product        TEXT NOT NULL DEFAULT '',
    price_observed NUMERIC(18,2),
    observed_on    DATE,
    region         TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commercial_events (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    event       TEXT NOT NULL DEFAULT 'note',
    note        TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_events_entity ON commercial_events(entity_type, entity_id);
"""


def ensure_schema() -> None:
    """Create tables if missing. Never raises (logged) — called at app startup."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("commercial schema ready")
    except Exception as e:
        logger.error("commercial schema init failed: %s", e)


# ── low-level helpers ─────────────────────────────────────────────
def _uid() -> str:
    return str(uuid.uuid4())


def _row(r) -> Optional[dict]:
    return dict(r) if r is not None else None


def _rows(rs) -> List[dict]:
    return [dict(r) for r in rs]


def _txt(v: Any, limit: int = 4000) -> str:
    return ("" if v is None else str(v)).strip()[:limit]


def _query(sql: str, params: Sequence = (), one: bool = False):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                if one:
                    return _row(cur.fetchone())
                return _rows(cur.fetchall())
    except Exception as e:
        logger.error("commercial query failed: %s | %s", e, sql[:160])
        return None if one else []


def _execute(sql: str, params: Sequence = ()) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
        return True
    except Exception as e:
        logger.error("commercial execute failed: %s | %s", e, sql[:160])
        return False


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
    return cur.fetchone() is not None


def _columns(cur, table: str) -> set:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
    return {r["column_name"] for r in cur.fetchall()}


def _next_no(cur, company_id: str, doc_type: str, on: Optional[date] = None) -> str:
    """Gapless per company + year. MUST run in the same transaction as the
    INSERT that consumes the number (the UPDATE row-lock serialises callers)."""
    year = (on or date.today()).year
    cur.execute("""INSERT INTO commercial_sequences(company_id, doc_type, year, next_number)
                   VALUES (%s,%s,%s,1) ON CONFLICT (company_id, doc_type, year) DO NOTHING""",
                (company_id, doc_type, year))
    cur.execute("""UPDATE commercial_sequences SET next_number = next_number + 1
                   WHERE company_id=%s AND doc_type=%s AND year=%s RETURNING next_number - 1 AS n""",
                (company_id, doc_type, year))
    n = cur.fetchone()["n"]
    return L.format_doc_no(L.DOC_PREFIXES[doc_type], year, n)


def _date(v: Any) -> Optional[date]:
    return L.as_date(v)


def _month_bounds(period: str) -> Tuple[date, date]:
    y, m = int(period[:4]), int(period[5:7])
    start = date(y, m, 1)
    end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start, end


class CommercialDataStore:
    """All methods swallow database errors (logged) and return falsy values so
    routes can flash a friendly message instead of 500ing."""

    def ensure_schema(self):
        ensure_schema()

    # ── audit trail ──────────────────────────────────────────────
    def add_event(self, company_id: str, entity_type: str, entity_id: str, event: str, note: str = "",
                  actor: str = "") -> None:
        _execute("""INSERT INTO commercial_events(id,company_id,entity_type,entity_id,event,note,actor)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                 (_uid(), company_id, entity_type, entity_id, event, _txt(note), _txt(actor, 120)))

    def events_for(self, company_id: str, entity_type: str, entity_id: str) -> List[dict]:
        return _query("""SELECT * FROM commercial_events WHERE company_id=%s AND entity_type=%s AND entity_id=%s
                         ORDER BY created_at DESC""", (company_id, entity_type, entity_id))

    # ── settings ─────────────────────────────────────────────────
    def get_settings(self, company_id: str) -> dict:
        base = {"company_id": company_id, "so_approvers": L.normalize_approvers(None), "default_price_list_id": None,
                "header_info": "", "footer_info": "", "iso_doc_no": "", "invoice_series_code": "INV",
                "allow_over_credit": False, "default_vat_rate": L.VAT_RATE, "default_credit_days": 30}
        r = _query("SELECT * FROM commercial_settings WHERE company_id=%s", (company_id,), one=True)
        if r:
            base.update(r)
            base["so_approvers"] = L.normalize_approvers(r.get("so_approvers"))
        return base

    def save_settings(self, company_id: str, data: dict) -> bool:
        approvers = []
        for i in (1, 2, 3):
            approvers.append({"label": _txt(data.get(f"approver{i}_label"), 80) or f"Approver {i}",
                              "username_or_role": _txt(data.get(f"approver{i}_user"), 80)})
        approvers = L.normalize_approvers(approvers)
        return _execute(
            """INSERT INTO commercial_settings(company_id,so_approvers,default_price_list_id,header_info,footer_info,
                   iso_doc_no,invoice_series_code,allow_over_credit,default_vat_rate,default_credit_days,updated_at)
               VALUES (%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
               ON CONFLICT (company_id) DO UPDATE SET so_approvers=EXCLUDED.so_approvers,
                   default_price_list_id=EXCLUDED.default_price_list_id, header_info=EXCLUDED.header_info,
                   footer_info=EXCLUDED.footer_info, iso_doc_no=EXCLUDED.iso_doc_no,
                   invoice_series_code=EXCLUDED.invoice_series_code, allow_over_credit=EXCLUDED.allow_over_credit,
                   default_vat_rate=EXCLUDED.default_vat_rate, default_credit_days=EXCLUDED.default_credit_days,
                   updated_at=NOW()""",
            (company_id, json.dumps(approvers), opt(data.get("default_price_list_id")), _txt(data.get("header_info")),
             _txt(data.get("footer_info")), _txt(data.get("iso_doc_no"), 80),
             (_txt(data.get("invoice_series_code"), 20) or "INV").upper(), as_bool(data.get("allow_over_credit")),
             D(data.get("default_vat_rate"), "0.15"), to_int(data.get("default_credit_days"), 30)))

    def _doc_defaults(self, company_id: str, data: dict) -> dict:
        """header/footer/ISO fields fall back to the company settings."""
        s = self.get_settings(company_id)
        return {"header_info": _txt(data.get("header_info")) or s["header_info"],
                "footer_info": _txt(data.get("footer_info")) or s["footer_info"],
                "iso_doc_no": _txt(data.get("iso_doc_no"), 80) or s["iso_doc_no"]}

    # ── territories & reps ───────────────────────────────────────
    def list_territories(self, company_id: str) -> List[dict]:
        return _query("""SELECT t.*, (SELECT COUNT(*) FROM commercial_customers c WHERE c.territory_id=t.id) AS customers
                         FROM commercial_territories t WHERE company_id=%s ORDER BY name""", (company_id,))

    def create_territory(self, company_id: str, data: dict) -> Optional[dict]:
        name = _txt(data.get("name"), 120)
        if not name:
            return None
        return _query("INSERT INTO commercial_territories(id,company_id,name,region) VALUES (%s,%s,%s,%s) RETURNING *",
                      (_uid(), company_id, name, _txt(data.get("region"), 120)), one=True)

    def delete_territory(self, company_id: str, tid: str) -> bool:
        return _execute("DELETE FROM commercial_territories WHERE id=%s AND company_id=%s", (tid, company_id))

    def list_reps(self, company_id: str, active_only: bool = False) -> List[dict]:
        sql = """SELECT r.*, t.name AS territory_name FROM commercial_sales_reps r
                 LEFT JOIN commercial_territories t ON t.id=r.territory_id WHERE r.company_id=%s"""
        if active_only:
            sql += " AND r.is_active"
        return _query(sql + " ORDER BY r.name", (company_id,))

    def get_rep(self, company_id: str, rep_id: str) -> Optional[dict]:
        return _query("SELECT * FROM commercial_sales_reps WHERE id=%s AND company_id=%s", (rep_id, company_id), one=True)

    def create_rep(self, company_id: str, data: dict) -> Optional[dict]:
        name = _txt(data.get("name"), 120)
        if not name:
            return None
        return _query("""INSERT INTO commercial_sales_reps(id,company_id,name,username,territory_id,commission_pct,phone,email)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id, name, opt(_txt(data.get("username"), 80)), opt(data.get("territory_id")),
                       D(data.get("commission_pct")), _txt(data.get("phone"), 40), _txt(data.get("email"), 120)), one=True)

    def update_rep(self, company_id: str, rep_id: str, data: dict) -> bool:
        return _execute("""UPDATE commercial_sales_reps SET name=%s, username=%s, territory_id=%s, commission_pct=%s,
                           phone=%s, email=%s, is_active=%s WHERE id=%s AND company_id=%s""",
                        (_txt(data.get("name"), 120), opt(_txt(data.get("username"), 80)), opt(data.get("territory_id")),
                         D(data.get("commission_pct")), _txt(data.get("phone"), 40), _txt(data.get("email"), 120),
                         as_bool(data.get("is_active", True)), rep_id, company_id))

    def toggle_rep(self, company_id: str, rep_id: str) -> bool:
        return _execute("UPDATE commercial_sales_reps SET is_active=NOT is_active WHERE id=%s AND company_id=%s",
                        (rep_id, company_id))

    # ── customers ────────────────────────────────────────────────
    def _customer_cols(self, data: dict) -> tuple:
        return (_txt(data.get("code"), 40).upper(), _txt(data.get("name"), 200), _txt(data.get("tin"), 20),
                _txt(data.get("contact_person"), 120), _txt(data.get("phone"), 40), _txt(data.get("email"), 120),
                _txt(data.get("address"), 400), _txt(data.get("region"), 120), opt(data.get("territory_id")),
                opt(data.get("sales_rep_id")), _txt(data.get("segment"), 40) or "other", D(data.get("credit_limit")),
                to_int(data.get("credit_terms_days")), opt(data.get("portal_user_id")), _txt(data.get("notes")),
                as_bool(data.get("is_active", True)))

    def next_customer_code(self, company_id: str) -> str:
        r = _query("SELECT COUNT(*) AS c FROM commercial_customers WHERE company_id=%s", (company_id,), one=True)
        return f"CUS-{(r or {}).get('c', 0) + 1:04d}"

    def create_customer(self, company_id: str, data: dict) -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        if not _txt(data.get("code")):
            data = dict(data, code=self.next_customer_code(company_id))
        return _query("""INSERT INTO commercial_customers(id,company_id,code,name,tin,contact_person,phone,email,address,region,
                             territory_id,sales_rep_id,segment,credit_limit,credit_terms_days,portal_user_id,notes,is_active,created_by)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id) + self._customer_cols(data) + (_txt(data.get("created_by"), 80),), one=True)

    def update_customer(self, company_id: str, customer_id: str, data: dict) -> bool:
        if not _txt(data.get("name")):
            return False
        return _execute("""UPDATE commercial_customers SET code=%s,name=%s,tin=%s,contact_person=%s,phone=%s,email=%s,address=%s,
                           region=%s,territory_id=%s,sales_rep_id=%s,segment=%s,credit_limit=%s,credit_terms_days=%s,
                           portal_user_id=%s,notes=%s,is_active=%s,updated_at=NOW() WHERE id=%s AND company_id=%s""",
                        self._customer_cols(data) + (customer_id, company_id))

    def get_customer(self, company_id: str, customer_id: str) -> Optional[dict]:
        c = _query("""SELECT c.*, t.name AS territory_name, r.name AS sales_rep_name
                      FROM commercial_customers c LEFT JOIN commercial_territories t ON t.id=c.territory_id
                      LEFT JOIN commercial_sales_reps r ON r.id=c.sales_rep_id
                      WHERE c.id=%s AND c.company_id=%s""", (customer_id, company_id), one=True)
        if c:
            bal = self.customer_balances(company_id, [customer_id]).get(customer_id, {})
            c.update(invoiced=bal.get("invoiced", Decimal(0)), paid=bal.get("paid", Decimal(0)),
                     credited=bal.get("credited", Decimal(0)), balance=bal.get("balance", Decimal(0)),
                     overdue=bal.get("overdue", Decimal(0)),
                     available_credit=q2(D(c.get("credit_limit")) - bal.get("balance", Decimal(0))))
        return c

    def list_customers(self, company_id: str, q: str = None, segment: str = None, territory_id: str = None,
                       sales_rep_id: str = None, active_only: bool = False, with_balance: bool = True) -> List[dict]:
        sql = """SELECT c.*, t.name AS territory_name, r.name AS sales_rep_name FROM commercial_customers c
                 LEFT JOIN commercial_territories t ON t.id=c.territory_id
                 LEFT JOIN commercial_sales_reps r ON r.id=c.sales_rep_id WHERE c.company_id=%s"""
        params: list = [company_id]
        if q:
            sql += " AND (c.name ILIKE %s OR c.code ILIKE %s OR c.tin ILIKE %s OR c.phone ILIKE %s)"
            params += [f"%{q}%"] * 4
        if segment:
            sql += " AND c.segment=%s"; params.append(segment)
        if territory_id:
            sql += " AND c.territory_id=%s"; params.append(territory_id)
        if sales_rep_id:
            sql += " AND c.sales_rep_id=%s"; params.append(sales_rep_id)
        if active_only:
            sql += " AND c.is_active"
        rows = _query(sql + " ORDER BY c.name", params)
        if with_balance and rows:
            bals = self.customer_balances(company_id, [r["id"] for r in rows])
            for r in rows:
                b = bals.get(r["id"], {})
                r["balance"] = b.get("balance", Decimal(0))
                r["overdue"] = b.get("overdue", Decimal(0))
                r["available_credit"] = q2(D(r.get("credit_limit")) - r["balance"])
                r["over_limit"] = r["balance"] > D(r.get("credit_limit"))
        return rows

    def customer_balances(self, company_id: str, customer_ids: Optional[List[str]] = None) -> Dict[str, dict]:
        """{customer_id: {invoiced, paid, credited, balance, overdue}} from invoices."""
        sql = """SELECT customer_id, COALESCE(SUM(grand_total),0) AS invoiced, COALESCE(SUM(paid_total),0) AS paid,
                        COALESCE(SUM(credited_total),0) AS credited,
                        COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE AND status IN ('issued','partially_paid','overdue')
                                          THEN grand_total - paid_total - credited_total ELSE 0 END),0) AS overdue
                 FROM commercial_invoices WHERE company_id=%s AND status<>'cancelled' AND customer_id IS NOT NULL"""
        params: list = [company_id]
        if customer_ids:
            sql += " AND customer_id = ANY(%s)"; params.append(list(customer_ids))
        out = {}
        for r in _query(sql + " GROUP BY customer_id", params):
            out[r["customer_id"]] = {"invoiced": q2(r["invoiced"]), "paid": q2(r["paid"]), "credited": q2(r["credited"]),
                                     "balance": L.customer_balance_from(r["invoiced"], r["paid"], r["credited"]),
                                     "overdue": q2(r["overdue"])}
        return out

    def customer_balance(self, company_id: str, customer_id: str) -> Decimal:
        return self.customer_balances(company_id, [customer_id]).get(customer_id, {}).get("balance", Decimal("0.00"))

    def credit_status(self, company_id: str, customer_id: str, new_amount: Any = 0, credit_sale: bool = True) -> dict:
        c = self.get_customer(company_id, customer_id) if customer_id else None
        if not c:
            return L.credit_check(0, 0, new_amount, allow_over=True, credit_sale=credit_sale)
        return L.credit_check(c.get("credit_limit"), c.get("balance"), new_amount,
                              allow_over=self.get_settings(company_id)["allow_over_credit"], credit_sale=credit_sale)

    def customer_history(self, company_id: str, customer_id: str) -> dict:
        return {"orders": _query("""SELECT * FROM commercial_sales_orders WHERE company_id=%s AND customer_id=%s
                                    ORDER BY order_date DESC, created_at DESC LIMIT 50""", (company_id, customer_id)),
                "invoices": _query("""SELECT * FROM commercial_invoices WHERE company_id=%s AND customer_id=%s
                                      ORDER BY invoice_date DESC, created_at DESC LIMIT 50""", (company_id, customer_id)),
                "proformas": _query("""SELECT * FROM commercial_proformas WHERE company_id=%s AND customer_id=%s
                                       ORDER BY proforma_date DESC LIMIT 20""", (company_id, customer_id))}

    def portal_customers(self, company_id: str) -> List[dict]:
        """Portal users that could be linked to a customer (read-only; guarded)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if not _table_exists(cur, "portal_users"):
                        return []
                    cols = _columns(cur, "portal_users")
                    name_col = "full_name" if "full_name" in cols else ("name" if "name" in cols else "username")
                    if name_col not in cols or "id" not in cols:
                        return []
                    where = " WHERE company_id=%s" if "company_id" in cols else ""
                    cur.execute(f"SELECT id, {name_col} AS name FROM portal_users{where} ORDER BY 2 LIMIT 500",
                                (company_id,) if where else ())
                    return _rows(cur.fetchall())
        except Exception as e:
            logger.debug("portal_customers: %s", e)
            return []

    # ── products ─────────────────────────────────────────────────
    def list_products(self, company_id: str, q: str = None, active_only: bool = False) -> List[dict]:
        sql = "SELECT * FROM commercial_products WHERE company_id=%s"
        params: list = [company_id]
        if q:
            sql += " AND (item_code ILIKE %s OR description ILIKE %s OR product_code ILIKE %s OR size_mm2 ILIKE %s)"
            params += [f"%{q}%"] * 4
        if active_only:
            sql += " AND is_active"
        return _query(sql + " ORDER BY item_code", params)

    def get_product(self, company_id: str, product_id: str) -> Optional[dict]:
        return _query("SELECT * FROM commercial_products WHERE id=%s AND company_id=%s", (product_id, company_id), one=True)

    def product_by_code(self, company_id: str, item_code: str) -> Optional[dict]:
        return _query("SELECT * FROM commercial_products WHERE company_id=%s AND item_code=%s", (company_id, item_code), one=True)

    def _product_cols(self, data: dict) -> tuple:
        return (_txt(data.get("item_code"), 60).upper(), _txt(data.get("product_code"), 60), _txt(data.get("description"), 300),
                _txt(data.get("size_mm2"), 40), _txt(data.get("color"), 40), _txt(data.get("unit"), 20) or "m",
                _txt(data.get("packaging"), 80), D(data.get("list_price")), _txt(data.get("currency"), 8) or "ETB",
                D(data.get("vat_rate"), "0.15"), _txt(data.get("category"), 60), as_bool(data.get("is_active", True)))

    def create_product(self, company_id: str, data: dict) -> Optional[dict]:
        if not _txt(data.get("item_code")):
            return None
        return _query("""INSERT INTO commercial_products(id,company_id,item_code,product_code,description,size_mm2,color,unit,
                             packaging,list_price,currency,vat_rate,category,is_active)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id) + self._product_cols(data), one=True)

    def update_product(self, company_id: str, product_id: str, data: dict) -> bool:
        return _execute("""UPDATE commercial_products SET item_code=%s,product_code=%s,description=%s,size_mm2=%s,color=%s,unit=%s,
                           packaging=%s,list_price=%s,currency=%s,vat_rate=%s,category=%s,is_active=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""", self._product_cols(data) + (product_id, company_id))

    def import_from_manufacturing(self, company_id: str) -> Tuple[int, str]:
        """Upsert the catalogue from mfg_products when that table exists.
        Returns (count, message)."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if not _table_exists(cur, "mfg_products"):
                        return 0, "Manufacturing catalog not available — add products manually"
                    cols = _columns(cur, "mfg_products")
                    sel = ["code", "name"] + [c for c in ("size_mm2", "color", "unit", "description", "list_price",
                                                           "unit_price", "packaging", "category") if c in cols]
                    where = " WHERE company_id=%s" if "company_id" in cols else ""
                    cur.execute(f"SELECT {', '.join(sel)} FROM mfg_products{where}", (company_id,) if where else ())
                    n = 0
                    for r in cur.fetchall():
                        r = dict(r)
                        code = _txt(r.get("code"), 60).upper()
                        if not code:
                            continue
                        cur.execute("""INSERT INTO commercial_products(id,company_id,item_code,product_code,description,size_mm2,color,
                                           unit,packaging,list_price,category)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                       ON CONFLICT (company_id, item_code) DO UPDATE SET product_code=EXCLUDED.product_code,
                                           size_mm2=CASE WHEN EXCLUDED.size_mm2<>'' THEN EXCLUDED.size_mm2 ELSE commercial_products.size_mm2 END,
                                           color=CASE WHEN EXCLUDED.color<>'' THEN EXCLUDED.color ELSE commercial_products.color END,
                                           unit=CASE WHEN EXCLUDED.unit<>'' THEN EXCLUDED.unit ELSE commercial_products.unit END,
                                           updated_at=NOW()""",
                                    (_uid(), company_id, code, code, _txt(r.get("name") or r.get("description"), 300),
                                     _txt(r.get("size_mm2"), 40), _txt(r.get("color"), 40), _txt(r.get("unit"), 20) or "m",
                                     _txt(r.get("packaging"), 80), D(r.get("list_price") or r.get("unit_price")),
                                     _txt(r.get("category"), 60)))
                        n += 1
                    return n, f"Imported/updated {n} product(s) from the manufacturing catalog"
        except Exception as e:
            logger.error("import_from_manufacturing: %s", e)
            return 0, f"Import failed: {e}"

    # ── price lists & discounts ──────────────────────────────────
    def list_price_lists(self, company_id: str) -> List[dict]:
        return _query("""SELECT p.*, (SELECT COUNT(*) FROM commercial_price_list_items i WHERE i.price_list_id=p.id) AS items
                         FROM commercial_price_lists p WHERE company_id=%s ORDER BY valid_from DESC NULLS LAST, name""",
                      (company_id,))

    def get_price_list(self, company_id: str, pl_id: str) -> Optional[dict]:
        pl = _query("SELECT * FROM commercial_price_lists WHERE id=%s AND company_id=%s", (pl_id, company_id), one=True)
        if pl:
            pl["items"] = _query("""SELECT i.*, p.description, p.unit FROM commercial_price_list_items i
                                    LEFT JOIN commercial_products p ON p.company_id=i.company_id AND p.item_code=i.item_code
                                    WHERE i.price_list_id=%s ORDER BY i.item_code, i.min_qty""", (pl_id,))
        return pl

    def create_price_list(self, company_id: str, data: dict) -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        return _query("""INSERT INTO commercial_price_lists(id,company_id,name,valid_from,valid_to,currency)
                         VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id, _txt(data.get("name"), 120), opt(data.get("valid_from")), opt(data.get("valid_to")),
                       _txt(data.get("currency"), 8) or "ETB"), one=True)

    def add_price_list_item(self, company_id: str, pl_id: str, data: dict) -> bool:
        code = _txt(data.get("item_code"), 60).upper()
        if not code:
            return False
        return _execute("""INSERT INTO commercial_price_list_items(id,company_id,price_list_id,item_code,unit_price,min_qty)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (_uid(), company_id, pl_id, code, D(data.get("unit_price")), D(data.get("min_qty"))))

    def delete_price_list_item(self, company_id: str, item_id: str) -> bool:
        return _execute("DELETE FROM commercial_price_list_items WHERE id=%s AND company_id=%s", (item_id, company_id))

    def active_price_items(self, company_id: str, on: Optional[date] = None) -> List[dict]:
        """Items of the default price list (settings) or, failing that, every
        list valid on ``on``."""
        on = on or date.today()
        s = self.get_settings(company_id)
        if s.get("default_price_list_id"):
            rows = _query("SELECT * FROM commercial_price_list_items WHERE price_list_id=%s AND company_id=%s",
                          (s["default_price_list_id"], company_id))
            if rows:
                return rows
        return _query("""SELECT i.* FROM commercial_price_list_items i JOIN commercial_price_lists p ON p.id=i.price_list_id
                         WHERE i.company_id=%s AND p.is_active AND (p.valid_from IS NULL OR p.valid_from<=%s)
                           AND (p.valid_to IS NULL OR p.valid_to>=%s)""", (company_id, on, on))

    def list_discounts(self, company_id: str, active_only: bool = False) -> List[dict]:
        sql = "SELECT * FROM commercial_discounts WHERE company_id=%s"
        if active_only:
            sql += " AND is_active"
        return _query(sql + " ORDER BY created_at DESC", (company_id,))

    def create_discount(self, company_id: str, data: dict) -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        kind = data.get("kind") if data.get("kind") in L.DISCOUNT_KINDS else "percent"
        applies = data.get("applies_to") if data.get("applies_to") in L.DISCOUNT_APPLIES else "order"
        return _query("""INSERT INTO commercial_discounts(id,company_id,name,kind,value,applies_to,target,min_order_value,valid_from,valid_to)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id, _txt(data.get("name"), 120), kind, D(data.get("value")), applies,
                       _txt(data.get("target"), 120), D(data.get("min_order_value")), opt(data.get("valid_from")),
                       opt(data.get("valid_to"))), one=True)

    def toggle_discount(self, company_id: str, did: str) -> bool:
        return _execute("UPDATE commercial_discounts SET is_active=NOT is_active WHERE id=%s AND company_id=%s", (did, company_id))

    def price_lines(self, company_id: str, lines: List[dict], customer: Optional[dict] = None) -> List[dict]:
        """Fill blank unit prices from the price list / catalogue and blank
        descriptions from the catalogue."""
        items = self.active_price_items(company_id)
        for ln in lines:
            prod = self.product_by_code(company_id, ln.get("item_code") or "") if ln.get("item_code") else None
            if prod:
                for k, pk in (("description", "description"), ("size", "size_mm2"), ("color", "color"),
                              ("unit", "unit"), ("packaging", "packaging"), ("product_code", "product_code")):
                    if not ln.get(k):
                        ln[k] = prod.get(pk) or ""
            if D(ln.get("unit_price")) <= 0:
                ln["unit_price"] = L.unit_price_for(ln.get("item_code") or "", ln.get("quantity"), items,
                                                    (prod or {}).get("list_price") or 0)
        return lines

    # ── proforma invoices ────────────────────────────────────────
    def list_proformas(self, company_id: str, status: str = None, customer_id: str = None, q: str = None) -> List[dict]:
        sql = "SELECT * FROM commercial_proformas WHERE company_id=%s"
        params: list = [company_id]
        if status:
            sql += " AND status=%s"; params.append(status)
        if customer_id:
            sql += " AND customer_id=%s"; params.append(customer_id)
        if q:
            sql += " AND (proforma_no ILIKE %s OR customer_name ILIKE %s)"; params += [f"%{q}%"] * 2
        return _query(sql + " ORDER BY created_at DESC LIMIT 500", params)

    def get_proforma(self, company_id: str, pid: str) -> Optional[dict]:
        p = _query("SELECT * FROM commercial_proformas WHERE id=%s AND company_id=%s", (pid, company_id), one=True)
        if p:
            p["lines"] = _query("SELECT * FROM commercial_proforma_lines WHERE proforma_id=%s ORDER BY line_no", (pid,))
        return p

    def _customer_snapshot(self, company_id: str, data: dict) -> Tuple[Optional[str], str, str, Optional[dict]]:
        cust = self.get_customer(company_id, data.get("customer_id")) if data.get("customer_id") else None
        name = _txt(data.get("customer_name"), 200) or (cust or {}).get("name", "")
        tin = _txt(data.get("customer_tin"), 20) or (cust or {}).get("tin", "")
        return (cust or {}).get("id"), name, tin, cust

    def create_proforma(self, company_id: str, data: dict, lines: List[dict], actor: str = "") -> Optional[dict]:
        cust_id, name, tin, cust = self._customer_snapshot(company_id, data)
        lines = L.normalize_lines(self.price_lines(company_id, list(lines), cust))
        if not name or not lines:
            return None
        s = self.get_settings(company_id)
        tot = L.document_totals(lines, s["default_vat_rate"], data.get("order_discount"))
        hdr = self._doc_defaults(company_id, data)
        pdate = _date(data.get("proforma_date")) or date.today()
        valid_until = _date(data.get("valid_until")) or (pdate + timedelta(days=30))
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    pid = _uid()
                    no = _next_no(cur, company_id, "proforma", pdate)
                    cur.execute("""INSERT INTO commercial_proformas(id,company_id,proforma_no,customer_id,customer_name,customer_tin,
                                       proforma_date,valid_until,payment_method,payment_info,currency,subtotal,discount_total,vat_total,
                                       grand_total,status,notes,prepared_by,checked_by,approved_by,header_info,footer_info,iso_doc_no,created_by)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                                (pid, company_id, no, cust_id, name, tin, pdate, valid_until, _txt(data.get("payment_method"), 40),
                                 _txt(data.get("payment_info"), 400), _txt(data.get("currency"), 8) or "ETB", tot["subtotal"],
                                 tot["discount_total"], tot["vat_total"], tot["grand_total"], _txt(data.get("notes")),
                                 _txt(data.get("prepared_by"), 80) or actor, _txt(data.get("checked_by"), 80),
                                 _txt(data.get("approved_by"), 80), hdr["header_info"], hdr["footer_info"], hdr["iso_doc_no"], actor))
                    p = _row(cur.fetchone())
                    for i, ln in enumerate(lines, start=1):
                        cur.execute("""INSERT INTO commercial_proforma_lines(id,company_id,proforma_id,line_no,ref_no,item_code,size,description,
                                           color,unit,quantity,unit_price,discount,line_total)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), company_id, pid, i, _txt(ln.get("ref_no"), 40), ln["item_code"], _txt(ln.get("size"), 40),
                                     ln["description"], _txt(ln.get("color"), 40), _txt(ln.get("unit"), 20), ln["quantity"],
                                     ln["unit_price"], ln["discount"], ln["line_total"]))
            self.add_event(company_id, "proforma", pid, "created", f"Proforma {no} created", actor)
            return p
        except Exception as e:
            logger.error("create_proforma: %s", e)
            return None

    def set_proforma_status(self, company_id: str, pid: str, status: str, actor: str = "") -> bool:
        if status not in L.PROFORMA_STATUSES:
            return False
        ok = _execute("UPDATE commercial_proformas SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                      (status, pid, company_id))
        if ok:
            self.add_event(company_id, "proforma", pid, "status", f"Marked {status}", actor)
        return ok

    def convert_proforma(self, company_id: str, pid: str, actor: str = "") -> Optional[dict]:
        p = self.get_proforma(company_id, pid)
        if not p or p["status"] == "converted":
            return None
        so = self.create_sales_order(company_id, {
            "customer_id": p.get("customer_id"), "customer_name": p["customer_name"], "proforma_id": pid,
            "payment_terms": p.get("payment_method") or "", "currency": p.get("currency"),
            "notes": f"Converted from proforma {p['proforma_no']}", "prepared_by": actor,
        }, p["lines"], actor)
        if so:
            _execute("UPDATE commercial_proformas SET status='converted', sales_order_id=%s, updated_at=NOW() WHERE id=%s",
                     (so["id"], pid))
            self.add_event(company_id, "proforma", pid, "converted", f"Converted to {so['so_no']}", actor)
        return so

    # ── sales orders ─────────────────────────────────────────────
    def list_sales_orders(self, company_id: str, status: str = None, customer_id: str = None, q: str = None,
                          date_from=None, date_to=None, sales_rep_id: str = None, territory_id: str = None,
                          limit: int = 500) -> List[dict]:
        sql = """SELECT o.*, r.name AS sales_rep_name FROM commercial_sales_orders o
                 LEFT JOIN commercial_sales_reps r ON r.id=o.sales_rep_id WHERE o.company_id=%s"""
        params: list = [company_id]
        if status:
            if status == "open":
                sql += " AND o.status NOT IN ('closed','cancelled','rejected','invoiced')"
            else:
                sql += " AND o.status=%s"; params.append(status)
        if customer_id:
            sql += " AND o.customer_id=%s"; params.append(customer_id)
        if sales_rep_id:
            sql += " AND o.sales_rep_id=%s"; params.append(sales_rep_id)
        if territory_id:
            sql += " AND o.territory_id=%s"; params.append(territory_id)
        if q:
            sql += " AND (o.so_no ILIKE %s OR o.customer_name ILIKE %s)"; params += [f"%{q}%"] * 2
        if date_from:
            sql += " AND o.order_date>=%s"; params.append(date_from)
        if date_to:
            sql += " AND o.order_date<=%s"; params.append(date_to)
        sql += " ORDER BY o.created_at DESC LIMIT %s"; params.append(limit)
        return _query(sql, params)

    def get_sales_order(self, company_id: str, so_id: str) -> Optional[dict]:
        so = _query("""SELECT o.*, r.name AS sales_rep_name, t.name AS territory_name FROM commercial_sales_orders o
                       LEFT JOIN commercial_sales_reps r ON r.id=o.sales_rep_id
                       LEFT JOIN commercial_territories t ON t.id=o.territory_id
                       WHERE o.id=%s AND o.company_id=%s""", (so_id, company_id), one=True)
        if not so:
            return None
        so["lines"] = _query("SELECT * FROM commercial_so_lines WHERE so_id=%s ORDER BY line_no", (so_id,))
        so["approvals"] = _query("SELECT * FROM commercial_so_approvals WHERE so_id=%s ORDER BY seq", (so_id,))
        so["mo_requests"] = _query("SELECT * FROM commercial_mo_requests WHERE so_id=%s ORDER BY created_at", (so_id,))
        so["deliveries"] = _query("SELECT * FROM commercial_delivery_instructions WHERE so_id=%s ORDER BY created_at", (so_id,))
        so["invoices"] = _query("SELECT * FROM commercial_invoices WHERE so_id=%s ORDER BY created_at", (so_id,))
        so["fulfilment"] = L.fulfilment(sum((D(l["quantity"]) for l in so["lines"]), Decimal(0)),
                                        sum((D(l["delivered_qty"]) for l in so["lines"]), Decimal(0)),
                                        sum((D(l["invoiced_qty"]) for l in so["lines"]), Decimal(0)))
        so["approval_state"] = L.approval_state(so["approvals"]) if so["approvals"] else None
        so["next_seq"] = L.next_approval_seq(so["approvals"]) if so["approvals"] else None
        return so

    def sales_order_by_no(self, company_id: str, so_no: str) -> Optional[dict]:
        r = _query("SELECT id FROM commercial_sales_orders WHERE company_id=%s AND so_no=%s", (company_id, so_no), one=True)
        return self.get_sales_order(company_id, r["id"]) if r else None

    def _so_header_vals(self, company_id: str, data: dict, cust: Optional[dict]) -> dict:
        hdr = self._doc_defaults(company_id, data)
        start, end = D(data.get("starting_reading")) if opt(data.get("starting_reading")) is not None else None, \
            D(data.get("ending_reading")) if opt(data.get("ending_reading")) is not None else None
        diff = None
        if opt(data.get("difference_reading")) is not None:
            diff = D(data.get("difference_reading"))
        elif start is not None and end is not None:
            diff = end - start
        return dict(
            required_date=opt(data.get("required_date")), currency=_txt(data.get("currency"), 8) or "ETB",
            credit_sale=as_bool(data.get("credit_sale")), payment_terms=_txt(data.get("payment_terms"), 200),
            sales_rep_id=opt(data.get("sales_rep_id")) or (cust or {}).get("sales_rep_id"),
            territory_id=opt(data.get("territory_id")) or (cust or {}).get("territory_id"),
            prepared_by=_txt(data.get("prepared_by"), 80), checked_by=_txt(data.get("checked_by"), 80),
            approved_by=_txt(data.get("approved_by"), 80), verification_note=_txt(data.get("verification_note")),
            starting_reading=start, ending_reading=end, difference_reading=diff,
            copy_finance=as_bool(data.get("copy_finance", True)), copy_sales=as_bool(data.get("copy_sales", True)),
            copy_first=as_bool(data.get("copy_first", True)), notes=_txt(data.get("notes")),
            order_discount=q2(data.get("order_discount") or 0), **hdr)

    def create_sales_order(self, company_id: str, data: dict, lines: List[dict], actor: str = "") -> Optional[dict]:
        cust_id, name, _tin, cust = self._customer_snapshot(company_id, data)
        lines = L.normalize_lines(self.price_lines(company_id, list(lines), cust))
        if not name or not lines:
            return None
        s = self.get_settings(company_id)
        h = self._so_header_vals(company_id, data, cust)
        tot = L.document_totals(lines, s["default_vat_rate"], h["order_discount"])
        odate = _date(data.get("order_date")) or date.today()
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    so_id = _uid()
                    no = _next_no(cur, company_id, "sales_order", odate)
                    cur.execute("""INSERT INTO commercial_sales_orders(id,company_id,so_no,customer_id,customer_name,proforma_id,order_date,
                                       required_date,status,currency,subtotal,order_discount,discount_total,vat_rate,vat_total,grand_total,
                                       credit_sale,payment_terms,sales_rep_id,territory_id,prepared_by,checked_by,approved_by,verification_note,
                                       starting_reading,ending_reading,difference_reading,copy_finance,copy_sales,copy_first,header_info,
                                       footer_info,iso_doc_no,notes,created_by)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                   RETURNING *""",
                                (so_id, company_id, no, cust_id, name, opt(data.get("proforma_id")), odate, h["required_date"],
                                 h["currency"], tot["subtotal"], h["order_discount"], tot["discount_total"], s["default_vat_rate"],
                                 tot["vat_total"], tot["grand_total"], h["credit_sale"], h["payment_terms"], h["sales_rep_id"],
                                 h["territory_id"], h["prepared_by"] or actor, h["checked_by"], h["approved_by"], h["verification_note"],
                                 h["starting_reading"], h["ending_reading"], h["difference_reading"], h["copy_finance"], h["copy_sales"],
                                 h["copy_first"], h["header_info"], h["footer_info"], h["iso_doc_no"], h["notes"], actor))
                    so = _row(cur.fetchone())
                    self._insert_so_lines(cur, company_id, so_id, lines)
            self.add_event(company_id, "sales_order", so_id, "created", f"Sales order {no} created", actor)
            return so
        except Exception as e:
            logger.error("create_sales_order: %s", e)
            return None

    def _insert_so_lines(self, cur, company_id: str, so_id: str, lines: List[dict]) -> None:
        for i, ln in enumerate(lines, start=1):
            cur.execute("""INSERT INTO commercial_so_lines(id,company_id,so_id,line_no,item_code,product_code,size,description,color,unit,
                               packaging,quantity,unit_price,discount,line_total)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (_uid(), company_id, so_id, i, ln["item_code"], _txt(ln.get("product_code"), 60) or ln["item_code"],
                         _txt(ln.get("size"), 40), ln["description"], _txt(ln.get("color"), 40), _txt(ln.get("unit"), 20),
                         _txt(ln.get("packaging"), 80), ln["quantity"], ln["unit_price"], ln["discount"], ln["line_total"]))

    def update_sales_order(self, company_id: str, so_id: str, data: dict, lines: List[dict], actor: str = "") -> bool:
        """Only draft / rejected orders are editable; lines are replaced."""
        so = self.get_sales_order(company_id, so_id)
        if not so or so["status"] not in ("draft", "rejected"):
            return False
        cust_id, name, _tin, cust = self._customer_snapshot(company_id, data)
        lines = L.normalize_lines(self.price_lines(company_id, list(lines), cust))
        if not name or not lines:
            return False
        s = self.get_settings(company_id)
        h = self._so_header_vals(company_id, data, cust)
        tot = L.document_totals(lines, s["default_vat_rate"], h["order_discount"])
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE commercial_sales_orders SET customer_id=%s,customer_name=%s,order_date=%s,required_date=%s,currency=%s,
                                       subtotal=%s,order_discount=%s,discount_total=%s,vat_total=%s,grand_total=%s,credit_sale=%s,payment_terms=%s,
                                       sales_rep_id=%s,territory_id=%s,prepared_by=%s,checked_by=%s,approved_by=%s,verification_note=%s,
                                       starting_reading=%s,ending_reading=%s,difference_reading=%s,copy_finance=%s,copy_sales=%s,copy_first=%s,
                                       header_info=%s,footer_info=%s,iso_doc_no=%s,notes=%s,status='draft',updated_at=NOW()
                                   WHERE id=%s AND company_id=%s""",
                                (cust_id, name, _date(data.get("order_date")) or so["order_date"], h["required_date"], h["currency"],
                                 tot["subtotal"], h["order_discount"], tot["discount_total"], tot["vat_total"], tot["grand_total"],
                                 h["credit_sale"], h["payment_terms"], h["sales_rep_id"], h["territory_id"], h["prepared_by"],
                                 h["checked_by"], h["approved_by"], h["verification_note"], h["starting_reading"], h["ending_reading"],
                                 h["difference_reading"], h["copy_finance"], h["copy_sales"], h["copy_first"], h["header_info"],
                                 h["footer_info"], h["iso_doc_no"], h["notes"], so_id, company_id))
                    cur.execute("DELETE FROM commercial_so_lines WHERE so_id=%s", (so_id,))
                    cur.execute("DELETE FROM commercial_so_approvals WHERE so_id=%s", (so_id,))
                    self._insert_so_lines(cur, company_id, so_id, lines)
            self.add_event(company_id, "sales_order", so_id, "amended", "Order amended", actor)
            return True
        except Exception as e:
            logger.error("update_sales_order: %s", e)
            return False

    def set_so_status(self, company_id: str, so_id: str, status: str, actor: str = "", note: str = "",
                      force: bool = False) -> bool:
        so = _query("SELECT status FROM commercial_sales_orders WHERE id=%s AND company_id=%s", (so_id, company_id), one=True)
        if not so or status not in L.SO_STATUSES:
            return False
        if not force and not L.can_transition(so["status"], status):
            return False
        extra = ", closed_at=NOW()" if status == "closed" else (", approved_at=NOW()" if status == "approved" else "")
        ok = _execute(f"UPDATE commercial_sales_orders SET status=%s, updated_at=NOW(){extra} WHERE id=%s AND company_id=%s",
                      (status, so_id, company_id))
        if ok:
            if status == "cancelled":
                _execute("""UPDATE commercial_reservations SET status='released', released_at=NOW()
                            WHERE so_id=%s AND status='active'""", (so_id,))
            self.add_event(company_id, "sales_order", so_id, "status", note or f"Status → {status}", actor)
        return ok

    # -- three-approver workflow --
    def submit_for_approval(self, company_id: str, so_id: str, actor: str = "", credit_override: bool = False) -> Tuple[bool, str]:
        so = self.get_sales_order(company_id, so_id)
        if not so:
            return False, "Order not found"
        if so["status"] not in ("draft", "rejected"):
            return False, f"Order is {so['status']} — only draft or rejected orders can be submitted"
        cc = self.credit_status(company_id, so.get("customer_id"), so["grand_total"], so["credit_sale"])
        if not cc["ok"] and not credit_override:
            return False, cc["message"]
        approvers = self.get_settings(company_id)["so_approvers"]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM commercial_so_approvals WHERE so_id=%s", (so_id,))
                    for i, a in enumerate(approvers, start=1):
                        cur.execute("""INSERT INTO commercial_so_approvals(id,company_id,so_id,seq,role_label,approver)
                                       VALUES (%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), company_id, so_id, i, a["label"], a["username_or_role"]))
                    cur.execute("""UPDATE commercial_sales_orders SET status='pending_approval', submitted_at=NOW(), updated_at=NOW(),
                                       credit_override_by=%s WHERE id=%s""",
                                (actor if (credit_override and cc["level"] != "ok") else "", so_id))
            self.add_event(company_id, "sales_order", so_id, "submitted",
                           "Submitted for approval" + (f" — {cc['message']}" if cc["level"] != "ok" else ""), actor)
            return True, cc["message"] if cc["level"] == "warn" else "Submitted for approval (3 approvers required)"
        except Exception as e:
            logger.error("submit_for_approval: %s", e)
            return False, "Could not submit order"

    def decide_approval(self, company_id: str, so_id: str, seq: int, actor: str, decision: str, comment: str = "",
                        privilege_level: str = "viewer") -> Tuple[bool, str]:
        so = self.get_sales_order(company_id, so_id)
        if not so or so["status"] != "pending_approval":
            return False, "Order is not awaiting approval"
        step = next((a for a in so["approvals"] if int(a["seq"]) == int(seq)), None)
        if not step or step.get("decision"):
            return False, "Approval step not found or already decided"
        if so["next_seq"] != int(seq):
            return False, f"Approver {so['next_seq']} must decide first"
        if not L.can_approve(step, actor, privilege_level, requested_by=so.get("created_by") or ""):
            return False, "You are not the designated approver for this step"
        if decision not in ("approved", "rejected"):
            return False, "Invalid decision"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE commercial_so_approvals SET decision=%s, decided_by=%s, decided_at=NOW(), comment=%s
                                   WHERE so_id=%s AND seq=%s""", (decision, actor, _txt(comment), so_id, int(seq)))
                    cur.execute("SELECT * FROM commercial_so_approvals WHERE so_id=%s ORDER BY seq", (so_id,))
                    state = L.approval_state(_rows(cur.fetchall()))
                    if state == "approved":
                        cur.execute("""UPDATE commercial_sales_orders SET status='approved', approved_at=NOW(), approved_by=%s, updated_at=NOW()
                                       WHERE id=%s""", (actor, so_id))
                    elif state == "rejected":
                        cur.execute("UPDATE commercial_sales_orders SET status='rejected', updated_at=NOW() WHERE id=%s", (so_id,))
            self.add_event(company_id, "sales_order", so_id, decision, f"Approver {seq} ({step['role_label']}): {decision}. {comment}".strip(), actor)
            return True, {"approved": "Order fully approved", "rejected": "Order rejected"}.get(state, f"Step {seq} {decision} — awaiting next approver")
        except Exception as e:
            logger.error("decide_approval: %s", e)
            return False, "Could not record decision"

    def sync_from_approval_engine(self, company_id: str, so_id: str, status: str, actor: str, comment: str = "") -> None:
        """Called by the approval engine hook when a 'sales_order' request is decided."""
        new = "approved" if status == "approved" else "rejected"
        _execute("""UPDATE commercial_sales_orders SET status=%s, approved_by=CASE WHEN %s='approved' THEN %s ELSE approved_by END,
                        approved_at=CASE WHEN %s='approved' THEN NOW() ELSE approved_at END, updated_at=NOW()
                    WHERE id=%s AND company_id=%s AND status='pending_approval'""",
                 (new, new, actor, new, so_id, company_id))
        _execute("""UPDATE commercial_so_approvals SET decision=%s, decided_by=%s, decided_at=NOW(), comment=%s
                    WHERE so_id=%s AND decision=''""", (new, actor, _txt(comment) or "Approval workflow", so_id))
        self.add_event(company_id, "sales_order", so_id, new, f"Approval workflow: {status}. {comment}".strip(), actor)

    def set_approval_request(self, so_id: str, request_id: str) -> None:
        _execute("UPDATE commercial_sales_orders SET approval_request_id=%s WHERE id=%s", (request_id, so_id))

    def pending_approvals_for(self, company_id: str, username: str, privilege_level: str) -> List[dict]:
        rows = _query("""SELECT a.*, o.so_no, o.customer_name, o.grand_total, o.order_date, o.created_by
                         FROM commercial_so_approvals a JOIN commercial_sales_orders o ON o.id=a.so_id
                         WHERE a.company_id=%s AND a.decision='' AND o.status='pending_approval'
                           AND a.seq = (SELECT MIN(seq) FROM commercial_so_approvals b WHERE b.so_id=a.so_id AND b.decision='')
                         ORDER BY o.created_at""", (company_id,))
        return [r for r in rows if L.can_approve(r, username, privilege_level, requested_by=r.get("created_by") or "")]

    # -- inventory availability & reservations --
    def inventory_on_hand(self, company_id: str, item_codes: List[str]) -> Dict[str, Optional[Decimal]]:
        """{item_code: on_hand or None when the inventory module is absent}."""
        out: Dict[str, Optional[Decimal]] = {c: None for c in item_codes}
        if not item_codes:
            return out
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if not _table_exists(cur, "inventory_items"):
                        return out
                    cur.execute("""SELECT sku, COALESCE(SUM(current_stock),0) AS qty FROM inventory_items
                                   WHERE company_id=%s AND sku = ANY(%s) GROUP BY sku""", (company_id, list(item_codes)))
                    for r in cur.fetchall():
                        out[r["sku"]] = q3(r["qty"])
        except Exception as e:
            logger.debug("inventory_on_hand: %s", e)
        return out

    def reserved_qty(self, company_id: str, item_codes: List[str]) -> Dict[str, Decimal]:
        rows = _query("""SELECT item_code, COALESCE(SUM(quantity),0) AS qty FROM commercial_reservations
                         WHERE company_id=%s AND status='active' AND item_code = ANY(%s) GROUP BY item_code""",
                      (company_id, list(item_codes))) if item_codes else []
        return {r["item_code"]: q3(r["qty"]) for r in rows}

    def availability(self, company_id: str, so: dict) -> List[dict]:
        codes = [l["item_code"] for l in so.get("lines", []) if l.get("item_code")]
        on_hand = self.inventory_on_hand(company_id, codes)
        reserved = self.reserved_qty(company_id, codes)
        out = []
        for l in so.get("lines", []):
            oh = on_hand.get(l["item_code"])
            res = reserved.get(l["item_code"], Decimal(0))
            remaining = D(l["quantity"]) - D(l["delivered_qty"])
            avail = None if oh is None else q3(oh - res + D(l["reserved_qty"]))
            out.append({"line": l, "on_hand": oh, "reserved_total": res, "available": avail, "remaining": q3(remaining),
                        "can_fulfil": (avail is None) or (avail >= remaining),
                        "shortfall": None if avail is None else q3(max(remaining - avail, Decimal(0)))})
        return out

    def reserve(self, company_id: str, so_id: str, quantities: Dict[str, Any], actor: str = "") -> int:
        """quantities: {so_line_id: qty}. Replaces the active reservation of each line."""
        n = 0
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for line_id, qty in quantities.items():
                        qty = q3(qty)
                        cur.execute("SELECT id, item_code, quantity, delivered_qty FROM commercial_so_lines WHERE id=%s AND so_id=%s", (line_id, so_id))
                        ln = cur.fetchone()
                        if not ln:
                            continue
                        qty = min(qty, q3(D(ln["quantity"]) - D(ln["delivered_qty"])))
                        cur.execute("""UPDATE commercial_reservations SET status='released', released_at=NOW()
                                       WHERE so_line_id=%s AND status='active'""", (line_id,))
                        if qty > 0:
                            cur.execute("""INSERT INTO commercial_reservations(id,company_id,so_id,so_line_id,item_code,quantity,reserved_by)
                                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                        (_uid(), company_id, so_id, line_id, ln["item_code"], qty, actor))
                        cur.execute("UPDATE commercial_so_lines SET reserved_qty=%s WHERE id=%s", (qty, line_id))
                        n += 1
            self.add_event(company_id, "sales_order", so_id, "reserved", f"Stock reserved on {n} line(s)", actor)
        except Exception as e:
            logger.error("reserve: %s", e)
        return n

    # -- manufacturing order requests --
    def create_mo_requests(self, company_id: str, so_id: str, line_ids: Optional[List[str]] = None, data: Optional[dict] = None,
                           actor: str = "") -> Tuple[int, int, str]:
        """One request per SO line (all lines when ``line_ids`` is None).
        Tries manufacturing_data_store.create_order_from_sales_order; falls back
        to a stored request. Returns (created, linked_to_mfg, message)."""
        data = data or {}
        so = self.get_sales_order(company_id, so_id)
        if not so or so["status"] in ("draft", "pending_approval", "rejected", "cancelled", "closed"):
            return 0, 0, "Order must be approved before manufacturing orders are raised"
        lines = [l for l in so["lines"] if (line_ids is None or l["id"] in line_ids) and not l.get("mo_request_id")]
        if not lines:
            return 0, 0, "No open lines to send to production"
        hdr = self._doc_defaults(company_id, data)
        created = linked = 0
        try:
            mfg = __import__("manufacturing_data_store")
            create_fn = getattr(mfg, "create_order_from_sales_order", None)
        except Exception:
            create_fn = None
        for l in lines:
            mo_no, mfg_id, status = "", None, "requested"
            if create_fn:
                try:
                    res = create_fn(company_id, source_ref=so["so_no"], customer_name=so["customer_name"],
                                    product_code=l.get("product_code") or l["item_code"], qty=D(l["quantity"]),
                                    unit=l.get("unit") or "", cutting_length=opt(data.get("cutting_length")),
                                    packing=opt(data.get("packing")) or l.get("packaging") or None,
                                    delivery_date=opt(data.get("delivery_date")) or so.get("required_date"), prepared_by=actor)
                    if isinstance(res, dict):
                        mo_no = _txt(res.get("mo_no") or res.get("order_no") or res.get("number"), 60)
                        mfg_id = res.get("id")
                    elif res:
                        mo_no = _txt(res, 60)
                    if mo_no or mfg_id:
                        status, linked = "sent", linked + 1
                except Exception as e:
                    logger.warning("manufacturing create_order_from_sales_order failed: %s", e)
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        rid = _uid()
                        no = _next_no(cur, company_id, "mo_request")
                        cur.execute("""INSERT INTO commercial_mo_requests(id,company_id,request_no,so_id,so_line_id,so_no,customer_name,product_code,
                                           item_code,description,size,color,unit,quantity,packing,cutting_length,delivery_date,comment,to_factory,
                                           status,mo_no,mfg_order_id,prepared_by,checked_by,approved_by,manager,header_info,footer_info,iso_doc_no)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (rid, company_id, no, so_id, l["id"], so["so_no"], so["customer_name"],
                                     l.get("product_code") or l["item_code"], l["item_code"], l["description"], l.get("size") or "",
                                     l.get("color") or "", l.get("unit") or "", l["quantity"], _txt(data.get("packing"), 80) or l.get("packaging") or "",
                                     _txt(data.get("cutting_length"), 80), opt(data.get("delivery_date")) or so.get("required_date"),
                                     _txt(data.get("comment")), _txt(data.get("to_factory"), 120), status, mo_no, mfg_id,
                                     actor, _txt(data.get("checked_by"), 80), _txt(data.get("approved_by"), 80), _txt(data.get("manager"), 80),
                                     hdr["header_info"], hdr["footer_info"], hdr["iso_doc_no"]))
                        cur.execute("UPDATE commercial_so_lines SET mo_request_id=%s, mo_no=%s WHERE id=%s", (rid, mo_no, l["id"]))
                created += 1
            except Exception as e:
                logger.error("create_mo_requests: %s", e)
        if created and so["status"] in ("approved",):
            _execute("UPDATE commercial_sales_orders SET status='in_production', updated_at=NOW() WHERE id=%s", (so_id,))
        self.add_event(company_id, "sales_order", so_id, "mo_request", f"{created} manufacturing order request(s) raised", actor)
        if create_fn and linked:
            return created, linked, f"{linked} manufacturing order(s) created in Production"
        return created, linked, f"{created} request(s) sent to Planning & Engineering (manual step — manufacturing module not linked)"

    def list_mo_requests(self, company_id: str, status: str = None) -> List[dict]:
        sql = "SELECT * FROM commercial_mo_requests WHERE company_id=%s"
        params: list = [company_id]
        if status:
            sql += " AND status=%s"; params.append(status)
        return _query(sql + " ORDER BY created_at DESC LIMIT 500", params)

    def get_mo_request(self, company_id: str, rid: str) -> Optional[dict]:
        r = _query("SELECT * FROM commercial_mo_requests WHERE id=%s AND company_id=%s", (rid, company_id), one=True)
        if r:
            r["mfg_status"] = self.manufacturing_status(company_id, r.get("so_no") or "")
        return r

    def update_mo_request(self, company_id: str, rid: str, data: dict, actor: str = "") -> bool:
        status = data.get("status") if data.get("status") in ("requested", "sent", "accepted", "in_production", "completed", "cancelled") else None
        ok = _execute("""UPDATE commercial_mo_requests SET status=COALESCE(%s,status), mo_no=CASE WHEN %s<>'' THEN %s ELSE mo_no END,
                             comment=%s, to_factory=%s, cutting_length=%s, packing=%s, delivery_date=%s, checked_by=%s, approved_by=%s, manager=%s
                         WHERE id=%s AND company_id=%s""",
                      (status, _txt(data.get("mo_no"), 60), _txt(data.get("mo_no"), 60), _txt(data.get("comment")),
                       _txt(data.get("to_factory"), 120), _txt(data.get("cutting_length"), 80), _txt(data.get("packing"), 80),
                       opt(data.get("delivery_date")), _txt(data.get("checked_by"), 80), _txt(data.get("approved_by"), 80),
                       _txt(data.get("manager"), 80), rid, company_id))
        if ok and _txt(data.get("mo_no")):
            _execute("UPDATE commercial_so_lines SET mo_no=%s WHERE mo_request_id=%s", (_txt(data.get("mo_no"), 60), rid))
        if ok and status == "completed":
            r = self.get_mo_request(company_id, rid)
            if r and r.get("so_id"):
                pending = _query("SELECT COUNT(*) AS c FROM commercial_mo_requests WHERE so_id=%s AND status NOT IN ('completed','cancelled')",
                                 (r["so_id"],), one=True)
                if pending and pending["c"] == 0:
                    _execute("UPDATE commercial_sales_orders SET status='ready', updated_at=NOW() WHERE id=%s AND status='in_production'", (r["so_id"],))
        return ok

    def manufacturing_status(self, company_id: str, source_ref: str) -> Optional[dict]:
        if not source_ref:
            return None
        try:
            mfg = __import__("manufacturing_data_store")
            fn = getattr(mfg, "order_status_summary", None)
            return fn(company_id, source_ref) if fn else None
        except Exception as e:
            logger.debug("manufacturing_status: %s", e)
            return None

    # ── delivery instructions & dispatch ─────────────────────────
    def list_deliveries(self, company_id: str, status: str = None, so_id: str = None) -> List[dict]:
        sql = """SELECT d.*, (SELECT COUNT(*) FROM commercial_dispatches x WHERE x.di_id=d.id) AS dispatches,
                        (SELECT COALESCE(SUM(quantity),0) FROM commercial_di_lines l WHERE l.di_id=d.id) AS total_qty
                 FROM commercial_delivery_instructions d WHERE d.company_id=%s"""
        params: list = [company_id]
        if status:
            sql += " AND d.status=%s"; params.append(status)
        if so_id:
            sql += " AND d.so_id=%s"; params.append(so_id)
        return _query(sql + " ORDER BY d.created_at DESC LIMIT 500", params)

    def get_delivery(self, company_id: str, di_id: str) -> Optional[dict]:
        d = _query("SELECT * FROM commercial_delivery_instructions WHERE id=%s AND company_id=%s", (di_id, company_id), one=True)
        if d:
            d["lines"] = _query("SELECT * FROM commercial_di_lines WHERE di_id=%s ORDER BY line_no", (di_id,))
            d["dispatches"] = _query("SELECT * FROM commercial_dispatches WHERE di_id=%s ORDER BY created_at", (di_id,))
        return d

    def create_delivery(self, company_id: str, so_id: str, quantities: Dict[str, Any], data: dict, actor: str = "") -> Optional[dict]:
        """quantities: {so_line_id: qty to deliver}. Capped at ordered − delivered."""
        so = self.get_sales_order(company_id, so_id)
        if not so or so["status"] in ("draft", "pending_approval", "rejected", "cancelled", "closed"):
            return None
        lines = []
        for l in so["lines"]:
            qty = q3(quantities.get(l["id"], 0))
            remaining = q3(D(l["quantity"]) - D(l["delivered_qty"]))
            qty = min(qty, remaining)
            if qty > 0:
                lines.append((l, qty))
        if not lines:
            return None
        hdr = self._doc_defaults(company_id, data)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    di_id = _uid()
                    no = _next_no(cur, company_id, "delivery_instruction")
                    cur.execute("""INSERT INTO commercial_delivery_instructions(id,company_id,di_no,so_id,so_no,customer_id,customer_name,di_date,
                                       deliver_to,warehouse,notes,prepared_by,checked_by,approved_by,header_info,footer_info,iso_doc_no,created_by)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                                (di_id, company_id, no, so_id, so["so_no"], so.get("customer_id"), so["customer_name"],
                                 _date(data.get("di_date")) or date.today(), _txt(data.get("deliver_to"), 400), _txt(data.get("warehouse"), 120),
                                 _txt(data.get("notes")), _txt(data.get("prepared_by"), 80) or actor, _txt(data.get("checked_by"), 80),
                                 _txt(data.get("approved_by"), 80), hdr["header_info"], hdr["footer_info"], hdr["iso_doc_no"], actor))
                    d = _row(cur.fetchone())
                    for i, (l, qty) in enumerate(lines, start=1):
                        cur.execute("""INSERT INTO commercial_di_lines(id,company_id,di_id,so_line_id,line_no,product_code,item_code,description,size,
                                           color,unit,packaging,quantity) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), company_id, di_id, l["id"], i, l.get("product_code") or "", l["item_code"], l["description"],
                                     l.get("size") or "", l.get("color") or "", l.get("unit") or "", l.get("packaging") or "", qty))
            self.add_event(company_id, "sales_order", so_id, "delivery_instruction", f"Delivery instruction {no} issued", actor)
            return d
        except Exception as e:
            logger.error("create_delivery: %s", e)
            return None

    def create_dispatch(self, company_id: str, di_id: str, data: dict, actor: str = "") -> Optional[dict]:
        d = self.get_delivery(company_id, di_id)
        if not d or d["status"] in ("completed", "cancelled"):
            return None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    xid = _uid()
                    no = _next_no(cur, company_id, "dispatch")
                    dispatched_at = opt(data.get("dispatched_at"))
                    cur.execute("""INSERT INTO commercial_dispatches(id,company_id,dispatch_no,di_id,so_id,vehicle,driver,driver_phone,dispatched_at,
                                       status,notes,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                                (xid, company_id, no, di_id, d.get("so_id"), _txt(data.get("vehicle"), 60), _txt(data.get("driver"), 120),
                                 _txt(data.get("driver_phone"), 40), dispatched_at, "dispatched" if dispatched_at else "planned",
                                 _txt(data.get("notes")), actor))
                    x = _row(cur.fetchone())
                    cur.execute("UPDATE commercial_delivery_instructions SET status='dispatched' WHERE id=%s AND status='open'", (di_id,))
            self.add_event(company_id, "delivery", di_id, "dispatch", f"Dispatch {no} created", actor)
            return x
        except Exception as e:
            logger.error("create_dispatch: %s", e)
            return None

    def update_dispatch(self, company_id: str, dispatch_id: str, data: dict, actor: str = "") -> Tuple[bool, str]:
        """Status changes: dispatched → delivered (posts delivered_qty to the SO)
        → accepted (customer confirmation) / rejected."""
        x = _query("SELECT * FROM commercial_dispatches WHERE id=%s AND company_id=%s", (dispatch_id, company_id), one=True)
        if not x:
            return False, "Dispatch not found"
        status = data.get("status") or x["status"]
        if status not in L.DISPATCH_STATUSES:
            return False, "Invalid status"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE commercial_dispatches SET status=%s, vehicle=COALESCE(NULLIF(%s,''),vehicle), driver=COALESCE(NULLIF(%s,''),driver),
                                       driver_phone=COALESCE(NULLIF(%s,''),driver_phone),
                                       dispatched_at=CASE WHEN %s='dispatched' AND dispatched_at IS NULL THEN NOW() ELSE COALESCE(%s,dispatched_at) END,
                                       received_by=COALESCE(NULLIF(%s,''),received_by),
                                       delivered_at=CASE WHEN %s IN ('delivered','accepted') AND delivered_at IS NULL THEN COALESCE(%s,NOW()) ELSE delivered_at END,
                                       accepted_at=CASE WHEN %s='accepted' THEN NOW() ELSE accepted_at END,
                                       acceptance_note=COALESCE(NULLIF(%s,''),acceptance_note), notes=COALESCE(NULLIF(%s,''),notes)
                                   WHERE id=%s""",
                                (status, _txt(data.get("vehicle"), 60), _txt(data.get("driver"), 120), _txt(data.get("driver_phone"), 40),
                                 status, opt(data.get("dispatched_at")), _txt(data.get("received_by"), 120), status,
                                 opt(data.get("delivered_at")), status, _txt(data.get("acceptance_note")), _txt(data.get("notes")), dispatch_id))
                    if status in ("delivered", "accepted") and x["status"] not in ("delivered", "accepted"):
                        # post quantities to the SO lines exactly once
                        cur.execute("SELECT so_line_id, quantity FROM commercial_di_lines WHERE di_id=%s", (x["di_id"],))
                        for l in cur.fetchall():
                            if l["so_line_id"]:
                                cur.execute("""UPDATE commercial_so_lines SET delivered_qty=delivered_qty+%s,
                                                   reserved_qty=GREATEST(reserved_qty-%s,0) WHERE id=%s""",
                                            (l["quantity"], l["quantity"], l["so_line_id"]))
                                cur.execute("""UPDATE commercial_reservations SET status='consumed', released_at=NOW()
                                               WHERE so_line_id=%s AND status='active'""", (l["so_line_id"],))
                        cur.execute("UPDATE commercial_delivery_instructions SET status='completed' WHERE id=%s", (x["di_id"],))
                        if x.get("so_id"):
                            cur.execute("SELECT status FROM commercial_sales_orders WHERE id=%s", (x["so_id"],))
                            so = cur.fetchone()
                            cur.execute("SELECT quantity, delivered_qty, invoiced_qty FROM commercial_so_lines WHERE so_id=%s", (x["so_id"],))
                            new = L.derive_so_status(so["status"], _rows(cur.fetchall()))
                            cur.execute("UPDATE commercial_sales_orders SET status=%s, updated_at=NOW() WHERE id=%s", (new, x["so_id"]))
            self.add_event(company_id, "delivery", x["di_id"], "dispatch_status", f"Dispatch {x['dispatch_no']} → {status}", actor)
            if x.get("so_id"):
                self.add_event(company_id, "sales_order", x["so_id"], "dispatch_status", f"Dispatch {x['dispatch_no']} → {status}", actor)
            return True, f"Dispatch marked {status}"
        except Exception as e:
            logger.error("update_dispatch: %s", e)
            return False, "Could not update dispatch"

    def get_dispatch(self, company_id: str, dispatch_id: str) -> Optional[dict]:
        return _query("""SELECT x.*, d.di_no, d.customer_name, d.so_no FROM commercial_dispatches x
                         JOIN commercial_delivery_instructions d ON d.id=x.di_id WHERE x.id=%s AND x.company_id=%s""",
                      (dispatch_id, company_id), one=True)

    def list_dispatches(self, company_id: str, status: str = None, date_from=None, date_to=None) -> List[dict]:
        sql = """SELECT x.*, d.di_no, d.customer_name, d.so_no, o.required_date FROM commercial_dispatches x
                 JOIN commercial_delivery_instructions d ON d.id=x.di_id
                 LEFT JOIN commercial_sales_orders o ON o.id=x.so_id WHERE x.company_id=%s"""
        params: list = [company_id]
        if status:
            sql += " AND x.status=%s"; params.append(status)
        if date_from:
            sql += " AND x.created_at>=%s"; params.append(date_from)
        if date_to:
            sql += " AND x.created_at<%s"; params.append(_date(date_to) + timedelta(days=1) if _date(date_to) else date_to)
        return _query(sql + " ORDER BY x.created_at DESC LIMIT 500", params)

    # ── invoices ─────────────────────────────────────────────────
    def list_invoices(self, company_id: str, status: str = None, customer_id: str = None, q: str = None, credit_only: bool = False,
                      date_from=None, date_to=None, sales_rep_id: str = None, overdue_only: bool = False, limit: int = 500) -> List[dict]:
        sql = """SELECT i.*, r.name AS sales_rep_name, (i.grand_total - i.paid_total - i.credited_total) AS outstanding
                 FROM commercial_invoices i LEFT JOIN commercial_sales_reps r ON r.id=i.sales_rep_id WHERE i.company_id=%s"""
        params: list = [company_id]
        if status:
            sql += " AND i.status=%s"; params.append(status)
        if customer_id:
            sql += " AND i.customer_id=%s"; params.append(customer_id)
        if sales_rep_id:
            sql += " AND i.sales_rep_id=%s"; params.append(sales_rep_id)
        if credit_only:
            sql += " AND i.credit_sale"
        if overdue_only:
            sql += " AND i.due_date < CURRENT_DATE AND i.status IN ('issued','partially_paid','overdue')"
        if q:
            sql += " AND (i.invoice_no ILIKE %s OR i.customer_name ILIKE %s OR i.so_no ILIKE %s)"; params += [f"%{q}%"] * 3
        if date_from:
            sql += " AND i.invoice_date>=%s"; params.append(date_from)
        if date_to:
            sql += " AND i.invoice_date<=%s"; params.append(date_to)
        sql += " ORDER BY i.created_at DESC LIMIT %s"; params.append(limit)
        return _query(sql, params)

    def get_invoice(self, company_id: str, invoice_id: str) -> Optional[dict]:
        inv = _query("""SELECT i.*, r.name AS sales_rep_name, (i.grand_total - i.paid_total - i.credited_total) AS outstanding
                        FROM commercial_invoices i LEFT JOIN commercial_sales_reps r ON r.id=i.sales_rep_id
                        WHERE i.id=%s AND i.company_id=%s""", (invoice_id, company_id), one=True)
        if inv:
            inv["lines"] = _query("SELECT * FROM commercial_invoice_lines WHERE invoice_id=%s ORDER BY line_no", (invoice_id,))
            inv["receipts"] = _query("SELECT * FROM commercial_receipts WHERE invoice_id=%s ORDER BY received_at", (invoice_id,))
            inv["returns"] = _query("SELECT * FROM commercial_returns WHERE invoice_id=%s ORDER BY created_at", (invoice_id,))
            inv["days_overdue"] = L.days_overdue(inv.get("due_date")) if inv["status"] in ("issued", "partially_paid", "overdue") else 0
        return inv

    def invoice_by_no(self, company_id: str, invoice_no: str) -> Optional[dict]:
        r = _query("SELECT id FROM commercial_invoices WHERE company_id=%s AND (invoice_no=%s OR erca_number=%s)",
                   (company_id, invoice_no, invoice_no), one=True)
        return self.get_invoice(company_id, r["id"]) if r else None

    def create_invoice(self, company_id: str, so_id: Optional[str], quantities: Optional[Dict[str, Any]], data: dict,
                       lines: Optional[List[dict]] = None, actor: str = "") -> Tuple[Optional[dict], str]:
        """Invoice an SO (``quantities`` {so_line_id: qty}, capped at ordered − invoiced)
        or free lines when ``so_id`` is None. Numbering: ERCA series when the ERCA
        module has an active series, else INV-YYYY-NNNNNN. Side effects (best
        effort): VAT income record, ERCA e-invoice, sales commission."""
        so = self.get_sales_order(company_id, so_id) if so_id else None
        s = self.get_settings(company_id)
        inv_lines: List[dict] = []
        if so:
            if so["status"] in ("draft", "pending_approval", "rejected", "cancelled"):
                return None, "Order must be approved before invoicing"
            for l in so["lines"]:
                qty = q3((quantities or {}).get(l["id"], 0))
                remaining = q3(D(l["quantity"]) - D(l["invoiced_qty"]))
                qty = min(qty, remaining)
                if qty > 0:
                    inv_lines.append({"so_line_id": l["id"], "item_code": l["item_code"], "description": l["description"],
                                      "size": l.get("size") or "", "color": l.get("color") or "", "unit": l.get("unit") or "",
                                      "quantity": qty, "unit_price": l["unit_price"],
                                      "discount": q2(D(l["discount"]) * qty / D(l["quantity"])) if D(l["quantity"]) else Decimal(0)})
            customer_id, customer_name = so.get("customer_id"), so["customer_name"]
            credit_sale = so["credit_sale"] if data.get("credit_sale") is None else as_bool(data.get("credit_sale"))
            rep_id, terr_id = so.get("sales_rep_id"), so.get("territory_id")
        else:
            cid_, customer_name, _t, cust = self._customer_snapshot(company_id, data)
            customer_id = cid_
            inv_lines = self.price_lines(company_id, list(lines or []), cust)
            credit_sale = as_bool(data.get("credit_sale"))
            rep_id, terr_id = (cust or {}).get("sales_rep_id"), (cust or {}).get("territory_id")
        inv_lines = L.normalize_lines(inv_lines)
        if not inv_lines or not customer_name:
            return None, "Nothing to invoice"
        cust = self.get_customer(company_id, customer_id) if customer_id else None
        tin = _txt(data.get("customer_tin"), 20) or (cust or {}).get("tin") or ""
        order_disc = Decimal(0)
        if so and D(so.get("order_discount")) > 0 and D(so["subtotal"]) > 0:
            order_disc = q2(D(so["order_discount"]) * sum((D(l["quantity"]) * D(l["unit_price"]) for l in inv_lines), Decimal(0)) / D(so["subtotal"]))
        tot = L.document_totals(inv_lines, s["default_vat_rate"], order_disc)
        inv_date = _date(data.get("invoice_date")) or date.today()
        due = _date(data.get("due_date")) or L.due_date_for(inv_date, (cust or {}).get("credit_terms_days") or s["default_credit_days"]) \
            if credit_sale else (_date(data.get("due_date")) or inv_date)
        if credit_sale:
            cc = self.credit_status(company_id, customer_id, tot["grand_total"], True)
            if not cc["ok"] and not as_bool(data.get("credit_override")):
                return None, cc["message"]
        hdr = self._doc_defaults(company_id, data)
        # ERCA number first (its own transaction — a failed insert below would be a gap, so we log it)
        erca_no, erca_id = "", None
        try:
            import erca_data_store as _erca
            e = _erca.issue_invoice(company_id, s["invoice_series_code"], customer_name=customer_name, customer_tin=tin,
                                    items=[{"description": f"{l['item_code']} {l['description']}".strip(), "qty": l["quantity"],
                                            "unit_price": l["unit_price"], "vat_rate": s["default_vat_rate"]} for l in inv_lines],
                                    issued_at=datetime.combine(inv_date, datetime.now().time()), created_by=actor)
            if e:
                erca_no, erca_id = e.get("number") or "", e.get("id")
        except Exception as ex:
            logger.debug("erca issue_invoice unavailable: %s", ex)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    inv_id = _uid()
                    no = erca_no or _next_no(cur, company_id, "invoice", inv_date)
                    cur.execute("""INSERT INTO commercial_invoices(id,company_id,invoice_no,so_id,so_no,customer_id,customer_name,customer_tin,invoice_date,
                                       due_date,currency,subtotal,discount_total,vat_rate,vat_total,grand_total,credit_sale,status,sales_rep_id,territory_id,
                                       erca_invoice_id,erca_number,prepared_by,checked_by,approved_by,header_info,footer_info,iso_doc_no,notes,created_by)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'issued',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                                (inv_id, company_id, no, so_id, (so or {}).get("so_no") or "", customer_id, customer_name, tin, inv_date, due,
                                 (so or {}).get("currency") or "ETB", tot["subtotal"], tot["discount_total"], s["default_vat_rate"], tot["vat_total"],
                                 tot["grand_total"], credit_sale, rep_id, terr_id, erca_id, erca_no, _txt(data.get("prepared_by"), 80) or actor,
                                 _txt(data.get("checked_by"), 80), _txt(data.get("approved_by"), 80), hdr["header_info"], hdr["footer_info"],
                                 hdr["iso_doc_no"], _txt(data.get("notes")), actor))
                    inv = _row(cur.fetchone())
                    for i, l in enumerate(inv_lines, start=1):
                        cur.execute("""INSERT INTO commercial_invoice_lines(id,company_id,invoice_id,so_line_id,line_no,item_code,description,size,color,unit,
                                           quantity,unit_price,discount,line_total) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), company_id, inv_id, l.get("so_line_id"), i, l["item_code"], l["description"], l.get("size") or "",
                                     l.get("color") or "", l.get("unit") or "", l["quantity"], l["unit_price"], l["discount"], l["line_total"]))
                        if l.get("so_line_id"):
                            cur.execute("UPDATE commercial_so_lines SET invoiced_qty=invoiced_qty+%s WHERE id=%s", (l["quantity"], l["so_line_id"]))
                    if so:
                        cur.execute("SELECT quantity, delivered_qty, invoiced_qty FROM commercial_so_lines WHERE so_id=%s", (so_id,))
                        new = L.derive_so_status(so["status"], _rows(cur.fetchall()))
                        cur.execute("UPDATE commercial_sales_orders SET status=%s, updated_at=NOW() WHERE id=%s", (new, so_id))
        except Exception as e:
            logger.error("create_invoice: %s", e)
            return None, "Could not create invoice"
        self.add_event(company_id, "invoice", inv["id"], "issued", f"Invoice {inv['invoice_no']} issued", actor)
        if so:
            self.add_event(company_id, "sales_order", so_id, "invoiced", f"Invoice {inv['invoice_no']} issued", actor)
        self._post_invoice_side_effects(company_id, inv, actor)
        return self.get_invoice(company_id, inv["id"]), "Invoice issued"

    def _post_invoice_side_effects(self, company_id: str, inv: dict, actor: str) -> None:
        # VAT income record
        try:
            from vat_data_store import vat_store
            income_id = _uid()
            ok = vat_store.add_income({"income_id": income_id, "company_id": company_id, "contract_date": inv["invoice_date"],
                                       "description": f"Sales invoice {inv['invoice_no']} — {inv['customer_name']}", "category": "Sales",
                                       "gross_amount": float(D(inv["grand_total"])), "vat_type": "standard",
                                       "vat_rate": float(D(inv["vat_rate"]) * 100), "vat_amount": float(D(inv["vat_total"])),
                                       "net_amount": float(D(inv["grand_total"]) - D(inv["vat_total"])), "customer_name": inv["customer_name"],
                                       "customer_tin": inv.get("customer_tin") or "", "invoice_number": inv["invoice_no"], "created_by": actor})
            if ok:
                _execute("UPDATE commercial_invoices SET vat_income_id=%s WHERE id=%s", (income_id, inv["id"]))
        except Exception as e:
            logger.debug("vat income record skipped: %s", e)
        # commission
        if inv.get("sales_rep_id"):
            rep = self.get_rep(company_id, inv["sales_rep_id"])
            if rep and D(rep.get("commission_pct")) > 0:
                base = L.commission_base(inv)
                _execute("""INSERT INTO commercial_commissions(id,company_id,sales_rep_id,invoice_id,base_amount,pct,commission_amount,period)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (invoice_id, sales_rep_id) DO NOTHING""",
                         (_uid(), company_id, rep["id"], inv["id"], base, D(rep["commission_pct"]),
                          L.commission(base, rep["commission_pct"]), L.period_of(inv["invoice_date"])))

    def cancel_invoice(self, company_id: str, invoice_id: str, actor: str = "", reason: str = "") -> bool:
        inv = self.get_invoice(company_id, invoice_id)
        if not inv or D(inv["paid_total"]) > 0 or inv["status"] == "cancelled":
            return False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE commercial_invoices SET status='cancelled', notes=notes||%s, updated_at=NOW() WHERE id=%s",
                                (f"\nCANCELLED: {reason}", invoice_id))
                    for l in inv["lines"]:
                        if l.get("so_line_id"):
                            cur.execute("UPDATE commercial_so_lines SET invoiced_qty=GREATEST(invoiced_qty-%s,0) WHERE id=%s",
                                        (l["quantity"], l["so_line_id"]))
                    cur.execute("DELETE FROM commercial_commissions WHERE invoice_id=%s AND status='accrued'", (invoice_id,))
            if inv.get("vat_income_id"):
                try:
                    from vat_data_store import vat_store
                    vat_store.update_income_record(company_id, inv["vat_income_id"], {"is_active": False})
                except Exception:
                    pass
            self.add_event(company_id, "invoice", invoice_id, "cancelled", reason, actor)
            return True
        except Exception as e:
            logger.error("cancel_invoice: %s", e)
            return False

    # ── receipts / payments ──────────────────────────────────────
    def record_payment(self, company_id: str, invoice_no: str, amount: Any, method: str = "cash", reference: str = "",
                       actor: str = "", received_at=None, source: str = "manual", payment_id: str = None) -> Optional[dict]:
        inv = self.invoice_by_no(company_id, invoice_no)
        if not inv:
            return None
        return self.add_receipt(company_id, inv["id"], {"amount": amount, "method": method, "reference": reference,
                                                        "received_at": received_at, "source": source, "payment_id": payment_id}, actor)

    def add_receipt(self, company_id: str, invoice_id: str, data: dict, actor: str = "") -> Optional[dict]:
        amount = q2(data.get("amount"))
        inv = self.get_invoice(company_id, invoice_id)
        if not inv or amount <= 0 or inv["status"] == "cancelled":
            return None
        method = data.get("method") if data.get("method") in L.PAYMENT_METHODS else "cash"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    rid = _uid()
                    no = _next_no(cur, company_id, "receipt")
                    cur.execute("""INSERT INTO commercial_receipts(id,company_id,receipt_no,invoice_id,amount,method,reference,received_at,received_by,source,payment_id)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,NOW()),%s,%s,%s) RETURNING *""",
                                (rid, company_id, no, invoice_id, amount, method, _txt(data.get("reference"), 120), opt(data.get("received_at")),
                                 actor, data.get("source") or "manual", opt(data.get("payment_id"))))
                    r = _row(cur.fetchone())
                    paid = q2(D(inv["paid_total"]) + amount)
                    status = L.invoice_status_after(D(inv["grand_total"]) - D(inv["credited_total"]), paid, inv.get("due_date"), current=inv["status"])
                    cur.execute("UPDATE commercial_invoices SET paid_total=%s, status=%s, updated_at=NOW() WHERE id=%s", (paid, status, invoice_id))
                    if status == "paid":
                        cur.execute("UPDATE commercial_commissions SET status='paid', paid_at=NOW() WHERE invoice_id=%s AND status='accrued'", (invoice_id,))
            self.add_event(company_id, "invoice", invoice_id, "payment", f"Receipt {no}: {amount:,} via {method} {data.get('reference') or ''}".strip(), actor)
            return r
        except Exception as e:
            logger.error("add_receipt: %s", e)
            return None

    def list_receipts(self, company_id: str, date_from=None, date_to=None, method: str = None) -> List[dict]:
        sql = """SELECT r.*, i.invoice_no, i.customer_name FROM commercial_receipts r JOIN commercial_invoices i ON i.id=r.invoice_id
                 WHERE r.company_id=%s"""
        params: list = [company_id]
        if date_from:
            sql += " AND r.received_at>=%s"; params.append(date_from)
        if date_to:
            sql += " AND r.received_at<%s"; params.append(_date(date_to) + timedelta(days=1) if _date(date_to) else date_to)
        if method:
            sql += " AND r.method=%s"; params.append(method)
        return _query(sql + " ORDER BY r.received_at DESC LIMIT 1000", params)

    def link_mobile_payments(self, company_id: str, actor: str = "system") -> Tuple[int, str]:
        """Match completed inbound rows of the payments module (``payments`` table)
        whose reference contains one of our open invoice numbers."""
        open_inv = _query("""SELECT id, invoice_no, erca_number FROM commercial_invoices WHERE company_id=%s
                             AND status IN ('issued','partially_paid','overdue')""", (company_id,))
        if not open_inv:
            return 0, "No open invoices"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if not _table_exists(cur, "payments"):
                        return 0, "Payments module not available — record receipts manually"
                    cur.execute("""SELECT id, amount, provider, reference, narration, paid_at FROM payments
                                   WHERE company_id=%s AND direction='in' AND status='completed'
                                     AND id NOT IN (SELECT payment_id FROM commercial_receipts WHERE payment_id IS NOT NULL)""", (company_id,))
                    pays = _rows(cur.fetchall())
        except Exception as e:
            logger.debug("link_mobile_payments: %s", e)
            return 0, "Payments lookup failed"
        n = 0
        for p in pays:
            text = f"{p.get('reference') or ''} {p.get('narration') or ''}".upper()
            hit = next((i for i in open_inv if i["invoice_no"].upper() in text or (i["erca_number"] and i["erca_number"].upper() in text)), None)
            if not hit:
                continue
            method = {"telebirr": "telebirr", "cbebirr": "cbebirr", "mpesa": "mpesa"}.get((p.get("provider") or "").lower(), "bank")
            if self.add_receipt(company_id, hit["id"], {"amount": p["amount"], "method": method, "reference": p.get("reference") or p["id"],
                                                        "received_at": p.get("paid_at"), "source": "payments_module", "payment_id": p["id"]}, actor):
                n += 1
        return n, f"Linked {n} mobile-money / bank payment(s) to invoices"

    def mark_overdue(self, company_id: str) -> int:
        r = _query("""UPDATE commercial_invoices SET status='overdue', updated_at=NOW()
                      WHERE company_id=%s AND status IN ('issued','partially_paid') AND due_date < CURRENT_DATE RETURNING id""", (company_id,))
        return len(r or [])

    # ── sales returns & credit notes ─────────────────────────────
    def list_returns(self, company_id: str, status: str = None) -> List[dict]:
        sql = "SELECT r.*, i.invoice_no FROM commercial_returns r LEFT JOIN commercial_invoices i ON i.id=r.invoice_id WHERE r.company_id=%s"
        params: list = [company_id]
        if status:
            sql += " AND r.status=%s"; params.append(status)
        return _query(sql + " ORDER BY r.created_at DESC LIMIT 500", params)

    def get_return(self, company_id: str, rid: str) -> Optional[dict]:
        r = _query("SELECT r.*, i.invoice_no FROM commercial_returns r LEFT JOIN commercial_invoices i ON i.id=r.invoice_id WHERE r.id=%s AND r.company_id=%s",
                   (rid, company_id), one=True)
        if r:
            r["lines"] = _query("SELECT * FROM commercial_return_lines WHERE return_id=%s ORDER BY line_no", (rid,))
        return r

    def create_return(self, company_id: str, invoice_id: str, quantities: Dict[str, Any], data: dict, actor: str = "") -> Optional[dict]:
        inv = self.get_invoice(company_id, invoice_id)
        if not inv or inv["status"] == "cancelled":
            return None
        lines = []
        for l in inv["lines"]:
            qty = min(q3(quantities.get(l["id"], 0)), q3(l["quantity"]))
            if qty > 0:
                lines.append({"item_code": l["item_code"], "description": l["description"], "unit": l.get("unit") or "",
                              "quantity": qty, "unit_price": l["unit_price"], "line_total": L.line_total(qty, l["unit_price"])})
        if not lines:
            return None
        tot = L.document_totals(lines, inv["vat_rate"])
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    rid = _uid()
                    no = _next_no(cur, company_id, "return")
                    cur.execute("""INSERT INTO commercial_returns(id,company_id,rn_no,invoice_id,so_id,customer_id,customer_name,return_date,reason,status,
                                       subtotal,vat_total,amount,notes,prepared_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s) RETURNING *""",
                                (rid, company_id, no, invoice_id, inv.get("so_id"), inv.get("customer_id"), inv["customer_name"],
                                 _date(data.get("return_date")) or date.today(), _txt(data.get("reason"), 400), tot["net"], tot["vat_total"],
                                 tot["grand_total"], _txt(data.get("notes")), actor))
                    r = _row(cur.fetchone())
                    for i, l in enumerate(lines, start=1):
                        cur.execute("""INSERT INTO commercial_return_lines(id,company_id,return_id,line_no,item_code,description,unit,quantity,unit_price,line_total)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), company_id, rid, i, l["item_code"], l["description"], l["unit"], l["quantity"], l["unit_price"], l["line_total"]))
            self.add_event(company_id, "invoice", invoice_id, "return", f"Return {no} raised", actor)
            return r
        except Exception as e:
            logger.error("create_return: %s", e)
            return None

    def decide_return(self, company_id: str, rid: str, approve: bool, actor: str = "") -> Tuple[bool, str]:
        r = self.get_return(company_id, rid)
        if not r or r["status"] != "draft":
            return False, "Return not found or already decided"
        if not approve:
            _execute("UPDATE commercial_returns SET status='rejected', approved_by=%s WHERE id=%s", (actor, rid))
            return True, "Return rejected"
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cn = _next_no(cur, company_id, "credit_note")
                    cur.execute("UPDATE commercial_returns SET status='credited', credit_note_no=%s, approved_by=%s WHERE id=%s", (cn, actor, rid))
                    if r.get("invoice_id"):
                        cur.execute("SELECT grand_total, paid_total, credited_total, due_date, status FROM commercial_invoices WHERE id=%s", (r["invoice_id"],))
                        inv = cur.fetchone()
                        credited = q2(D(inv["credited_total"]) + D(r["amount"]))
                        status = L.invoice_status_after(D(inv["grand_total"]) - credited, inv["paid_total"], inv["due_date"], current=inv["status"])
                        cur.execute("UPDATE commercial_invoices SET credited_total=%s, status=%s, updated_at=NOW() WHERE id=%s",
                                    (credited, status, r["invoice_id"]))
            self.add_event(company_id, "invoice", r.get("invoice_id") or rid, "credit_note", f"Credit note {cn} issued for {r['rn_no']}", actor)
            return True, f"Credit note {cn} issued"
        except Exception as e:
            logger.error("decide_return: %s", e)
            return False, "Could not approve return"

    # ── forecasts ────────────────────────────────────────────────
    def invoiced_history(self, company_id: str, months: int = 12, end_period: str = None) -> Dict[str, Dict[str, Decimal]]:
        """{item_code: {period: qty}} from non-cancelled invoices."""
        end_period = end_period or L.period_of(date.today())
        periods = L.month_sequence(end_period, months)
        start, _ = _month_bounds(periods[0])
        _, end = _month_bounds(periods[-1])
        rows = _query("""SELECT l.item_code, to_char(i.invoice_date,'YYYY-MM') AS period, SUM(l.quantity) AS qty
                         FROM commercial_invoice_lines l JOIN commercial_invoices i ON i.id=l.invoice_id
                         WHERE i.company_id=%s AND i.status<>'cancelled' AND i.invoice_date>=%s AND i.invoice_date<%s
                         GROUP BY l.item_code, 2""", (company_id, start, end))
        out: Dict[str, Dict[str, Decimal]] = {}
        for r in rows:
            out.setdefault(r["item_code"], {})[r["period"]] = q3(r["qty"])
        return out

    def refresh_forecasts(self, company_id: str, target_period: str = None, actor: str = "system") -> int:
        """Recompute the automatic forecast rows for ``target_period`` (default:
        next month). Manual overrides are kept."""
        target_period = target_period or L.next_period(L.period_of(date.today()))
        last = L.month_sequence(target_period, 2)[0]
        hist = self.invoiced_history(company_id, 7, last)
        rows = L.build_forecast(hist, target_period, 6)
        prices = {p["item_code"]: D(p["list_price"]) for p in self.list_products(company_id)}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM commercial_forecasts WHERE company_id=%s AND period=%s AND method<>'manual' AND territory_id IS NULL",
                                (company_id, target_period))
                    for r in rows:
                        cur.execute("""INSERT INTO commercial_forecasts(id,company_id,period,item_code,forecast_qty,forecast_value,method,detail,generated_by)
                                       VALUES (%s,%s,%s,%s,%s,%s,'moving_avg',%s::jsonb,%s)""",
                                    (_uid(), company_id, target_period, r["key"], r["forecast_qty"],
                                     q2(r["forecast_qty"] * prices.get(r["key"], Decimal(0))),
                                     json.dumps({"ma3": str(r["ma3"]), "ma6": str(r["ma6"]), "linear": str(r["linear"]),
                                                 "history": {k: str(v) for k, v in r["history"].items()}}), actor))
            return len(rows)
        except Exception as e:
            logger.error("refresh_forecasts: %s", e)
            return 0

    def save_manual_forecast(self, company_id: str, data: dict, actor: str = "") -> bool:
        period = _txt(data.get("period"), 7)
        if not L.PERIOD_RE.match(period):
            return False
        return _execute("""INSERT INTO commercial_forecasts(id,company_id,period,item_code,territory_id,forecast_qty,forecast_value,method,generated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,'manual',%s)""",
                        (_uid(), company_id, period, opt(_txt(data.get("item_code"), 60).upper()), opt(data.get("territory_id")),
                         q3(data.get("forecast_qty")), q2(data.get("forecast_value")), actor))

    def delete_forecast(self, company_id: str, fid: str) -> bool:
        return _execute("DELETE FROM commercial_forecasts WHERE id=%s AND company_id=%s", (fid, company_id))

    def list_forecasts(self, company_id: str, period: str = None) -> List[dict]:
        sql = """SELECT f.*, p.description, t.name AS territory_name FROM commercial_forecasts f
                 LEFT JOIN commercial_products p ON p.company_id=f.company_id AND p.item_code=f.item_code
                 LEFT JOIN commercial_territories t ON t.id=f.territory_id WHERE f.company_id=%s"""
        params: list = [company_id]
        if period:
            sql += " AND f.period=%s"; params.append(period)
        return _query(sql + " ORDER BY f.period DESC, f.method, f.item_code", params)

    def forecast_periods(self, company_id: str) -> List[str]:
        return [r["period"] for r in _query("SELECT DISTINCT period FROM commercial_forecasts WHERE company_id=%s ORDER BY period DESC", (company_id,))]

    # ── commissions ──────────────────────────────────────────────
    def list_commissions(self, company_id: str, period: str = None, sales_rep_id: str = None, status: str = None) -> List[dict]:
        sql = """SELECT c.*, r.name AS sales_rep_name, i.invoice_no, i.customer_name FROM commercial_commissions c
                 JOIN commercial_sales_reps r ON r.id=c.sales_rep_id JOIN commercial_invoices i ON i.id=c.invoice_id WHERE c.company_id=%s"""
        params: list = [company_id]
        if period:
            sql += " AND c.period=%s"; params.append(period)
        if sales_rep_id:
            sql += " AND c.sales_rep_id=%s"; params.append(sales_rep_id)
        if status:
            sql += " AND c.status=%s"; params.append(status)
        return _query(sql + " ORDER BY c.period DESC, r.name", params)

    def mark_commissions_paid(self, company_id: str, sales_rep_id: str, period: str) -> int:
        r = _query("""UPDATE commercial_commissions SET status='paid', paid_at=NOW()
                      WHERE company_id=%s AND sales_rep_id=%s AND period=%s AND status='accrued' RETURNING id""", (company_id, sales_rep_id, period))
        return len(r or [])

    # ── marketing ────────────────────────────────────────────────
    def list_campaigns(self, company_id: str, status: str = None) -> List[dict]:
        sql = """SELECT c.*, (SELECT COUNT(*) FROM commercial_leads l WHERE l.campaign_id=c.id) AS lead_count,
                        (SELECT COUNT(*) FROM commercial_leads l WHERE l.campaign_id=c.id AND l.status='won') AS won_count,
                        (SELECT COUNT(*) FROM commercial_marketing_events e WHERE e.campaign_id=c.id) AS event_count
                 FROM commercial_campaigns c WHERE c.company_id=%s"""
        params: list = [company_id]
        if status:
            sql += " AND c.status=%s"; params.append(status)
        rows = _query(sql + " ORDER BY c.start_date DESC NULLS LAST, c.created_at DESC", params)
        for r in rows:
            r["roi_pct"] = L.campaign_roi(r["spent"], r["revenue_attributed"])
            r["conversion_pct"] = L.conversion_rate(max(to_int(r["leads"]), to_int(r["lead_count"])), max(to_int(r["conversions"]), to_int(r["won_count"])))
            r["budget_used_pct"] = q2(D(r["spent"]) / D(r["budget"]) * 100) if D(r["budget"]) > 0 else None
        return rows

    def get_campaign(self, company_id: str, cid: str) -> Optional[dict]:
        rows = [c for c in self.list_campaigns(company_id) if c["id"] == cid]
        if not rows:
            return None
        c = rows[0]
        c["events"] = _query("SELECT * FROM commercial_marketing_events WHERE campaign_id=%s ORDER BY event_date DESC NULLS LAST", (cid,))
        c["lead_rows"] = _query("SELECT * FROM commercial_leads WHERE campaign_id=%s ORDER BY created_at DESC", (cid,))
        return c

    def _campaign_cols(self, data: dict) -> tuple:
        status = data.get("status") if data.get("status") in L.CAMPAIGN_STATUSES else "planned"
        return (_txt(data.get("name"), 200), _txt(data.get("channel"), 80), _txt(data.get("segment"), 40), opt(data.get("start_date")),
                opt(data.get("end_date")), D(data.get("budget")), D(data.get("spent")), to_int(data.get("leads")), to_int(data.get("conversions")),
                D(data.get("revenue_attributed")), status, _txt(data.get("owner"), 80), _txt(data.get("notes")))

    def create_campaign(self, company_id: str, data: dict) -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        return _query("""INSERT INTO commercial_campaigns(id,company_id,name,channel,segment,start_date,end_date,budget,spent,leads,conversions,
                             revenue_attributed,status,owner,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id) + self._campaign_cols(data), one=True)

    def update_campaign(self, company_id: str, cid: str, data: dict) -> bool:
        if not _txt(data.get("name")):
            return False
        return _execute("""UPDATE commercial_campaigns SET name=%s,channel=%s,segment=%s,start_date=%s,end_date=%s,budget=%s,spent=%s,leads=%s,
                           conversions=%s,revenue_attributed=%s,status=%s,owner=%s,notes=%s WHERE id=%s AND company_id=%s""",
                        self._campaign_cols(data) + (cid, company_id))

    def add_marketing_event(self, company_id: str, data: dict) -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        return _query("""INSERT INTO commercial_marketing_events(id,company_id,campaign_id,name,event_date,location,attendees,leads,cost,notes)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id, opt(data.get("campaign_id")), _txt(data.get("name"), 200), opt(data.get("event_date")),
                       _txt(data.get("location"), 200), to_int(data.get("attendees")), to_int(data.get("leads")), D(data.get("cost")),
                       _txt(data.get("notes"))), one=True)

    def list_marketing_events(self, company_id: str) -> List[dict]:
        return _query("""SELECT e.*, c.name AS campaign_name FROM commercial_marketing_events e
                         LEFT JOIN commercial_campaigns c ON c.id=e.campaign_id WHERE e.company_id=%s ORDER BY e.event_date DESC NULLS LAST""",
                      (company_id,))

    def delete_marketing_event(self, company_id: str, eid: str) -> bool:
        return _execute("DELETE FROM commercial_marketing_events WHERE id=%s AND company_id=%s", (eid, company_id))

    def list_leads(self, company_id: str, status: str = None, campaign_id: str = None, q: str = None) -> List[dict]:
        sql = "SELECT l.*, c.name AS campaign_name FROM commercial_leads l LEFT JOIN commercial_campaigns c ON c.id=l.campaign_id WHERE l.company_id=%s"
        params: list = [company_id]
        if status:
            sql += " AND l.status=%s"; params.append(status)
        if campaign_id:
            sql += " AND l.campaign_id=%s"; params.append(campaign_id)
        if q:
            sql += " AND (l.name ILIKE %s OR l.company ILIKE %s OR l.phone ILIKE %s)"; params += [f"%{q}%"] * 3
        return _query(sql + " ORDER BY l.created_at DESC LIMIT 1000", params)

    def get_lead(self, company_id: str, lid: str) -> Optional[dict]:
        return _query("SELECT * FROM commercial_leads WHERE id=%s AND company_id=%s", (lid, company_id), one=True)

    def create_lead(self, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        status = data.get("status") if data.get("status") in L.LEAD_STATUSES else "new"
        return _query("""INSERT INTO commercial_leads(id,company_id,campaign_id,name,company,phone,email,segment,source,status,est_value,owner,notes)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id, opt(data.get("campaign_id")), _txt(data.get("name"), 200), _txt(data.get("company"), 200),
                       _txt(data.get("phone"), 40), _txt(data.get("email"), 120), _txt(data.get("segment"), 40), _txt(data.get("source"), 80),
                       status, D(data.get("est_value")), _txt(data.get("owner"), 80) or actor, _txt(data.get("notes"))), one=True)

    def set_lead_status(self, company_id: str, lid: str, status: str) -> bool:
        if status not in L.LEAD_STATUSES:
            return False
        return _execute("UPDATE commercial_leads SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s", (status, lid, company_id))

    def convert_lead(self, company_id: str, lid: str, actor: str = "") -> Optional[dict]:
        lead = self.get_lead(company_id, lid)
        if not lead:
            return None
        if lead.get("converted_customer_id"):
            return self.get_customer(company_id, lead["converted_customer_id"])
        c = self.create_customer(company_id, {"name": lead.get("company") or lead["name"], "contact_person": lead["name"],
                                              "phone": lead.get("phone"), "email": lead.get("email"), "segment": lead.get("segment") or "other",
                                              "notes": f"Converted from lead ({lead.get('source') or 'marketing'})", "created_by": actor})
        if c:
            _execute("UPDATE commercial_leads SET status='won', converted_customer_id=%s, updated_at=NOW() WHERE id=%s", (c["id"], lid))
            if lead.get("campaign_id"):
                _execute("UPDATE commercial_campaigns SET conversions=conversions+1 WHERE id=%s", (lead["campaign_id"],))
        return c

    def list_competitors(self, company_id: str) -> List[dict]:
        return _query("SELECT * FROM commercial_competitors WHERE company_id=%s ORDER BY observed_on DESC NULLS LAST, name", (company_id,))

    def add_competitor(self, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        if not _txt(data.get("name")):
            return None
        return _query("""INSERT INTO commercial_competitors(id,company_id,name,product,price_observed,observed_on,region,notes,created_by)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                      (_uid(), company_id, _txt(data.get("name"), 200), _txt(data.get("product"), 200),
                       q2(data.get("price_observed")) if opt(data.get("price_observed")) is not None else None,
                       opt(data.get("observed_on")), _txt(data.get("region"), 120), _txt(data.get("notes")), actor), one=True)

    def delete_competitor(self, company_id: str, cid: str) -> bool:
        return _execute("DELETE FROM commercial_competitors WHERE id=%s AND company_id=%s", (cid, company_id))

    # ── dashboards ───────────────────────────────────────────────
    @staticmethod
    def empty_dashboard() -> dict:
        return {"orders_open": 0, "orders_pending_approval": 0, "orders_month": 0, "revenue_mtd": Decimal(0), "revenue_ytd": Decimal(0),
                "receivables": Decimal(0), "overdue": Decimal(0), "credit_breaches": 0, "pipeline": {}, "top_customers": [],
                "top_products": [], "monthly_revenue": [], "on_time_pct": Decimal(0), "recent_orders": [], "pending_my_approval": [],
                "proformas_open": 0, "leads_open": 0, "backlog_value": Decimal(0)}

    def dashboard(self, company_id: str, username: str = "", privilege_level: str = "viewer") -> dict:
        d = self.empty_dashboard()
        today = date.today()
        mstart, ystart = today.replace(day=1), today.replace(month=1, day=1)
        try:
            pipeline = _query("SELECT status, COUNT(*) AS c, COALESCE(SUM(grand_total),0) AS v FROM commercial_sales_orders WHERE company_id=%s GROUP BY status", (company_id,))
            d["pipeline"] = {r["status"]: {"count": r["c"], "value": q2(r["v"])} for r in pipeline}
            d["orders_open"] = sum(v["count"] for k, v in d["pipeline"].items() if k not in ("closed", "cancelled", "rejected", "invoiced"))
            d["orders_pending_approval"] = d["pipeline"].get("pending_approval", {}).get("count", 0)
            d["backlog_value"] = q2(sum((v["value"] for k, v in d["pipeline"].items() if k in ("approved", "in_production", "ready", "partially_delivered")), Decimal(0)))
            r = _query("SELECT COUNT(*) AS c FROM commercial_sales_orders WHERE company_id=%s AND order_date>=%s", (company_id, mstart), one=True)
            d["orders_month"] = (r or {}).get("c", 0)
            r = _query("""SELECT COALESCE(SUM(CASE WHEN invoice_date>=%s THEN grand_total END),0) AS mtd,
                                 COALESCE(SUM(CASE WHEN invoice_date>=%s THEN grand_total END),0) AS ytd,
                                 COALESCE(SUM(grand_total-paid_total-credited_total),0) AS recv,
                                 COALESCE(SUM(CASE WHEN due_date<CURRENT_DATE AND status IN ('issued','partially_paid','overdue')
                                                   THEN grand_total-paid_total-credited_total ELSE 0 END),0) AS overdue
                          FROM commercial_invoices WHERE company_id=%s AND status<>'cancelled'""", (mstart, ystart, company_id), one=True) or {}
            d.update(revenue_mtd=q2(r.get("mtd")), revenue_ytd=q2(r.get("ytd")), receivables=q2(r.get("recv")), overdue=q2(r.get("overdue")))
            d["top_customers"] = _query("""SELECT customer_name, COALESCE(SUM(grand_total),0) AS revenue, COUNT(*) AS invoices FROM commercial_invoices
                                           WHERE company_id=%s AND status<>'cancelled' AND invoice_date>=%s GROUP BY customer_name ORDER BY 2 DESC LIMIT 5""",
                                        (company_id, ystart))
            d["top_products"] = _query("""SELECT l.item_code, MAX(l.description) AS description, SUM(l.quantity) AS qty, SUM(l.line_total) AS revenue
                                          FROM commercial_invoice_lines l JOIN commercial_invoices i ON i.id=l.invoice_id
                                          WHERE i.company_id=%s AND i.status<>'cancelled' AND i.invoice_date>=%s GROUP BY l.item_code ORDER BY 4 DESC LIMIT 5""",
                                       (company_id, ystart))
            d["monthly_revenue"] = _query("""SELECT to_char(invoice_date,'YYYY-MM') AS period, COALESCE(SUM(grand_total),0) AS total, COUNT(*) AS invoices
                                             FROM commercial_invoices WHERE company_id=%s AND status<>'cancelled' AND invoice_date>=%s
                                             GROUP BY 1 ORDER BY 1""", (company_id, (mstart - timedelta(days=340)).replace(day=1)))
            d["on_time_pct"] = L.on_time_pct(self.list_dispatches(company_id, status=None)[:500])
            d["recent_orders"] = self.list_sales_orders(company_id, limit=8)
            d["pending_my_approval"] = self.pending_approvals_for(company_id, username, privilege_level) if username else []
            d["credit_breaches"] = len([c for c in self.list_customers(company_id, active_only=True) if c.get("over_limit")])
            r = _query("SELECT COUNT(*) AS c FROM commercial_proformas WHERE company_id=%s AND status IN ('draft','sent')", (company_id,), one=True)
            d["proformas_open"] = (r or {}).get("c", 0)
            r = _query("SELECT COUNT(*) AS c FROM commercial_leads WHERE company_id=%s AND status IN ('new','contacted','qualified')", (company_id,), one=True)
            d["leads_open"] = (r or {}).get("c", 0)
        except Exception as e:
            logger.error("dashboard: %s", e)
        return d

    @staticmethod
    def empty_marketing_dashboard() -> dict:
        return {"campaigns": [], "budget": Decimal(0), "spent": Decimal(0), "revenue": Decimal(0), "leads_by_status": {}, "leads_total": 0,
                "conversion_pct": Decimal(0), "events": 0, "attendees": 0, "by_segment": [], "acquisition": [], "competitors": 0,
                "revenue_by_segment": [], "cost_per_lead": None}

    def marketing_dashboard(self, company_id: str) -> dict:
        d = self.empty_marketing_dashboard()
        try:
            d["campaigns"] = self.list_campaigns(company_id)
            d["budget"] = q2(sum((D(c["budget"]) for c in d["campaigns"]), Decimal(0)))
            d["spent"] = q2(sum((D(c["spent"]) for c in d["campaigns"]), Decimal(0)))
            d["revenue"] = q2(sum((D(c["revenue_attributed"]) for c in d["campaigns"]), Decimal(0)))
            for r in _query("SELECT status, COUNT(*) AS c FROM commercial_leads WHERE company_id=%s GROUP BY status", (company_id,)):
                d["leads_by_status"][r["status"]] = r["c"]
            d["leads_total"] = sum(d["leads_by_status"].values())
            d["conversion_pct"] = L.conversion_rate(d["leads_total"], d["leads_by_status"].get("won", 0))
            d["cost_per_lead"] = q2(d["spent"] / d["leads_total"]) if d["leads_total"] else None
            ev = _query("SELECT COUNT(*) AS c, COALESCE(SUM(attendees),0) AS a FROM commercial_marketing_events WHERE company_id=%s", (company_id,), one=True) or {}
            d["events"], d["attendees"] = ev.get("c", 0), ev.get("a", 0)
            d["by_segment"] = _query("""SELECT segment, COUNT(*) AS customers FROM commercial_customers WHERE company_id=%s AND is_active
                                        GROUP BY segment ORDER BY 2 DESC""", (company_id,))
            d["revenue_by_segment"] = _query("""SELECT COALESCE(c.segment,'unknown') AS segment, COALESCE(SUM(i.grand_total),0) AS revenue, COUNT(DISTINCT i.customer_id) AS customers
                                                FROM commercial_invoices i LEFT JOIN commercial_customers c ON c.id=i.customer_id
                                                WHERE i.company_id=%s AND i.status<>'cancelled' GROUP BY 1 ORDER BY 2 DESC""", (company_id,))
            d["acquisition"] = self.acquisition_retention(company_id, 6)
            d["competitors"] = len(self.list_competitors(company_id))
        except Exception as e:
            logger.error("marketing_dashboard: %s", e)
        return d

    def acquisition_retention(self, company_id: str, months: int = 6) -> List[dict]:
        rows = _query("""SELECT customer_id, to_char(invoice_date,'YYYY-MM') AS period FROM commercial_invoices
                         WHERE company_id=%s AND status<>'cancelled' AND customer_id IS NOT NULL GROUP BY 1, 2""", (company_id,))
        by_cust: Dict[str, List[str]] = {}
        for r in rows:
            by_cust.setdefault(r["customer_id"], []).append(r["period"])
        return [L.retention_split(by_cust, p) for p in L.month_sequence(L.period_of(date.today()), months)]

    # ── reports ──────────────────────────────────────────────────
    # Each report returns {"columns": [...], "rows": [[...]], "totals": [...]|None}
    # for the generic commercial/report.html page and the Excel export.

    @staticmethod
    def _f(filters: dict) -> dict:
        f = dict(filters or {})
        f["date_from"] = _date(f.get("date_from"))
        f["date_to"] = _date(f.get("date_to"))
        for k in ("customer_id", "item_code", "sales_rep_id", "territory_id", "status", "segment", "campaign_id"):
            f[k] = (f.get(k) or "").strip() or None
        return f

    def _where(self, f: dict, alias: str, date_col: str, customer_col: str = "customer_id", rep_col: str = "sales_rep_id",
               terr_col: str = "territory_id") -> Tuple[str, list]:
        sql, params = "", []
        if f.get("date_from"):
            sql += f" AND {alias}.{date_col}>=%s"; params.append(f["date_from"])
        if f.get("date_to"):
            sql += f" AND {alias}.{date_col}<=%s"; params.append(f["date_to"])
        if f.get("customer_id") and customer_col:
            sql += f" AND {alias}.{customer_col}=%s"; params.append(f["customer_id"])
        if f.get("sales_rep_id") and rep_col:
            sql += f" AND {alias}.{rep_col}=%s"; params.append(f["sales_rep_id"])
        if f.get("territory_id") and terr_col:
            sql += f" AND {alias}.{terr_col}=%s"; params.append(f["territory_id"])
        return sql, params

    def _sum_cols(self, rows: List[list], idxs: Iterable[int], label: str = "TOTAL") -> list:
        if not rows:
            return None
        tot = [""] * len(rows[0])
        tot[0] = label
        for i in idxs:
            tot[i] = q2(sum((D(r[i]) for r in rows if r[i] not in (None, "")), Decimal(0)))
        return tot

    def report_proformas(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "p", "proforma_date", rep_col=None, terr_col=None)
        rows = _query(f"SELECT * FROM commercial_proformas p WHERE p.company_id=%s{w} ORDER BY p.proforma_date DESC", [company_id] + p)
        out = [[r["proforma_no"], r["proforma_date"], r["customer_name"], r["valid_until"], r["status"], q2(r["subtotal"]), q2(r["vat_total"]), q2(r["grand_total"])] for r in rows]
        return {"columns": ["Proforma", "Date", "Customer", "Valid until", "Status", "Subtotal", "VAT", "Total"], "rows": out, "totals": self._sum_cols(out, (5, 6, 7)), "date_cols": [1, 3]}

    def report_sales_orders(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "o", "order_date")
        if f.get("status"):
            w += " AND o.status=%s"; p.append(f["status"])
        rows = _query(f"""SELECT o.*, r.name AS rep FROM commercial_sales_orders o LEFT JOIN commercial_sales_reps r ON r.id=o.sales_rep_id
                          WHERE o.company_id=%s{w} ORDER BY o.order_date DESC""", [company_id] + p)
        out = [[r["so_no"], r["order_date"], r["customer_name"], r["required_date"], r["status"], "credit" if r["credit_sale"] else "cash", r.get("rep") or "",
                q2(r["subtotal"]), q2(r["discount_total"]), q2(r["vat_total"]), q2(r["grand_total"])] for r in rows]
        return {"columns": ["SO", "Date", "Customer", "Required", "Status", "Terms", "Sales rep", "Subtotal", "Discount", "VAT", "Total"],
                "rows": out, "totals": self._sum_cols(out, (7, 8, 9, 10)), "date_cols": [1, 3]}

    def report_invoices(self, company_id: str, f: dict, credit_only: bool = False) -> dict:
        w, p = self._where(f, "i", "invoice_date")
        if credit_only:
            w += " AND i.credit_sale"
        rows = _query(f"""SELECT i.*, r.name AS rep FROM commercial_invoices i LEFT JOIN commercial_sales_reps r ON r.id=i.sales_rep_id
                          WHERE i.company_id=%s AND i.status<>'cancelled'{w} ORDER BY i.invoice_date DESC""", [company_id] + p)
        out = [[r["invoice_no"], r["invoice_date"], r["customer_name"], r["so_no"], r["due_date"], r["status"], r.get("rep") or "",
                q2(r["subtotal"]), q2(r["vat_total"]), q2(r["grand_total"]), q2(r["paid_total"]),
                q2(D(r["grand_total"]) - D(r["paid_total"]) - D(r["credited_total"])), L.days_overdue(r["due_date"]) if r["status"] in ("issued", "partially_paid", "overdue") else 0]
               for r in rows]
        return {"columns": ["Invoice", "Date", "Customer", "SO", "Due", "Status", "Sales rep", "Net", "VAT", "Total", "Paid", "Outstanding", "Days overdue"],
                "rows": out, "totals": self._sum_cols(out, (7, 8, 9, 10, 11)), "date_cols": [1, 4]}

    def report_credit_sales(self, company_id: str, f: dict) -> dict:
        return self.report_invoices(company_id, f, credit_only=True)

    def report_customers(self, company_id: str, f: dict) -> dict:
        rows = self.list_customers(company_id, segment=f.get("segment"), territory_id=f.get("territory_id"), sales_rep_id=f.get("sales_rep_id"))
        if f.get("customer_id"):
            rows = [r for r in rows if r["id"] == f["customer_id"]]
        out = [[r["code"], r["name"], r["tin"], r["segment"], r.get("territory_name") or "", r.get("sales_rep_name") or "", r["phone"], r["credit_terms_days"],
                q2(r["credit_limit"]), q2(r["balance"]), q2(r["overdue"]), q2(r["available_credit"]), "YES" if r["over_limit"] else ""] for r in rows]
        return {"columns": ["Code", "Customer", "TIN", "Segment", "Territory", "Sales rep", "Phone", "Terms (days)", "Credit limit", "Balance", "Overdue", "Available", "Over limit"],
                "rows": out, "totals": self._sum_cols(out, (8, 9, 10, 11)), "date_cols": []}

    def report_product_sales(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "i", "invoice_date")
        if f.get("item_code"):
            w += " AND l.item_code=%s"; p.append(f["item_code"])
        rows = _query(f"""SELECT l.item_code, MAX(l.description) AS description, MAX(l.unit) AS unit, SUM(l.quantity) AS qty,
                                 COUNT(DISTINCT i.id) AS invoices, SUM(l.quantity*l.unit_price) AS gross, SUM(l.discount) AS discount, SUM(l.line_total) AS net,
                                 MAX(pr.list_price) AS list_price
                          FROM commercial_invoice_lines l JOIN commercial_invoices i ON i.id=l.invoice_id
                          LEFT JOIN commercial_products pr ON pr.company_id=i.company_id AND pr.item_code=l.item_code
                          WHERE i.company_id=%s AND i.status<>'cancelled'{w} GROUP BY l.item_code ORDER BY net DESC""", [company_id] + p)
        out = [[r["item_code"], r["description"], r["unit"], q3(r["qty"]), r["invoices"], q2(r["list_price"]) if r["list_price"] is not None else "",
                q2(D(r["net"]) / D(r["qty"])) if D(r["qty"]) else "", q2(r["gross"]), q2(r["discount"]), q2(r["net"])] for r in rows]
        return {"columns": ["Item", "Description", "Unit", "Qty sold", "Invoices", "List price", "Avg price", "Gross", "Discount", "Net revenue"],
                "rows": out, "totals": self._sum_cols(out, (7, 8, 9)), "date_cols": []}

    def report_pricing(self, company_id: str, f: dict) -> dict:
        prods = self.list_products(company_id)
        items = self.active_price_items(company_id)
        out = []
        for pr in prods:
            tiers = sorted([i for i in items if i["item_code"] == pr["item_code"]], key=lambda i: D(i["min_qty"]))
            tier_txt = "; ".join(f">= {q3(t['min_qty']):,} @ {q2(t['unit_price']):,}" for t in tiers)
            out.append([pr["item_code"], pr["description"], pr["size_mm2"], pr["color"], pr["unit"], q2(pr["list_price"]), f"{D(pr['vat_rate']) * 100:.0f}%", tier_txt, "active" if pr["is_active"] else "inactive"])
        return {"columns": ["Item", "Description", "Size (mm²)", "Colour", "Unit", "List price", "VAT", "Price-list tiers", "Status"], "rows": out, "totals": None, "date_cols": []}

    def report_discounts(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "o", "order_date")
        rows = _query(f"""SELECT o.customer_name, COUNT(*) AS orders, SUM(o.subtotal) AS gross, SUM(o.discount_total) AS discount, SUM(o.grand_total) AS total
                          FROM commercial_sales_orders o WHERE o.company_id=%s AND o.status NOT IN ('cancelled','rejected'){w}
                          GROUP BY o.customer_name ORDER BY discount DESC""", [company_id] + p)
        out = [[r["customer_name"], r["orders"], q2(r["gross"]), q2(r["discount"]), q2(D(r["discount"]) / D(r["gross"]) * 100) if D(r["gross"]) else Decimal(0), q2(r["total"])] for r in rows]
        return {"columns": ["Customer", "Orders", "Gross", "Discount", "Discount %", "Total (incl. VAT)"], "rows": out, "totals": self._sum_cols(out, (2, 3, 5)), "date_cols": []}

    def report_dispatch(self, company_id: str, f: dict) -> dict:
        rows = self.list_dispatches(company_id, status=f.get("status"), date_from=f.get("date_from"), date_to=f.get("date_to"))
        if f.get("customer_id"):
            ids = {d["id"] for d in self.list_deliveries(company_id) if d.get("customer_id") == f["customer_id"]}
            rows = [r for r in rows if r["di_id"] in ids]
        out = [[r["dispatch_no"], r["di_no"], r["so_no"], r["customer_name"], r["vehicle"], r["driver"], r["dispatched_at"], r["delivered_at"], r["received_by"], r["status"],
                r.get("required_date"), ("on time" if r.get("delivered_at") and r.get("required_date") and L.as_date(r["delivered_at"]) <= L.as_date(r["required_date"]) else ("late" if r.get("delivered_at") and r.get("required_date") else ""))]
               for r in rows]
        return {"columns": ["Dispatch", "DI", "SO", "Customer", "Vehicle", "Driver", "Dispatched", "Delivered", "Received by", "Status", "Required", "On time"],
                "rows": out, "totals": None, "date_cols": [6, 7, 10]}

    def report_returns(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "r", "return_date", rep_col=None, terr_col=None)
        rows = _query(f"""SELECT r.*, i.invoice_no FROM commercial_returns r LEFT JOIN commercial_invoices i ON i.id=r.invoice_id
                          WHERE r.company_id=%s{w} ORDER BY r.return_date DESC""", [company_id] + p)
        out = [[r["rn_no"], r["return_date"], r["customer_name"], r.get("invoice_no") or "", r["reason"], r["status"], r["credit_note_no"], q2(r["subtotal"]), q2(r["vat_total"]), q2(r["amount"])] for r in rows]
        return {"columns": ["Return", "Date", "Customer", "Invoice", "Reason", "Status", "Credit note", "Net", "VAT", "Amount"], "rows": out, "totals": self._sum_cols(out, (7, 8, 9)), "date_cols": [1]}

    def report_fulfilment(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "o", "order_date")
        rows = _query(f"""SELECT o.so_no, o.order_date, o.required_date, o.customer_name, o.status, l.item_code, l.description, l.quantity, l.delivered_qty, l.invoiced_qty, l.reserved_qty, l.mo_no
                          FROM commercial_so_lines l JOIN commercial_sales_orders o ON o.id=l.so_id
                          WHERE o.company_id=%s AND o.status NOT IN ('cancelled','rejected','draft'){w} ORDER BY o.order_date DESC, l.line_no""", [company_id] + p)
        out = []
        for r in rows:
            fu = L.fulfilment(r["quantity"], r["delivered_qty"], r["invoiced_qty"])
            out.append([r["so_no"], r["order_date"], r["required_date"], r["customer_name"], r["status"], r["item_code"], r["description"], r["mo_no"],
                        fu["ordered"], q3(r["reserved_qty"]), fu["delivered"], fu["invoiced"], fu["backlog"], fu["delivered_pct"]])
        return {"columns": ["SO", "Ordered", "Required", "Customer", "Status", "Item", "Description", "MO", "Qty ordered", "Reserved", "Delivered", "Invoiced", "Backlog", "Delivered %"],
                "rows": out, "totals": self._sum_cols(out, (8, 9, 10, 11, 12)), "date_cols": [1, 2]}

    def report_forecast(self, company_id: str, f: dict) -> dict:
        period = f.get("period") or (self.forecast_periods(company_id) or [L.next_period(L.period_of(date.today()))])[0]
        rows = self.list_forecasts(company_id, period)
        hist = self.invoiced_history(company_id, 3, L.month_sequence(period, 2)[0])
        out = []
        for r in rows:
            h = hist.get(r.get("item_code") or "", {})
            det = r.get("detail") or {}
            if isinstance(det, str):
                try:
                    det = json.loads(det)
                except Exception:
                    det = {}
            out.append([r["period"], r.get("item_code") or "(all)", r.get("description") or "", r.get("territory_name") or "", r["method"],
                        q3(sum(h.values(), Decimal(0))), det.get("ma3") or "", det.get("ma6") or "", det.get("linear") or "", q3(r["forecast_qty"]), q2(r["forecast_value"])])
        return {"columns": ["Period", "Item", "Description", "Territory", "Method", "Last 3 months qty", "MA-3", "MA-6", "Trend", "Forecast qty", "Forecast value"],
                "rows": out, "totals": self._sum_cols(out, (9, 10)), "date_cols": [], "period": period}

    def report_rep_performance(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "i", "invoice_date")
        rows = _query(f"""SELECT r.id, r.name, t.name AS territory, r.commission_pct, COUNT(DISTINCT i.id) AS invoices, COUNT(DISTINCT i.customer_id) AS customers,
                                 COALESCE(SUM(i.grand_total),0) AS revenue, COALESCE(SUM(i.subtotal - i.discount_total),0) AS net,
                                 (SELECT COALESCE(SUM(c.commission_amount),0) FROM commercial_commissions c WHERE c.sales_rep_id=r.id) AS commission
                          FROM commercial_sales_reps r LEFT JOIN commercial_territories t ON t.id=r.territory_id
                          LEFT JOIN commercial_invoices i ON i.sales_rep_id=r.id AND i.status<>'cancelled'{w}
                          WHERE r.company_id=%s GROUP BY r.id, r.name, t.name, r.commission_pct ORDER BY revenue DESC""", p + [company_id])
        out = [[r["name"], r.get("territory") or "", r["invoices"], r["customers"], q2(r["net"]), q2(r["revenue"]), q2(r["commission_pct"]), q2(r["commission"])] for r in rows]
        return {"columns": ["Sales rep", "Territory", "Invoices", "Customers", "Net sales", "Revenue (incl. VAT)", "Commission %", "Commission"], "rows": out, "totals": self._sum_cols(out, (4, 5, 7)), "date_cols": []}

    def report_territory_performance(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "i", "invoice_date", terr_col=None)
        rows = _query(f"""SELECT t.name, t.region, (SELECT COUNT(*) FROM commercial_customers c WHERE c.territory_id=t.id) AS customers,
                                 COUNT(DISTINCT i.id) AS invoices, COALESCE(SUM(i.grand_total),0) AS revenue,
                                 COALESCE(SUM(i.grand_total-i.paid_total-i.credited_total),0) AS outstanding
                          FROM commercial_territories t LEFT JOIN commercial_invoices i ON i.territory_id=t.id AND i.status<>'cancelled'{w}
                          WHERE t.company_id=%s GROUP BY t.id, t.name, t.region ORDER BY revenue DESC""", p + [company_id])
        out = [[r["name"], r["region"], r["customers"], r["invoices"], q2(r["revenue"]), q2(r["outstanding"])] for r in rows]
        return {"columns": ["Territory", "Region", "Customers", "Invoices", "Revenue", "Outstanding"], "rows": out, "totals": self._sum_cols(out, (4, 5)), "date_cols": []}

    def report_vat_summary(self, company_id: str, f: dict) -> dict:
        w, p = self._where(f, "i", "invoice_date")
        rows = _query(f"""SELECT to_char(i.invoice_date,'YYYY-MM') AS period, COUNT(*) AS invoices, SUM(i.subtotal) AS gross, SUM(i.discount_total) AS discount,
                                 SUM(i.subtotal-i.discount_total) AS net, SUM(i.vat_total) AS vat, SUM(i.grand_total) AS total, SUM(i.paid_total) AS paid,
                                 SUM(CASE WHEN i.credit_sale THEN i.grand_total ELSE 0 END) AS credit
                          FROM commercial_invoices i WHERE i.company_id=%s AND i.status<>'cancelled'{w} GROUP BY 1 ORDER BY 1 DESC""", [company_id] + p)
        cn = {r["period"]: r for r in _query("""SELECT to_char(return_date,'YYYY-MM') AS period, SUM(subtotal) AS net, SUM(vat_total) AS vat
                                                FROM commercial_returns WHERE company_id=%s AND status='credited' GROUP BY 1""", (company_id,))}
        out = [[r["period"], r["invoices"], q2(r["gross"]), q2(r["discount"]), q2(r["net"]), q2(r["vat"]), q2(r["total"]), q2(r["credit"]), q2(r["paid"]),
                q2((cn.get(r["period"]) or {}).get("net")), q2((cn.get(r["period"]) or {}).get("vat")),
                q2(D(r["vat"]) - D((cn.get(r["period"]) or {}).get("vat")))] for r in rows]
        return {"columns": ["Period", "Invoices", "Gross", "Discount", "Net sales", "Output VAT", "Total", "Credit sales", "Collected", "Credit notes (net)", "Credit notes VAT", "Net output VAT"],
                "rows": out, "totals": self._sum_cols(out, (2, 3, 4, 5, 6, 7, 8, 9, 10, 11)), "date_cols": []}

    def report_campaigns(self, company_id: str, f: dict) -> dict:
        rows = self.list_campaigns(company_id, status=f.get("status"))
        out = [[c["name"], c["channel"], c["segment"], c["start_date"], c["end_date"], c["status"], q2(c["budget"]), q2(c["spent"]), c.get("budget_used_pct") or "",
                max(to_int(c["leads"]), to_int(c["lead_count"])), max(to_int(c["conversions"]), to_int(c["won_count"])), c["conversion_pct"], q2(c["revenue_attributed"]),
                c["roi_pct"] if c["roi_pct"] is not None else ""] for c in rows]
        return {"columns": ["Campaign", "Channel", "Segment", "Start", "End", "Status", "Budget", "Spent", "Budget used %", "Leads", "Conversions", "Conversion %", "Revenue", "ROI %"],
                "rows": out, "totals": self._sum_cols(out, (6, 7, 12)), "date_cols": [3, 4]}

    def report_segmentation(self, company_id: str, f: dict) -> dict:
        rows = _query("""SELECT c.segment, COUNT(DISTINCT c.id) AS customers, COUNT(DISTINCT i.id) AS invoices, COALESCE(SUM(i.grand_total),0) AS revenue,
                                COALESCE(AVG(i.grand_total),0) AS avg_invoice, COALESCE(SUM(i.grand_total-i.paid_total-i.credited_total),0) AS outstanding
                         FROM commercial_customers c LEFT JOIN commercial_invoices i ON i.customer_id=c.id AND i.status<>'cancelled'
                         WHERE c.company_id=%s GROUP BY c.segment ORDER BY revenue DESC""", (company_id,))
        out = [[r["segment"], r["customers"], r["invoices"], q2(r["avg_invoice"]), q2(r["revenue"]), q2(r["outstanding"])] for r in rows]
        acq = self.acquisition_retention(company_id, 6)
        out2 = [[a["period"], a["new"], a["returning"], a["active"]] for a in acq]
        return {"columns": ["Segment", "Customers", "Invoices", "Avg invoice", "Revenue", "Outstanding"], "rows": out, "totals": self._sum_cols(out, (4, 5)), "date_cols": [],
                "extra": {"title": "Customer acquisition & retention (last 6 months)", "columns": ["Period", "New customers", "Returning customers", "Active"], "rows": out2}}

    def report_competitors(self, company_id: str, f: dict) -> dict:
        rows = self.list_competitors(company_id)
        out = [[r["name"], r["product"], q2(r["price_observed"]) if r["price_observed"] is not None else "", r["observed_on"], r["region"], r["notes"]] for r in rows]
        return {"columns": ["Competitor", "Product", "Price observed", "Date", "Region", "Notes"], "rows": out, "totals": None, "date_cols": [3]}

    def report_events(self, company_id: str, f: dict) -> dict:
        rows = self.list_marketing_events(company_id)
        out = [[r["name"], r.get("campaign_name") or "", r["event_date"], r["location"], r["attendees"], r["leads"], q2(r["cost"]),
                q2(D(r["cost"]) / r["leads"]) if to_int(r["leads"]) else "", r["notes"]] for r in rows]
        return {"columns": ["Event", "Campaign", "Date", "Location", "Attendees", "Leads", "Cost", "Cost / lead", "Notes"], "rows": out, "totals": self._sum_cols(out, (4, 5, 6)), "date_cols": [2]}

    def report_marketing_kpis(self, company_id: str, f: dict) -> dict:
        m = self.marketing_dashboard(company_id)
        out = [["Campaigns", len(m["campaigns"])], ["Total budget", m["budget"]], ["Total spent", m["spent"]], ["Revenue attributed", m["revenue"]],
               ["Marketing ROI %", L.campaign_roi(m["spent"], m["revenue"]) if L.campaign_roi(m["spent"], m["revenue"]) is not None else ""],
               ["Leads", m["leads_total"]], ["Leads won", m["leads_by_status"].get("won", 0)], ["Lead conversion %", m["conversion_pct"]],
               ["Cost per lead", m["cost_per_lead"] if m["cost_per_lead"] is not None else ""], ["Events held", m["events"]], ["Event attendees", m["attendees"]],
               ["Competitor observations", m["competitors"]]]
        rev = [[r["segment"], r["customers"], q2(r["revenue"])] for r in m["revenue_by_segment"]]
        return {"columns": ["KPI", "Value"], "rows": out, "totals": None, "date_cols": [],
                "extra": {"title": "Revenue by customer segment", "columns": ["Segment", "Customers", "Revenue"], "rows": rev}}

    REPORTS = {
        "proformas": ("Quotation / proforma report", "report_proformas"),
        "sales_orders": ("Sales order report", "report_sales_orders"),
        "invoices": ("Sales invoice report", "report_invoices"),
        "credit_sales": ("Credit sales & receivables", "report_credit_sales"),
        "customers": ("Customer master, credit limit & balance", "report_customers"),
        "product_sales": ("Product sales & revenue", "report_product_sales"),
        "pricing": ("Pricing & price lists", "report_pricing"),
        "discounts": ("Discounts granted", "report_discounts"),
        "dispatch": ("Delivery, dispatch & shipping", "report_dispatch"),
        "returns": ("Sales returns & credit notes", "report_returns"),
        "fulfilment": ("Order fulfilment (ordered vs delivered vs invoiced)", "report_fulfilment"),
        "forecast": ("Sales forecast & trend", "report_forecast"),
        "rep_performance": ("Sales representative performance", "report_rep_performance"),
        "territory_performance": ("Territory performance", "report_territory_performance"),
        "vat_summary": ("Tax / VAT & sales summary", "report_vat_summary"),
        "campaigns": ("Campaign budget vs spend vs revenue", "report_campaigns"),
        "segmentation": ("Customer segmentation, acquisition & retention", "report_segmentation"),
        "competitors": ("Competitor analysis", "report_competitors"),
        "events": ("Events & engagement", "report_events"),
        "marketing_kpis": ("Marketing KPIs & revenue by segment", "report_marketing_kpis"),
    }

    def run_report(self, company_id: str, key: str, filters: dict) -> Optional[dict]:
        if key not in self.REPORTS:
            return None
        title, fn = self.REPORTS[key]
        try:
            res = getattr(self, fn)(company_id, self._f(filters))
        except Exception as e:
            logger.error("report %s: %s", key, e)
            res = {"columns": [], "rows": [], "totals": None, "date_cols": []}
        res.update(key=key, title=title)
        res.setdefault("date_cols", [])
        return res

    # ── job helpers ──────────────────────────────────────────────
    def companies(self) -> List[str]:
        rows = _query("""SELECT DISTINCT company_id FROM (SELECT company_id FROM commercial_customers UNION SELECT company_id FROM commercial_invoices
                         UNION SELECT company_id FROM commercial_sales_orders) x""")
        return [r["company_id"] for r in rows]

    def credit_breaches(self, company_id: str) -> List[dict]:
        return [c for c in self.list_customers(company_id, active_only=True) if c.get("over_limit")]

    def overdue_invoices(self, company_id: str) -> List[dict]:
        return self.list_invoices(company_id, overdue_only=True, credit_only=True)

    def open_orders_summary(self, company_id: str) -> dict:
        rows = _query("""SELECT status, COUNT(*) AS c, COALESCE(SUM(grand_total),0) AS v FROM commercial_sales_orders
                         WHERE company_id=%s AND status NOT IN ('closed','cancelled','rejected','invoiced') GROUP BY status""", (company_id,))
        late = _query("""SELECT COUNT(*) AS c FROM commercial_sales_orders WHERE company_id=%s AND required_date < CURRENT_DATE
                         AND status IN ('approved','in_production','ready','partially_delivered')""", (company_id,), one=True) or {}
        return {"count": sum(r["c"] for r in rows), "value": q2(sum((D(r["v"]) for r in rows), Decimal(0))),
                "by_status": {r["status"]: {"count": r["c"], "value": q2(r["v"])} for r in rows}, "overdue_deliveries": late.get("c", 0)}


commercial_store = CommercialDataStore()


# ── public module-level API ───────────────────────────────────────
def sales_order_by_no(company_id: str, so_no: str) -> Optional[dict]:
    """Header + lines + approvals + linked documents for ``so_no`` (or None)."""
    try:
        return commercial_store.sales_order_by_no(company_id, so_no)
    except Exception as e:
        logger.error("sales_order_by_no: %s", e)
        return None


def customer_balance(company_id: str, customer_id: str) -> Decimal:
    """Outstanding receivable: invoiced − paid − credited (Decimal, 2 dp)."""
    try:
        return commercial_store.customer_balance(company_id, customer_id)
    except Exception as e:
        logger.error("customer_balance: %s", e)
        return Decimal("0.00")


def record_payment(company_id: str, invoice_no: str, amount, method: str = "cash", reference: str = "", actor: str = "api") -> Optional[dict]:
    """Post a receipt against ``invoice_no`` (internal or ERCA number); updates
    the invoice status. Returns the receipt row or None."""
    try:
        return commercial_store.record_payment(company_id, invoice_no, amount, method, reference, actor, source="api")
    except Exception as e:
        logger.error("record_payment: %s", e)
        return None


def open_orders_summary(company_id: str) -> dict:
    """{count, value, by_status:{status:{count,value}}, overdue_deliveries}."""
    try:
        return commercial_store.open_orders_summary(company_id)
    except Exception as e:
        logger.error("open_orders_summary: %s", e)
        return {"count": 0, "value": Decimal("0.00"), "by_status": {}, "overdue_deliveries": 0}


# ── approval-engine completion hook ───────────────────────────────
def _sync_sales_order(req: dict, status: str, actor: str, comment: str) -> None:
    if (req or {}).get("entity_type") != "sales_order":
        return
    commercial_store.sync_from_approval_engine(req.get("company_id") or "default", str(req.get("entity_id")), status, actor, comment or "")


def _install_hooks() -> None:
    try:
        from approval_data_store import register_on_decided
        register_on_decided(_sync_sales_order)
    except Exception as exc:  # approval module absent — native 3-approver path still works
        logger.debug("commercial approval hook not installed: %s", exc)


_install_hooks()

__all__ = ["CommercialDataStore", "commercial_store", "ensure_schema", "sales_order_by_no", "customer_balance",
           "record_payment", "open_orders_summary"]
