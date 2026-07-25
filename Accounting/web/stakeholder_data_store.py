"""
Stakeholder Management Data Store — PostgreSQL backend.
Tables: sh_shareholders, sh_share_transactions, sh_dividends, sh_dividend_payments
"""
from __future__ import annotations
import logging, uuid
from decimal import Decimal
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sh_shareholders (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'individual',  -- individual|institutional
    tin         TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    joined_date DATE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sh_shareholders_company ON sh_shareholders(company_id);

CREATE TABLE IF NOT EXISTS sh_share_transactions (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    shareholder_id  TEXT NOT NULL,
    txn_type        TEXT NOT NULL DEFAULT 'purchase',  -- purchase|transfer_in|transfer_out|sale
    shares          NUMERIC(18,2) NOT NULL DEFAULT 0,
    price_per_share NUMERIC(18,2),
    txn_date        DATE,
    reference       TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sh_share_txns_company ON sh_share_transactions(company_id);
CREATE INDEX IF NOT EXISTS idx_sh_share_txns_shareholder ON sh_share_transactions(shareholder_id);

CREATE TABLE IF NOT EXISTS sh_dividends (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL,
    title            TEXT NOT NULL,
    fiscal_year      INT,
    total_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
    per_share_amount NUMERIC(18,6) NOT NULL DEFAULT 0,
    declared_date    DATE,
    payment_date     DATE,
    status           TEXT NOT NULL DEFAULT 'declared',  -- declared|approved|paid
    created_at       TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sh_dividends_company ON sh_dividends(company_id);

CREATE TABLE IF NOT EXISTS sh_dividend_payments (
    id             TEXT PRIMARY KEY,
    dividend_id    TEXT NOT NULL,
    shareholder_id TEXT NOT NULL,
    shares_held    NUMERIC(18,2) NOT NULL DEFAULT 0,
    amount         NUMERIC(18,2) NOT NULL DEFAULT 0,
    paid           BOOLEAN NOT NULL DEFAULT FALSE,
    paid_date      DATE
);
CREATE INDEX IF NOT EXISTS idx_sh_dividend_payments_dividend ON sh_dividend_payments(dividend_id);
"""

# Buy-side transactions add to a holding; sell-side subtract.
_NET_SHARES_EXPR = "SUM(CASE WHEN txn_type IN ('purchase','transfer_in') THEN shares ELSE -shares END)"


def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC."""
    return value if value not in ("", None) else None


def _num(value):
    return value if value not in ("", None) else 0


def _bool(value):
    return value in (True, "on", "true", "1", "yes")


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("stakeholder_mgmt schema ready")
    except Exception as e:
        logger.error("stakeholder_mgmt schema init failed: %s", e)


class StakeholderDataStore:

    def ensure_schema(self):
        ensure_schema()

    # Shareholders
    def get_shareholders(self, company_id: str) -> List[dict]:
        """All shareholders with their current net shareholding attached as `shares`."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT s.*, COALESCE(t.net, 0) AS shares
                            FROM sh_shareholders s
                            LEFT JOIN (SELECT shareholder_id, {_NET_SHARES_EXPR} AS net
                                       FROM sh_share_transactions WHERE company_id=%s
                                       GROUP BY shareholder_id) t ON t.shareholder_id = s.id
                            WHERE s.company_id=%s
                            ORDER BY s.name""",
                        (company_id, company_id)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_shareholders: %s", e); return []

    def get_shareholder(self, shareholder_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM sh_shareholders WHERE id=%s AND company_id=%s",
                                (shareholder_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_shareholder: %s", e); return None

    def create_shareholder(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            sid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO sh_shareholders(id,company_id,name,type,tin,phone,email,address,
                           joined_date,is_active)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (sid, company_id, data["name"], data.get("type", "individual"),
                         data.get("tin", ""), data.get("phone", ""), data.get("email", ""),
                         data.get("address", ""), _opt(data.get("joined_date")), True)
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_shareholder: %s", e); return None

    def update_shareholder(self, shareholder_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE sh_shareholders SET name=%s,type=%s,tin=%s,phone=%s,email=%s,
                           address=%s,joined_date=%s,is_active=%s
                           WHERE id=%s AND company_id=%s""",
                        (data["name"], data.get("type", "individual"), data.get("tin", ""),
                         data.get("phone", ""), data.get("email", ""), data.get("address", ""),
                         _opt(data.get("joined_date")), _bool(data.get("is_active")),
                         shareholder_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_shareholder: %s", e); return False

    # Share transactions
    def get_transactions(self, shareholder_id: str, company_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM sh_share_transactions
                           WHERE shareholder_id=%s AND company_id=%s
                           ORDER BY txn_date DESC NULLS LAST, created_at DESC""",
                        (shareholder_id, company_id)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_transactions: %s", e); return []

    def create_transaction(self, company_id: str, shareholder_id: str, data: dict) -> Optional[dict]:
        try:
            tid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO sh_share_transactions(id,company_id,shareholder_id,txn_type,
                           shares,price_per_share,txn_date,reference,notes)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (tid, company_id, shareholder_id, data.get("txn_type", "purchase"),
                         _num(data.get("shares")), _opt(data.get("price_per_share")),
                         _opt(data.get("txn_date")), data.get("reference", ""),
                         data.get("notes", ""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_transaction: %s", e); return None

    def get_net_shares(self, shareholder_id: str, company_id: str) -> Decimal:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT COALESCE({_NET_SHARES_EXPR}, 0) AS net
                            FROM sh_share_transactions
                            WHERE shareholder_id=%s AND company_id=%s""",
                        (shareholder_id, company_id)
                    )
                    return cur.fetchone()["net"]
        except Exception as e:
            logger.error("get_net_shares: %s", e); return Decimal(0)

    def get_total_shares(self, company_id: str) -> Decimal:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT COALESCE({_NET_SHARES_EXPR}, 0) AS total
                            FROM sh_share_transactions WHERE company_id=%s""",
                        (company_id,)
                    )
                    return cur.fetchone()["total"]
        except Exception as e:
            logger.error("get_total_shares: %s", e); return Decimal(0)

    def get_equity_structure(self, company_id: str) -> List[dict]:
        """Shareholders holding shares, with their % of total shares outstanding."""
        try:
            holders = [s for s in self.get_shareholders(company_id) if float(s["shares"]) > 0]
            total = sum(float(s["shares"]) for s in holders)
            for s in holders:
                s["pct"] = (float(s["shares"]) / total * 100) if total else 0.0
            holders.sort(key=lambda s: float(s["shares"]), reverse=True)
            return holders
        except Exception as e:
            logger.error("get_equity_structure: %s", e); return []

    def get_stats(self, company_id: str) -> dict:
        try:
            shareholders = self.get_shareholders(company_id)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*) AS c, COALESCE(SUM(total_amount),0) AS total
                           FROM sh_dividends WHERE company_id=%s""",
                        (company_id,)
                    )
                    div = cur.fetchone()
            return {
                "total": len(shareholders),
                "active": sum(1 for s in shareholders if s["is_active"]),
                "individual": sum(1 for s in shareholders if s["type"] == "individual"),
                "institutional": sum(1 for s in shareholders if s["type"] == "institutional"),
                "total_shares": float(sum(Decimal(str(s["shares"])) for s in shareholders)) if shareholders else 0.0,
                "dividend_count": div["c"],
                "dividend_total": float(div["total"]),
            }
        except Exception as e:
            logger.error("get_stats: %s", e)
            return {"total": 0, "active": 0, "individual": 0, "institutional": 0,
                    "total_shares": 0.0, "dividend_count": 0, "dividend_total": 0.0}

    # Dividends
    def get_dividends(self, company_id: str, status: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM sh_dividends WHERE company_id=%s"
            params = [company_id]
            if status:
                sql += " AND status=%s"; params.append(status)
            sql += " ORDER BY created_at DESC"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_dividends: %s", e); return []

    def get_dividend(self, dividend_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM sh_dividends WHERE id=%s AND company_id=%s",
                                (dividend_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_dividend: %s", e); return None

    def create_dividend(self, company_id: str, data: dict) -> Optional[dict]:
        """Create a dividend and auto-generate its payment rows pro-rata
        from each shareholder's current net holding."""
        try:
            did = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO sh_dividends(id,company_id,title,fiscal_year,total_amount,
                           per_share_amount,declared_date,payment_date,status)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (did, company_id, data["title"], _opt(data.get("fiscal_year")),
                         _num(data.get("total_amount")), _num(data.get("per_share_amount")),
                         _opt(data.get("declared_date")), _opt(data.get("payment_date")),
                         data.get("status", "declared"))
                    )
                    dividend = dict(cur.fetchone())
            self._generate_payments(company_id, dividend)
            return dividend
        except Exception as e:
            logger.error("create_dividend: %s", e); return None

    def _generate_payments(self, company_id: str, dividend: dict) -> None:
        """Pro-rata sh_dividend_payments rows from current holdings."""
        try:
            holders = [s for s in self.get_shareholders(company_id)
                       if Decimal(str(s["shares"])) > 0]
            total = sum(Decimal(str(s["shares"])) for s in holders)
            per_share = Decimal(str(dividend.get("per_share_amount") or 0))
            if per_share == 0 and total > 0:
                per_share = Decimal(str(dividend.get("total_amount") or 0)) / total
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for s in holders:
                        shares = Decimal(str(s["shares"]))
                        amount = (shares * per_share).quantize(Decimal("0.01"))
                        cur.execute(
                            """INSERT INTO sh_dividend_payments(id,dividend_id,shareholder_id,
                               shares_held,amount) VALUES(%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), dividend["id"], s["id"], shares, amount)
                        )
        except Exception as e:
            logger.error("_generate_payments: %s", e)

    def get_dividend_payments(self, dividend_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT p.*, s.name AS shareholder_name, s.type AS shareholder_type
                           FROM sh_dividend_payments p
                           JOIN sh_shareholders s ON s.id = p.shareholder_id
                           WHERE p.dividend_id=%s
                           ORDER BY s.name""",
                        (dividend_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_dividend_payments: %s", e); return []

    def get_shareholder_dividends(self, shareholder_id: str) -> List[dict]:
        """Dividend payment history for one shareholder."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT p.*, d.title, d.fiscal_year, d.status AS dividend_status,
                                  d.declared_date, d.id AS dividend_id
                           FROM sh_dividend_payments p
                           JOIN sh_dividends d ON d.id = p.dividend_id
                           WHERE p.shareholder_id=%s
                           ORDER BY d.created_at DESC""",
                        (shareholder_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_shareholder_dividends: %s", e); return []

    def mark_payment_paid(self, dividend_id: str, payment_id: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE sh_dividend_payments SET paid=TRUE, paid_date=CURRENT_DATE
                           WHERE id=%s AND dividend_id=%s""",
                        (payment_id, dividend_id)
                    )
            return True
        except Exception as e:
            logger.error("mark_payment_paid: %s", e); return False

    def set_dividend_status(self, dividend_id: str, company_id: str, status: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE sh_dividends SET status=%s WHERE id=%s AND company_id=%s",
                        (status, dividend_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("set_dividend_status: %s", e); return False


stakeholder_store = StakeholderDataStore()
