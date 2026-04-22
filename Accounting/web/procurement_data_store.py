"""
Procurement Data Store — PostgreSQL backend.
Tables: proc_vendors, proc_purchase_requisitions, proc_po_lines,
        proc_purchase_orders, proc_grn, proc_invoices, proc_tenders, proc_tender_bids
"""
from __future__ import annotations
import logging, uuid
from datetime import datetime
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proc_vendors (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT '',
    tin_number   TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',
    address      TEXT NOT NULL DEFAULT '',
    rating       NUMERIC(3,1) NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'active',  -- active | blacklisted | suspended
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_vendors_company ON proc_vendors(company_id);

CREATE TABLE IF NOT EXISTS proc_purchase_requisitions (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    department      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft|pending|approved|rejected
    requested_by    TEXT NOT NULL,
    approved_by     TEXT,
    rejection_note  TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_pr_company ON proc_purchase_requisitions(company_id);
CREATE INDEX IF NOT EXISTS idx_proc_pr_status  ON proc_purchase_requisitions(status);

CREATE TABLE IF NOT EXISTS proc_purchase_orders (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    pr_id           TEXT,
    vendor_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    delivery_date   DATE,
    payment_terms   TEXT NOT NULL DEFAULT 'net30',
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',  -- open|delivered|matched|cancelled
    grn_received    BOOLEAN NOT NULL DEFAULT FALSE,
    invoice_matched BOOLEAN NOT NULL DEFAULT FALSE,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_po_company ON proc_purchase_orders(company_id);

CREATE TABLE IF NOT EXISTS proc_po_lines (
    id           TEXT PRIMARY KEY,
    po_id        TEXT NOT NULL,
    description  TEXT NOT NULL,
    quantity     NUMERIC(12,4) NOT NULL DEFAULT 1,
    unit         TEXT NOT NULL DEFAULT 'unit',
    unit_price   NUMERIC(18,2) NOT NULL DEFAULT 0,
    total        NUMERIC(18,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_proc_po_lines ON proc_po_lines(po_id);

CREATE TABLE IF NOT EXISTS proc_grn (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    po_id           TEXT NOT NULL,
    received_date   DATE NOT NULL,
    received_by     TEXT NOT NULL,
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted|rejected
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_grn_po ON proc_grn(po_id);

CREATE TABLE IF NOT EXISTS proc_invoices (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    po_id           TEXT NOT NULL,
    invoice_number  TEXT NOT NULL,
    invoice_date    DATE NOT NULL,
    amount          NUMERIC(18,2) NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|matched|paid|disputed
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_invoices_po ON proc_invoices(po_id);

CREATE TABLE IF NOT EXISTS proc_tenders (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    rfq_deadline    TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'open',  -- open|closed|awarded|cancelled
    awarded_to      TEXT,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_tenders_company ON proc_tenders(company_id);

CREATE TABLE IF NOT EXISTS proc_tender_bids (
    id          TEXT PRIMARY KEY,
    tender_id   TEXT NOT NULL,
    vendor_id   TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT '',
    submitted_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proc_bids_tender ON proc_tender_bids(tender_id);
"""


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("procurement schema ready")
    except Exception as e:
        logger.error("procurement schema init failed: %s", e)


class ProcurementDataStore:

    def ensure_schema(self):
        ensure_schema()

    # Vendors
    def get_vendors(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM proc_vendors WHERE company_id=%s ORDER BY name", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_vendors: %s", e); return []

    def get_vendor(self, vendor_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM proc_vendors WHERE id=%s AND company_id=%s", (vendor_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_vendor: %s", e); return None

    def create_vendor(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            vid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO proc_vendors(id,company_id,name,category,tin_number,email,phone,address,rating,status)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (vid, company_id, data["name"], data.get("category",""), data.get("tin_number",""),
                         data.get("email",""), data.get("phone",""), data.get("address",""),
                         data.get("rating",0), data.get("status","active"))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_vendor: %s", e); return None

    def update_vendor_status(self, vendor_id: str, status: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE proc_vendors SET status=%s WHERE id=%s AND company_id=%s", (status, vendor_id, company_id))
            return True
        except Exception as e:
            logger.error("update_vendor_status: %s", e); return False

    # Purchase Requisitions
    def get_prs(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM proc_purchase_requisitions WHERE company_id=%s ORDER BY created_at DESC", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_prs: %s", e); return []

    def get_pr(self, pr_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM proc_purchase_requisitions WHERE id=%s AND company_id=%s", (pr_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_pr: %s", e); return None

    def create_pr(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            pid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO proc_purchase_requisitions(id,company_id,department,title,description,total_amount,requested_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (pid, company_id, data["department"], data["title"],
                         data.get("description",""), data.get("total_amount",0), data.get("requested_by",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_pr: %s", e); return None

    def approve_pr(self, pr_id: str, company_id: str, approver: str, approved: bool, note: str = "") -> dict:
        """Returns {ok, error}. Checks budget before approving."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM proc_purchase_requisitions WHERE id=%s AND company_id=%s", (pr_id, company_id))
                    pr = cur.fetchone()
                    if not pr:
                        return {"ok": False, "error": "PR not found"}
                    if pr["status"] != "pending":
                        return {"ok": False, "error": f"PR is already {pr['status']}"}
                    if approved:
                        # Budget check against fin_budgets if available
                        try:
                            cur.execute(
                                """SELECT COALESCE(SUM(budget_amount),0) AS avail FROM fin_budgets
                                   WHERE company_id=%s AND cost_center=%s""",
                                (company_id, pr["department"])
                            )
                            row = cur.fetchone()
                            if row and float(row["avail"]) > 0 and float(pr["total_amount"]) > float(row["avail"]):
                                return {"ok": False, "error": f"PR amount exceeds department budget (available: {row['avail']:,.2f} ETB)"}
                        except Exception:
                            pass  # fin_budgets may not exist yet
                        new_status = "approved"
                    else:
                        new_status = "rejected"
                    cur.execute(
                        "UPDATE proc_purchase_requisitions SET status=%s,approved_by=%s,rejection_note=%s,updated_at=NOW() WHERE id=%s",
                        (new_status, approver, note if not approved else None, pr_id)
                    )
            return {"ok": True, "status": new_status}
        except Exception as e:
            logger.error("approve_pr: %s", e); return {"ok": False, "error": str(e)}

    def submit_pr(self, pr_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE proc_purchase_requisitions SET status='pending',updated_at=NOW() WHERE id=%s AND company_id=%s AND status='draft'",
                        (pr_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("submit_pr: %s", e); return False

    # Purchase Orders
    def get_pos(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT po.*, v.name AS vendor_name FROM proc_purchase_orders po
                           LEFT JOIN proc_vendors v ON po.vendor_id=v.id
                           WHERE po.company_id=%s ORDER BY po.created_at DESC""",
                        (company_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_pos: %s", e); return []

    def get_po(self, po_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT po.*, v.name AS vendor_name FROM proc_purchase_orders po
                           LEFT JOIN proc_vendors v ON po.vendor_id=v.id
                           WHERE po.id=%s AND po.company_id=%s""",
                        (po_id, company_id)
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    po = dict(row)
                    cur.execute("SELECT * FROM proc_po_lines WHERE po_id=%s", (po_id,))
                    po["lines"] = [dict(r) for r in cur.fetchall()]
                    return po
        except Exception as e:
            logger.error("get_po: %s", e); return None

    def create_po(self, company_id: str, data: dict, lines: List[dict]) -> Optional[dict]:
        try:
            po_id = str(uuid.uuid4())
            total = sum(float(l.get("total", 0)) for l in lines)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO proc_purchase_orders(id,company_id,pr_id,vendor_id,title,delivery_date,payment_terms,total_amount,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (po_id, company_id, data.get("pr_id"), data["vendor_id"], data["title"],
                         data.get("delivery_date"), data.get("payment_terms","net30"),
                         total, data.get("created_by",""))
                    )
                    po = dict(cur.fetchone())
                    for line in lines:
                        lid = str(uuid.uuid4())
                        qty = float(line.get("quantity", 1))
                        price = float(line.get("unit_price", 0))
                        cur.execute(
                            "INSERT INTO proc_po_lines(id,po_id,description,quantity,unit,unit_price,total) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                            (lid, po_id, line["description"], qty, line.get("unit","unit"), price, qty * price)
                        )
                    return po
        except Exception as e:
            logger.error("create_po: %s", e); return None

    # Three-Way Match
    def record_grn(self, company_id: str, po_id: str, data: dict) -> Optional[dict]:
        try:
            gid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO proc_grn(id,company_id,po_id,received_date,received_by,notes) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                        (gid, company_id, po_id, data["received_date"], data.get("received_by",""), data.get("notes",""))
                    )
                    grn = dict(cur.fetchone())
                    cur.execute("UPDATE proc_purchase_orders SET grn_received=TRUE WHERE id=%s", (po_id,))
                    self._check_three_way_match(cur, po_id)
                    return grn
        except Exception as e:
            logger.error("record_grn: %s", e); return None

    def record_invoice(self, company_id: str, po_id: str, data: dict) -> Optional[dict]:
        try:
            iid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO proc_invoices(id,company_id,po_id,invoice_number,invoice_date,amount) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                        (iid, company_id, po_id, data["invoice_number"], data["invoice_date"], data.get("amount",0))
                    )
                    inv = dict(cur.fetchone())
                    self._check_three_way_match(cur, po_id)
                    return inv
        except Exception as e:
            logger.error("record_invoice: %s", e); return None

    def _check_three_way_match(self, cur, po_id: str):
        """If PO has both GRN and Invoice, mark as matched."""
        cur.execute("SELECT grn_received, invoice_matched FROM proc_purchase_orders WHERE id=%s", (po_id,))
        row = cur.fetchone()
        if not row:
            return
        cur.execute("SELECT COUNT(*) AS c FROM proc_invoices WHERE po_id=%s", (po_id,))
        has_invoice = cur.fetchone()["c"] > 0
        if row["grn_received"] and has_invoice:
            cur.execute(
                "UPDATE proc_purchase_orders SET invoice_matched=TRUE, status='matched' WHERE id=%s",
                (po_id,)
            )

    def get_three_way_status(self, po_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT grn_received, invoice_matched, status FROM proc_purchase_orders WHERE id=%s", (po_id,))
                    row = cur.fetchone()
                    if not row:
                        return {}
                    cur.execute("SELECT * FROM proc_grn WHERE po_id=%s ORDER BY created_at DESC LIMIT 1", (po_id,))
                    grn = cur.fetchone()
                    cur.execute("SELECT * FROM proc_invoices WHERE po_id=%s ORDER BY created_at DESC LIMIT 1", (po_id,))
                    inv = cur.fetchone()
                    return {
                        "po_received": True,
                        "grn_received": bool(row["grn_received"]),
                        "invoice_matched": bool(row["invoice_matched"]),
                        "status": row["status"],
                        "grn": dict(grn) if grn else None,
                        "invoice": dict(inv) if inv else None
                    }
        except Exception as e:
            logger.error("get_three_way_status: %s", e); return {}

    # Tenders
    def get_tenders(self, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM proc_tenders WHERE company_id=%s ORDER BY created_at DESC", (company_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_tenders: %s", e); return []

    def create_tender(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            tid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO proc_tenders(id,company_id,title,description,rfq_deadline,created_by) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                        (tid, company_id, data["title"], data.get("description",""), data.get("rfq_deadline"), data.get("created_by",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_tender: %s", e); return None

    def submit_bid(self, tender_id: str, data: dict) -> Optional[dict]:
        try:
            bid_id = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO proc_tender_bids(id,tender_id,vendor_id,vendor_name,amount,notes) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                        (bid_id, tender_id, data.get("vendor_id",""), data["vendor_name"], data.get("amount",0), data.get("notes",""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("submit_bid: %s", e); return None

    def get_bid_comparison(self, tender_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM proc_tender_bids WHERE tender_id=%s ORDER BY amount ASC",
                        (tender_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_bid_comparison: %s", e); return []

    def award_tender(self, tender_id: str, company_id: str, vendor_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE proc_tenders SET status='awarded', awarded_to=%s WHERE id=%s AND company_id=%s",
                        (vendor_id, tender_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("award_tender: %s", e); return False

    def get_stats(self, company_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM proc_vendors WHERE company_id=%s AND status='active'", (company_id,))
                    vendors = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) AS c FROM proc_purchase_requisitions WHERE company_id=%s AND status='pending'", (company_id,))
                    pending_prs = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) AS c FROM proc_purchase_orders WHERE company_id=%s AND status='open'", (company_id,))
                    open_pos = cur.fetchone()["c"]
                    cur.execute("SELECT COUNT(*) AS c FROM proc_tenders WHERE company_id=%s AND status='open'", (company_id,))
                    open_tenders = cur.fetchone()["c"]
                    return {"vendors": vendors, "pending_prs": pending_prs, "open_pos": open_pos, "open_tenders": open_tenders}
        except Exception as e:
            logger.error("get_stats: %s", e)
            return {"vendors": 0, "pending_prs": 0, "open_pos": 0, "open_tenders": 0}


procurement_store = ProcurementDataStore()
