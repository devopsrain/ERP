"""
Contract Management Data Store — PostgreSQL backend.
Tables: contracts, contract_events
"""
from __future__ import annotations
import logging, uuid
from typing import List, Optional
from db import get_conn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    party_type      TEXT NOT NULL DEFAULT 'vendor',   -- vendor|consultant|client|other
    party_name      TEXT NOT NULL DEFAULT '',
    party_reference TEXT NOT NULL DEFAULT '',          -- free-text id of vendor/client
    contract_type   TEXT NOT NULL DEFAULT 'service',  -- service|supply|lease|employment|other
    value           NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'ETB',
    start_date      DATE,
    end_date        DATE,
    status          TEXT NOT NULL DEFAULT 'draft',    -- draft|active|expired|terminated|renewed
    renewal_of      TEXT,                              -- prior contract id
    terms           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_contracts_company ON contracts(company_id);

CREATE TABLE IF NOT EXISTS contract_events (
    id          TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    event_type  TEXT NOT NULL DEFAULT 'note',  -- created|activated|amended|renewed|terminated|expired|note
    note        TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_contract_events_contract ON contract_events(contract_id);
"""


def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC."""
    return value if value not in ("", None) else None


def _num(value):
    return value if value not in ("", None) else 0


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("contract_mgmt schema ready")
    except Exception as e:
        logger.error("contract_mgmt schema init failed: %s", e)


class ContractDataStore:

    def ensure_schema(self):
        ensure_schema()

    # Contracts
    def get_contracts(self, company_id: str, status: str = None, party_type: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM contracts WHERE company_id=%s"
            params = [company_id]
            if status:
                sql += " AND status=%s"; params.append(status)
            if party_type:
                sql += " AND party_type=%s"; params.append(party_type)
            sql += " ORDER BY created_at DESC"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_contracts: %s", e); return []

    def get_contract(self, contract_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM contracts WHERE id=%s AND company_id=%s", (contract_id, company_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("get_contract: %s", e); return None

    def create_contract(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            cid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO contracts(id,company_id,title,party_type,party_name,party_reference,
                           contract_type,value,currency,start_date,end_date,status,renewal_of,terms,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (cid, company_id, data["title"], data.get("party_type", "vendor"),
                         data.get("party_name", ""), data.get("party_reference", ""),
                         data.get("contract_type", "service"), _num(data.get("value")),
                         data.get("currency") or "ETB", _opt(data.get("start_date")), _opt(data.get("end_date")),
                         data.get("status", "draft"), _opt(data.get("renewal_of")),
                         data.get("terms", ""), data.get("created_by", ""))
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_contract: %s", e); return None

    def update_contract(self, contract_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE contracts SET title=%s,party_type=%s,party_name=%s,party_reference=%s,
                           contract_type=%s,value=%s,currency=%s,start_date=%s,end_date=%s,terms=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s""",
                        (data["title"], data.get("party_type", "vendor"), data.get("party_name", ""),
                         data.get("party_reference", ""), data.get("contract_type", "service"),
                         _num(data.get("value")), data.get("currency") or "ETB",
                         _opt(data.get("start_date")), _opt(data.get("end_date")),
                         data.get("terms", ""), contract_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("update_contract: %s", e); return False

    def set_status(self, contract_id: str, company_id: str, status: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE contracts SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                        (status, contract_id, company_id)
                    )
            return True
        except Exception as e:
            logger.error("set_status: %s", e); return False

    def renew_contract(self, contract_id: str, company_id: str, actor: str) -> Optional[dict]:
        """Create a new contract copying fields from the old one, mark the old one renewed."""
        try:
            old = self.get_contract(contract_id, company_id)
            if not old:
                return None
            new = self.create_contract(company_id, {
                "title": old["title"],
                "party_type": old["party_type"],
                "party_name": old["party_name"],
                "party_reference": old["party_reference"],
                "contract_type": old["contract_type"],
                "value": old["value"],
                "currency": old["currency"],
                "start_date": old["start_date"],
                "end_date": old["end_date"],
                "status": "draft",
                "renewal_of": old["id"],
                "terms": old["terms"],
                "created_by": actor,
            })
            if not new:
                return None
            self.set_status(contract_id, company_id, "renewed")
            self.add_event(contract_id, "renewed", f"Renewed as contract {new['id']}", actor)
            self.add_event(new["id"], "created", f"Created as renewal of contract {contract_id}", actor)
            return new
        except Exception as e:
            logger.error("renew_contract: %s", e); return None

    def get_expiring(self, company_id: str, days: int = 60) -> List[dict]:
        """Active contracts whose end_date falls within the next `days` days."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM contracts
                           WHERE company_id=%s AND status='active' AND end_date IS NOT NULL
                             AND end_date >= CURRENT_DATE
                             AND end_date <= CURRENT_DATE + %s * INTERVAL '1 day'
                           ORDER BY end_date""",
                        (company_id, days)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_expiring: %s", e); return []

    def get_stats(self, company_id: str) -> dict:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status, COUNT(*) AS c FROM contracts WHERE company_id=%s GROUP BY status",
                        (company_id,)
                    )
                    by_status = {r["status"]: r["c"] for r in cur.fetchall()}
                    cur.execute(
                        "SELECT COALESCE(SUM(value),0) AS total FROM contracts WHERE company_id=%s AND status='active'",
                        (company_id,)
                    )
                    active_value = cur.fetchone()["total"]
                    return {
                        "total": sum(by_status.values()),
                        "draft": by_status.get("draft", 0),
                        "active": by_status.get("active", 0),
                        "expired": by_status.get("expired", 0),
                        "terminated": by_status.get("terminated", 0),
                        "renewed": by_status.get("renewed", 0),
                        "active_value": float(active_value),
                    }
        except Exception as e:
            logger.error("get_stats: %s", e)
            return {"total": 0, "draft": 0, "active": 0, "expired": 0,
                    "terminated": 0, "renewed": 0, "active_value": 0.0}

    # Events
    def add_event(self, contract_id: str, event_type: str, note: str, actor: str) -> Optional[dict]:
        try:
            eid = str(uuid.uuid4())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO contract_events(id,contract_id,event_type,note,actor)
                           VALUES(%s,%s,%s,%s,%s) RETURNING *""",
                        (eid, contract_id, event_type, note, actor)
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("add_event: %s", e); return None

    def get_events(self, contract_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM contract_events WHERE contract_id=%s ORDER BY created_at DESC",
                        (contract_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_events: %s", e); return []


contract_store = ContractDataStore()
