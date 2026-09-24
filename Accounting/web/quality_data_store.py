"""
Quality Management Data Store — PostgreSQL backend.

Tables (all carry ``company_id TEXT NOT NULL DEFAULT 'default'``)
    quality_sequences                  gapless numbering (COA / CMP / CAPA / NCR / AUD / inspection refs)
    quality_spec_sets, quality_spec_params
    quality_inspection_lines           parameter lines of every inspection kind
    quality_rm_inspections             raw-material inspection
    quality_inprocess_inspections      cable in-process inspection
    quality_insulation_inspections     wire insulation inspection
    quality_final_inspections          final product inspection (+ certificate of analysis number)
    quality_packing_summaries          wire packing summary
    quality_conductor_delivery_reports AAC / ABC delivery report
    quality_equipment, quality_calibration_records
    quality_complaints, quality_capa, quality_ncrs
    quality_audits, quality_audit_checklist

Integration API (Python, importable by other modules — every call is safe
when the tables are empty or the database is down: it returns None / [] / {}):

    from quality_data_store import (latest_rm_result, inspections_for_order,
                                    open_quality_issues, record_procurement_sample_result)

    latest_rm_result(company_id, material_code=None, lot_no=None) -> dict | None
        Most recent raw-material inspection for the material / lot:
        {"id", "ref_no", "overall_result", "disposition", "status", "inspection_date",
         "supplier_name", "material_code", "lot_no", "released": bool}
        ``released`` is True only for an approved inspection that passed and was
        accepted — manufacturing gates production-order release on it.

    inspections_for_order(company_id, order_number)
        -> {"in_process": [...], "insulation": [...], "final": [...], "packing": [...],
            "conductor": [...]}  (headers only, newest first)

    open_quality_issues(company_id) -> {"open_ncrs", "open_capas", "overdue_capas",
        "open_complaints", "equipment_due_soon", "equipment_expired", "failed_inspections_30d"}

    record_procurement_sample_result(company_id, supplier, material_code, pr_no, result,
                                     lot_no="", quantity=None, unit="", remarks="", actor="",
                                     supplier_id=None, invoice_number="", invoice_date=None)
        -> dict | None  Creates a submitted raw-material inspection from a
        procurement sample approval (result "pass" | "fail").

Webhook events (emitted when ``webhook_data_store.emit`` is importable):
    quality.inspection_failed, quality.ncr_raised
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

from db import get_conn
from quality_forms import (
    DUE_SOON_DAYS, INSPECTION_KINDS, calibration_status, capa_effective_status,
    evaluate_lines, format_number, next_due_date, overall_result, to_date, to_num,
)

logger = logging.getLogger(__name__)

_COMMON_HEADER = """
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL DEFAULT 'default',
    ref_no           TEXT NOT NULL DEFAULT '',
    inspection_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    spec_set_id      TEXT,
    status           TEXT NOT NULL DEFAULT 'draft',      -- draft|submitted|approved
    overall_result   TEXT NOT NULL DEFAULT 'na',         -- pass|fail|conditional|na
    remarks          TEXT NOT NULL DEFAULT '',
    prepared_by      TEXT NOT NULL DEFAULT '',
    received_by      TEXT NOT NULL DEFAULT '',
    inspected_by     TEXT NOT NULL DEFAULT '',
    checked_by       TEXT NOT NULL DEFAULT '',
    approved_by      TEXT NOT NULL DEFAULT '',
    submitted_at     TIMESTAMP,
    approved_at      TIMESTAMP,
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP NOT NULL DEFAULT NOW()"""

_COMMON_COLS = ("ref_no", "inspection_date", "spec_set_id", "status", "overall_result", "remarks",
                "prepared_by", "received_by", "inspected_by", "checked_by", "approved_by")


def _col_ddl(field: dict) -> str:
    t = field["type"]
    if t == "number":
        return f"    {field['name']} NUMERIC(18,4)"
    if t == "date":
        return f"    {field['name']} DATE"
    return f"    {field['name']} TEXT NOT NULL DEFAULT ''"


def _inspection_ddl() -> str:
    parts = []
    for kind, cfg in INSPECTION_KINDS.items():
        cols = [_col_ddl(f) for f in cfg["fields"]]
        cols += [f"    {h} TEXT" for h in cfg.get("hidden", ())]
        parts.append(f"CREATE TABLE IF NOT EXISTS {cfg['table']} (\n{_COMMON_HEADER},\n" + ",\n".join(cols) + "\n);")
        parts.append(f"CREATE INDEX IF NOT EXISTS idx_{cfg['table']}_company ON {cfg['table']}(company_id, inspection_date DESC);")
    return "\n".join(parts)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS quality_sequences (
    company_id  TEXT NOT NULL DEFAULT 'default',
    kind        TEXT NOT NULL,
    year        INT  NOT NULL,
    last_no     INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, kind, year)
);

CREATE TABLE IF NOT EXISTS quality_spec_sets (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    applies_to    TEXT NOT NULL DEFAULT 'final',     -- raw_material|in_process|final|packaging|audit
    product_code  TEXT,
    product_type  TEXT,
    standard      TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT '1',
    status        TEXT NOT NULL DEFAULT 'draft',     -- draft|active|obsolete
    notes         TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_spec_sets_company ON quality_spec_sets(company_id, applies_to);

CREATE TABLE IF NOT EXISTS quality_spec_params (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    spec_set_id   TEXT NOT NULL,
    parameter     TEXT NOT NULL,
    unit          TEXT NOT NULL DEFAULT '',
    spec_kind     TEXT NOT NULL DEFAULT 'range',     -- range|min|max|nominal
    nominal       NUMERIC(18,4),
    min_value     NUMERIC(18,4),
    max_value     NUMERIC(18,4),
    tolerance_pct NUMERIC(9,4),
    method        TEXT NOT NULL DEFAULT '',
    sample_size   TEXT NOT NULL DEFAULT '',
    mandatory     BOOLEAN NOT NULL DEFAULT TRUE,
    seq           INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_quality_spec_params_set ON quality_spec_params(spec_set_id, seq);

CREATE TABLE IF NOT EXISTS quality_inspection_lines (
    id               TEXT PRIMARY KEY,
    company_id       TEXT NOT NULL DEFAULT 'default',
    inspection_kind  TEXT NOT NULL,                  -- rm|inprocess|insulation|final|packing|conductor
    inspection_id    TEXT NOT NULL,
    seq              INT NOT NULL DEFAULT 0,
    parameter        TEXT NOT NULL,
    unit             TEXT NOT NULL DEFAULT '',
    spec_kind        TEXT NOT NULL DEFAULT 'range',
    spec_value       NUMERIC(18,4),
    spec_min         NUMERIC(18,4),
    spec_max         NUMERIC(18,4),
    tolerance_pct    NUMERIC(9,4),
    measured_value   NUMERIC(18,4),
    result           TEXT NOT NULL DEFAULT 'na',     -- pass|fail|na
    mandatory        BOOLEAN NOT NULL DEFAULT TRUE,
    method           TEXT NOT NULL DEFAULT '',
    remarks          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_quality_lines_inspection ON quality_inspection_lines(inspection_kind, inspection_id, seq);
CREATE INDEX IF NOT EXISTS idx_quality_lines_param ON quality_inspection_lines(company_id, parameter);

{_inspection_ddl()}

CREATE TABLE IF NOT EXISTS quality_equipment (
    id                           TEXT PRIMARY KEY,
    company_id                   TEXT NOT NULL DEFAULT 'default',
    equipment_name               TEXT NOT NULL,
    equipment_tag                TEXT NOT NULL,
    location                     TEXT NOT NULL DEFAULT '',
    calibration_frequency_months INT NOT NULL DEFAULT 12,
    last_calibration_date        DATE,
    next_due_date                DATE,
    calibration_standard         TEXT NOT NULL DEFAULT '',
    performed_by                 TEXT NOT NULL DEFAULT 'external',   -- internal|external
    provider                     TEXT NOT NULL DEFAULT '',
    certificate_number           TEXT NOT NULL DEFAULT '',
    certificate_doc_id           TEXT,
    status                       TEXT NOT NULL DEFAULT 'unknown',    -- valid|due_soon|expired|unknown
    notes                        TEXT NOT NULL DEFAULT '',
    created_at                   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at                   TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, equipment_tag)
);

CREATE TABLE IF NOT EXISTS quality_calibration_records (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL DEFAULT 'default',
    equipment_id        TEXT NOT NULL,
    calibration_date    DATE NOT NULL,
    next_due_date       DATE,
    performed_by        TEXT NOT NULL DEFAULT 'external',
    provider            TEXT NOT NULL DEFAULT '',
    certificate_number  TEXT NOT NULL DEFAULT '',
    certificate_doc_id  TEXT,
    result              TEXT NOT NULL DEFAULT 'pass',   -- pass|adjusted|fail
    remarks             TEXT NOT NULL DEFAULT '',
    recorded_by         TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_cal_records_eq ON quality_calibration_records(equipment_id, calibration_date DESC);

CREATE TABLE IF NOT EXISTS quality_complaints (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL DEFAULT 'default',
    complaint_no           TEXT NOT NULL DEFAULT '',
    customer_name          TEXT NOT NULL DEFAULT '',
    customer_id            TEXT,
    product_details        TEXT NOT NULL DEFAULT '',
    batch_number           TEXT NOT NULL DEFAULT '',
    complaint_type         TEXT NOT NULL DEFAULT 'other',    -- electrical|physical|packaging|other
    description            TEXT NOT NULL DEFAULT '',
    date_received          DATE NOT NULL DEFAULT CURRENT_DATE,
    status                 TEXT NOT NULL DEFAULT 'open',     -- open|investigating|resolved|closed
    root_cause_analysis    TEXT NOT NULL DEFAULT '',
    responsible_department TEXT NOT NULL DEFAULT '',
    supporting_data        TEXT NOT NULL DEFAULT '',
    linked_capa_id         TEXT,
    resolution             TEXT NOT NULL DEFAULT '',
    closed_at              TIMESTAMP,
    closed_by              TEXT NOT NULL DEFAULT '',
    created_by             TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_complaints_company ON quality_complaints(company_id, status);

CREATE TABLE IF NOT EXISTS quality_capa (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL DEFAULT 'default',
    capa_no                TEXT NOT NULL DEFAULT '',
    initiated_date         DATE NOT NULL DEFAULT CURRENT_DATE,
    problem_description    TEXT NOT NULL DEFAULT '',
    nc_kind                TEXT NOT NULL DEFAULT 'actual',      -- actual|potential
    possible_cause         TEXT NOT NULL DEFAULT '',
    proposed_action        TEXT NOT NULL DEFAULT '',
    action_type            TEXT NOT NULL DEFAULT 'corrective',  -- corrective|preventive
    conditions_for_closing TEXT NOT NULL DEFAULT '',
    responsible_person     TEXT NOT NULL DEFAULT '',
    target_date            DATE,
    completion_date        DATE,
    status                 TEXT NOT NULL DEFAULT 'open',        -- open|in_progress|pending_verification|closed|overdue
    approved_by            TEXT NOT NULL DEFAULT '',
    approved_at            TIMESTAMP,
    requested_by           TEXT NOT NULL DEFAULT '',
    source                 TEXT NOT NULL DEFAULT 'other',       -- complaint|ncr|audit|other
    source_id              TEXT,
    effectiveness_check    TEXT NOT NULL DEFAULT '',
    closed_by              TEXT NOT NULL DEFAULT '',
    closed_at              TIMESTAMP,
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_capa_company ON quality_capa(company_id, status, target_date);

CREATE TABLE IF NOT EXISTS quality_ncrs (
    id                     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL DEFAULT 'default',
    ncr_no                 TEXT NOT NULL DEFAULT '',
    date_of_inspection     DATE NOT NULL DEFAULT CURRENT_DATE,
    ncr_type               TEXT NOT NULL DEFAULT 'inspection',   -- inspection|measurement|analysis
    product_or_material    TEXT NOT NULL DEFAULT '',
    item_kind              TEXT NOT NULL DEFAULT 'finished_good', -- raw_material|packaging|finished_good|process
    location               TEXT NOT NULL DEFAULT '',
    measurement            TEXT NOT NULL DEFAULT '',
    description            TEXT NOT NULL DEFAULT '',
    objective_evidence     TEXT NOT NULL DEFAULT '',
    inspector_name         TEXT NOT NULL DEFAULT '',
    inspector_signed_at    TIMESTAMP,
    disposition            TEXT,                                 -- use_as_is|rework|reject|return_to_supplier|scrap
    disposition_by         TEXT NOT NULL DEFAULT '',
    disposition_at         TIMESTAMP,
    disposition_note       TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'open',         -- open|dispositioned|closed
    linked_capa_id         TEXT,
    source_inspection_kind TEXT,
    source_inspection_id   TEXT,
    lot_no                 TEXT NOT NULL DEFAULT '',
    quantity               NUMERIC(18,4),
    unit                   TEXT NOT NULL DEFAULT '',
    closed_by              TEXT NOT NULL DEFAULT '',
    closed_at              TIMESTAMP,
    created_by             TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_ncrs_company ON quality_ncrs(company_id, status);

CREATE TABLE IF NOT EXISTS quality_audits (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL DEFAULT 'default',
    audit_no           TEXT NOT NULL DEFAULT '',
    scope              TEXT NOT NULL DEFAULT '',
    standard           TEXT NOT NULL DEFAULT 'ISO 9001:2015',
    planned_date       DATE,
    actual_date        DATE,
    auditor            TEXT NOT NULL DEFAULT '',
    auditee_department TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'planned',   -- planned|done|closed
    summary            TEXT NOT NULL DEFAULT '',
    created_by         TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_audits_company ON quality_audits(company_id, planned_date);

CREATE TABLE IF NOT EXISTS quality_audit_checklist (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL DEFAULT 'default',
    audit_id        TEXT NOT NULL,
    seq             INT NOT NULL DEFAULT 0,
    item            TEXT NOT NULL,
    requirement_ref TEXT NOT NULL DEFAULT '',
    result          TEXT NOT NULL DEFAULT 'na',   -- conforming|minor_nc|major_nc|observation|na
    evidence        TEXT NOT NULL DEFAULT '',
    capa_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_quality_audit_checklist_audit ON quality_audit_checklist(audit_id, seq);
"""

# product / machine / lot columns per kind used by the reports
_PRODUCT_COL = {"rm": "type_of_material", "inprocess": "product_type_mm2", "insulation": "product_type_mm2",
                "final": "cable_type_mm2", "packing": "product_type_mm2", "conductor": "product_type_mm2"}
_MACHINE_COL = {"inprocess": "machine_name", "insulation": "machine_name"}
_SUPPLIER_COL = {"rm": "supplier_name"}
_LOT_COLS = {"rm": ("lot_no",), "inprocess": ("order_number", "lot_no"), "insulation": ("order_number",),
             "final": ("order_number", "lot_no", "drum_number"), "packing": ("order_number",),
             "conductor": ("order_number",)}
SEQ_PREFIX = {"coa": "COA", "complaint": "CMP", "capa": "CAPA", "ncr": "NCR", "audit": "AUD"}


def _opt(v):
    return v if v not in ("", None) else None


def _txt(v, limit=2000) -> str:
    return ("" if v is None else str(v)).strip()[:limit]


def _uid() -> str:
    return str(uuid.uuid4())


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("quality schema ready")
    except Exception as e:
        logger.error("quality schema init failed: %s", e)


def _emit(company_id: str, event: str, payload: dict) -> None:
    try:
        from webhook_data_store import emit
    except Exception:
        return
    try:
        emit(company_id, event, payload)
    except Exception as e:  # never break the caller
        logger.debug("quality webhook %s not emitted: %s", event, e)


def _table_exists(cur, name: str) -> bool:
    try:
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1", (name,))
        return cur.fetchone() is not None
    except Exception:
        return False


def _first(row: dict, *keys, default=""):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return default


class QualityDataStore:

    def ensure_schema(self):
        ensure_schema()

    # ── sequences ──────────────────────────────────────────────────
    @staticmethod
    def _next_no(cur, company_id: str, kind: str, year: Optional[int] = None) -> str:
        """Gapless per-company / per-year number, taken inside the caller's transaction."""
        year = year or date.today().year
        cur.execute(
            """INSERT INTO quality_sequences(company_id, kind, year, last_no) VALUES (%s,%s,%s,1)
               ON CONFLICT (company_id, kind, year)
               DO UPDATE SET last_no = quality_sequences.last_no + 1
               RETURNING last_no""",
            (company_id, kind, year))
        n = cur.fetchone()["last_no"]
        prefix = SEQ_PREFIX.get(kind) or INSPECTION_KINDS.get(kind, {}).get("prefix") or kind.upper()
        return format_number(prefix, year, n)

    # ── lookups into sibling modules (guarded) ─────────────────────
    def lookups(self, company_id: str) -> dict:
        """Datalist options for forms: products, machines, orders, customers, vendors."""
        out = {"products": [], "machines": [], "orders": [], "customers": [], "vendors": []}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    def _rows(table):
                        if not _table_exists(cur, table):
                            return []
                        cur.execute(f"SELECT * FROM {table} WHERE company_id=%s ORDER BY 1 LIMIT 300", (company_id,))
                        return [dict(r) for r in cur.fetchall()]
                    for r in _rows("mfg_products"):
                        size = _first(r, "size_mm2")
                        label = " ".join(str(x) for x in (_first(r, "name", "code"), f"{size} mm²" if size else "",
                                                          _first(r, "color")) if x)
                        out["products"].append({"id": r.get("id"), "label": label, "code": _first(r, "code")})
                    for r in _rows("mfg_machines"):
                        out["machines"].append({"id": r.get("id"), "label": str(_first(r, "name", "machine_name", "code", "tag"))})
                    for r in _rows("mfg_production_orders"):
                        out["orders"].append({"id": r.get("id"), "label": str(_first(r, "order_no", "order_number")),
                                              "customer": _first(r, "customer_name")})
                    for r in _rows("commercial_customers"):
                        out["customers"].append({"id": r.get("id"), "label": str(_first(r, "name", "customer_name"))})
                    for r in _rows("proc_vendors"):
                        out["vendors"].append({"id": r.get("id"), "label": str(_first(r, "name"))})
        except Exception as e:
            logger.debug("quality lookups unavailable: %s", e)
        return out

    @staticmethod
    def _resolve_refs(cur, company_id: str, kind: str, data: dict) -> None:
        """Best-effort id resolution (machine / order / customer / supplier) — never raises."""
        pairs = (("machine_name", "machine_id", "mfg_machines", ("name", "machine_name", "code")),
                 ("order_number", "production_order_id", "mfg_production_orders", ("order_no", "order_number")),
                 ("customer_name", "customer_id", "commercial_customers", ("name", "customer_name")),
                 ("supplier_name", "supplier_id", "proc_vendors", ("name",)))
        hidden = set(INSPECTION_KINDS[kind].get("hidden", ()))
        for name_col, id_col, table, cands in pairs:
            if id_col not in hidden or not data.get(name_col) or data.get(id_col):
                continue
            try:
                if not _table_exists(cur, table):
                    continue
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))
                cols = {r["column_name"] for r in cur.fetchall()}
                col = next((c for c in cands if c in cols), None)
                if not col:
                    continue
                cur.execute(f"SELECT id FROM {table} WHERE company_id=%s AND LOWER({col})=LOWER(%s) LIMIT 1",
                            (company_id, data[name_col]))
                row = cur.fetchone()
                if row:
                    data[id_col] = str(row["id"])
            except Exception as e:
                logger.debug("resolve %s via %s failed: %s", id_col, table, e)

    # ── spec sets ──────────────────────────────────────────────────
    def list_spec_sets(self, company_id: str, applies_to: str = None, status: str = None) -> List[dict]:
        try:
            sql = """SELECT s.*, (SELECT COUNT(*) FROM quality_spec_params p WHERE p.spec_set_id=s.id) AS param_count
                     FROM quality_spec_sets s WHERE s.company_id=%s"""
            params: list = [company_id]
            if applies_to:
                sql += " AND s.applies_to=%s"; params.append(applies_to)
            if status:
                sql += " AND s.status=%s"; params.append(status)
            sql += " ORDER BY s.status='active' DESC, s.name, s.version DESC"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_spec_sets: %s", e); return []

    def get_spec_set(self, spec_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_spec_sets WHERE id=%s AND company_id=%s", (spec_id, company_id))
                row = cur.fetchone()
                if not row:
                    return None
                s = dict(row)
                cur.execute("SELECT * FROM quality_spec_params WHERE spec_set_id=%s ORDER BY seq, parameter", (spec_id,))
                s["params"] = [dict(r) for r in cur.fetchall()]
                return s
        except Exception as e:
            logger.error("get_spec_set: %s", e); return None

    def create_spec_set(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            sid = _uid()
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO quality_spec_sets(id,company_id,name,applies_to,product_code,product_type,standard,
                       version,status,notes,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (sid, company_id, _txt(data.get("name"), 200), data.get("applies_to") or "final",
                     _opt(_txt(data.get("product_code"), 100)), _opt(_txt(data.get("product_type"), 200)),
                     _txt(data.get("standard"), 200), _txt(data.get("version"), 20) or "1",
                     data.get("status") or "draft", _txt(data.get("notes")), _txt(data.get("created_by"), 100)))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_spec_set: %s", e); return None

    def update_spec_set(self, spec_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE quality_spec_sets SET name=%s, applies_to=%s, product_code=%s, product_type=%s,
                       standard=%s, version=%s, status=%s, notes=%s, updated_at=NOW()
                       WHERE id=%s AND company_id=%s""",
                    (_txt(data.get("name"), 200), data.get("applies_to") or "final",
                     _opt(_txt(data.get("product_code"), 100)), _opt(_txt(data.get("product_type"), 200)),
                     _txt(data.get("standard"), 200), _txt(data.get("version"), 20) or "1",
                     data.get("status") or "draft", _txt(data.get("notes")), spec_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_spec_set: %s", e); return False

    def set_spec_status(self, spec_id: str, company_id: str, status: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE quality_spec_sets SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                            (status, spec_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("set_spec_status: %s", e); return False

    def add_spec_param(self, spec_id: str, company_id: str, data: dict) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM quality_spec_params WHERE spec_set_id=%s", (spec_id,))
                seq = int(to_num(data.get("seq")) or cur.fetchone()["n"])
                cur.execute(
                    """INSERT INTO quality_spec_params(id,company_id,spec_set_id,parameter,unit,spec_kind,nominal,min_value,
                       max_value,tolerance_pct,method,sample_size,mandatory,seq)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (_uid(), company_id, spec_id, _txt(data.get("parameter"), 120), _txt(data.get("unit"), 30),
                     data.get("spec_kind") or "range", to_num(data.get("nominal")), to_num(data.get("min_value")),
                     to_num(data.get("max_value")), to_num(data.get("tolerance_pct")), _txt(data.get("method"), 200),
                     _txt(data.get("sample_size"), 60), str(data.get("mandatory", "1")) not in ("0", "false", "off", ""),
                     seq))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("add_spec_param: %s", e); return None

    def delete_spec_param(self, param_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM quality_spec_params WHERE id=%s AND company_id=%s", (param_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_spec_param: %s", e); return False

    # ── inspections (generic over INSPECTION_KINDS) ────────────────
    @staticmethod
    def _coerce(kind: str, data: dict) -> dict:
        cfg = INSPECTION_KINDS[kind]
        out: dict = {}
        for f in cfg["fields"]:
            v = data.get(f["name"])
            if f["type"] == "number":
                out[f["name"]] = to_num(v)
            elif f["type"] == "date":
                out[f["name"]] = to_date(v)
            elif f["type"] == "select":
                out[f["name"]] = v if v in f["options"] else (f["options"][0] if f["options"] else "")
            else:
                out[f["name"]] = _txt(v)
        for h in cfg.get("hidden", ()):
            out[h] = _opt(_txt(data.get(h), 200))
        out["inspection_date"] = to_date(data.get("inspection_date")) or date.today()
        out["spec_set_id"] = _opt(_txt(data.get("spec_set_id"), 64))
        out["remarks"] = _txt(data.get("remarks"))
        for s in ("prepared_by", "received_by", "inspected_by", "checked_by", "approved_by"):
            out[s] = _txt(data.get(s), 120)
        return out

    @staticmethod
    def _write_lines(cur, company_id: str, kind: str, inspection_id: str, lines: List[dict]) -> None:
        cur.execute("DELETE FROM quality_inspection_lines WHERE inspection_kind=%s AND inspection_id=%s",
                    (kind, inspection_id))
        for i, ln in enumerate(lines or [], start=1):
            cur.execute(
                """INSERT INTO quality_inspection_lines(id,company_id,inspection_kind,inspection_id,seq,parameter,unit,
                   spec_kind,spec_value,spec_min,spec_max,tolerance_pct,measured_value,result,mandatory,method,remarks)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (_uid(), company_id, kind, inspection_id, ln.get("seq") or i, _txt(ln.get("parameter"), 120),
                 _txt(ln.get("unit"), 30), ln.get("spec_kind") or "range", to_num(ln.get("spec_value")),
                 to_num(ln.get("spec_min")), to_num(ln.get("spec_max")), to_num(ln.get("tolerance_pct")),
                 to_num(ln.get("measured_value")), ln.get("result") or "na",
                 bool(ln.get("mandatory", True)), _txt(ln.get("method"), 120), _txt(ln.get("remarks"), 500)))

    @staticmethod
    def _overall(kind: str, data: dict, lines: List[dict]) -> str:
        res = overall_result(lines)
        if kind == "final" and data.get("test_result") == "fail":
            return "fail"
        if kind == "insulation" and data.get("product_defect") == "reject":
            return "fail"
        if kind == "rm" and data.get("disposition") in ("reject", "return_to_supplier", "request_replacement") and res != "fail":
            return "conditional" if res == "pass" else res
        if kind == "final" and res == "na" and data.get("test_result") == "pass":
            return "pass"
        return res

    def create_inspection(self, kind: str, company_id: str, data: dict, lines: List[dict],
                          actor: str = "") -> Optional[dict]:
        if kind not in INSPECTION_KINDS:
            return None
        cfg = INSPECTION_KINDS[kind]
        try:
            row = self._coerce(kind, data)
            lines = evaluate_lines(lines or [])
            row["overall_result"] = self._overall(kind, row, lines)
            row["status"] = "draft"
            iid = _uid()
            with get_conn() as conn, conn.cursor() as cur:
                self._resolve_refs(cur, company_id, kind, row)
                row["ref_no"] = self._next_no(cur, company_id, kind, row["inspection_date"].year)
                if kind == "final" and not row.get("certificate_number"):
                    row["certificate_number"] = self._next_no(cur, company_id, "coa", row["inspection_date"].year)
                cols = ["id", "company_id", "created_by"] + list(row.keys())
                vals = [iid, company_id, _txt(actor, 100)] + list(row.values())
                cur.execute(f"INSERT INTO {cfg['table']}({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                            tuple(vals))
                created = dict(cur.fetchone())
                self._write_lines(cur, company_id, kind, iid, lines)
            created["lines"] = lines
            if created["overall_result"] == "fail":
                _emit(company_id, "quality.inspection_failed",
                      {"kind": kind, "id": iid, "ref_no": created.get("ref_no"),
                       "product": created.get(_PRODUCT_COL.get(kind, ""), ""),
                       "order_number": created.get("order_number") or "", "lot_no": created.get("lot_no") or "",
                       "failed_parameters": [ln["parameter"] for ln in lines if ln.get("result") == "fail"]})
            return created
        except Exception as e:
            logger.error("create_inspection(%s): %s", kind, e); return None

    def update_inspection(self, kind: str, inspection_id: str, company_id: str, data: dict,
                          lines: List[dict]) -> bool:
        if kind not in INSPECTION_KINDS:
            return False
        cfg = INSPECTION_KINDS[kind]
        try:
            row = self._coerce(kind, data)
            lines = evaluate_lines(lines or [])
            row["overall_result"] = self._overall(kind, row, lines)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT status FROM {cfg['table']} WHERE id=%s AND company_id=%s", (inspection_id, company_id))
                cur_row = cur.fetchone()
                if not cur_row or cur_row["status"] == "approved":
                    return False
                self._resolve_refs(cur, company_id, kind, row)
                sets = ", ".join(f"{k}=%s" for k in row)
                cur.execute(f"UPDATE {cfg['table']} SET {sets}, updated_at=NOW() WHERE id=%s AND company_id=%s",
                            tuple(row.values()) + (inspection_id, company_id))
                self._write_lines(cur, company_id, kind, inspection_id, lines)
            return True
        except Exception as e:
            logger.error("update_inspection(%s): %s", kind, e); return False

    def get_inspection(self, kind: str, inspection_id: str, company_id: str) -> Optional[dict]:
        if kind not in INSPECTION_KINDS:
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {INSPECTION_KINDS[kind]['table']} WHERE id=%s AND company_id=%s",
                            (inspection_id, company_id))
                row = cur.fetchone()
                if not row:
                    return None
                d = dict(row)
                cur.execute("SELECT * FROM quality_inspection_lines WHERE inspection_kind=%s AND inspection_id=%s ORDER BY seq",
                            (kind, inspection_id))
                d["lines"] = [dict(r) for r in cur.fetchall()]
                d["kind"] = kind
                return d
        except Exception as e:
            logger.error("get_inspection(%s): %s", kind, e); return None

    def list_inspections(self, kind: str, company_id: str, status: str = None, result: str = None,
                         q: str = None, date_from=None, date_to=None, limit: int = 500) -> List[dict]:
        if kind not in INSPECTION_KINDS:
            return []
        cfg = INSPECTION_KINDS[kind]
        try:
            sql = f"SELECT * FROM {cfg['table']} WHERE company_id=%s"
            params: list = [company_id]
            if status:
                sql += " AND status=%s"; params.append(status)
            if result:
                sql += " AND overall_result=%s"; params.append(result)
            if date_from:
                sql += " AND inspection_date >= %s"; params.append(date_from)
            if date_to:
                sql += " AND inspection_date <= %s"; params.append(date_to)
            if q:
                text_cols = [f["name"] for f in cfg["fields"] if f["type"] in ("text", "textarea")] + ["ref_no"]
                sql += " AND (" + " OR ".join(f"{c} ILIKE %s" for c in text_cols) + ")"
                params += [f"%{q}%"] * len(text_cols)
            sql += " ORDER BY inspection_date DESC, created_at DESC LIMIT %s"; params.append(limit)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    r["kind"] = kind
                return rows
        except Exception as e:
            logger.error("list_inspections(%s): %s", kind, e); return []

    def set_inspection_status(self, kind: str, inspection_id: str, company_id: str, status: str,
                              actor: str = "") -> bool:
        if kind not in INSPECTION_KINDS or status not in ("draft", "submitted", "approved"):
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                if status == "approved":
                    cur.execute(f"""UPDATE {INSPECTION_KINDS[kind]['table']} SET status='approved', approved_at=NOW(),
                                    approved_by=CASE WHEN approved_by='' THEN %s ELSE approved_by END, updated_at=NOW()
                                    WHERE id=%s AND company_id=%s""", (_txt(actor, 120), inspection_id, company_id))
                elif status == "submitted":
                    cur.execute(f"""UPDATE {INSPECTION_KINDS[kind]['table']} SET status='submitted', submitted_at=NOW(),
                                    updated_at=NOW() WHERE id=%s AND company_id=%s AND status<>'approved'""",
                                (inspection_id, company_id))
                else:
                    cur.execute(f"""UPDATE {INSPECTION_KINDS[kind]['table']} SET status='draft', approved_at=NULL,
                                    updated_at=NOW() WHERE id=%s AND company_id=%s""", (inspection_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("set_inspection_status(%s): %s", kind, e); return False

    def recent_failures(self, company_id: str, limit: int = 10) -> List[dict]:
        out = []
        for kind in INSPECTION_KINDS:
            for r in self.list_inspections(kind, company_id, result="fail", limit=limit):
                r["product"] = r.get(_PRODUCT_COL[kind]) or ""
                out.append(r)
        out.sort(key=lambda r: (r.get("inspection_date") or date.min, r.get("created_at") or datetime.min), reverse=True)
        return out[:limit]

    # ── equipment / calibration ────────────────────────────────────
    def list_equipment(self, company_id: str, status: str = None, q: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM quality_equipment WHERE company_id=%s"
            params: list = [company_id]
            if q:
                sql += " AND (equipment_name ILIKE %s OR equipment_tag ILIKE %s OR location ILIKE %s)"
                params += [f"%{q}%"] * 3
            sql += " ORDER BY next_due_date NULLS LAST, equipment_name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
            today = date.today()
            for r in rows:
                r["status"] = calibration_status(r.get("next_due_date"), today)
                r["days_to_due"] = (r["next_due_date"] - today).days if r.get("next_due_date") else None
            if status:
                rows = [r for r in rows if r["status"] == status]
            return rows
        except Exception as e:
            logger.error("list_equipment: %s", e); return []

    def get_equipment(self, equipment_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_equipment WHERE id=%s AND company_id=%s", (equipment_id, company_id))
                row = cur.fetchone()
                if not row:
                    return None
                e = dict(row)
                e["status"] = calibration_status(e.get("next_due_date"))
                cur.execute("SELECT * FROM quality_calibration_records WHERE equipment_id=%s ORDER BY calibration_date DESC, created_at DESC",
                            (equipment_id,))
                e["records"] = [dict(r) for r in cur.fetchall()]
                return e
        except Exception as e:
            logger.error("get_equipment: %s", e); return None

    @staticmethod
    def _equipment_row(data: dict) -> dict:
        freq = int(to_num(data.get("calibration_frequency_months")) or 12)
        last = to_date(data.get("last_calibration_date"))
        nxt = to_date(data.get("next_due_date")) or next_due_date(last, freq)
        return {
            "equipment_name": _txt(data.get("equipment_name"), 200), "equipment_tag": _txt(data.get("equipment_tag"), 80),
            "location": _txt(data.get("location"), 200), "calibration_frequency_months": freq,
            "last_calibration_date": last, "next_due_date": nxt,
            "calibration_standard": _txt(data.get("calibration_standard"), 200),
            "performed_by": data.get("performed_by") if data.get("performed_by") in ("internal", "external") else "external",
            "provider": _txt(data.get("provider"), 200), "certificate_number": _txt(data.get("certificate_number"), 100),
            "status": calibration_status(nxt), "notes": _txt(data.get("notes")),
        }

    def create_equipment(self, company_id: str, data: dict) -> Optional[dict]:
        row = self._equipment_row(data)
        if not row["equipment_name"] or not row["equipment_tag"]:
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cols = ["id", "company_id"] + list(row)
                cur.execute(f"INSERT INTO quality_equipment({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                            (_uid(), company_id, *row.values()))
                created = dict(cur.fetchone())
                if row["last_calibration_date"]:
                    cur.execute(
                        """INSERT INTO quality_calibration_records(id,company_id,equipment_id,calibration_date,next_due_date,
                           performed_by,provider,certificate_number,result,remarks,recorded_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'pass',%s,%s)""",
                        (_uid(), company_id, created["id"], row["last_calibration_date"], row["next_due_date"],
                         row["performed_by"], row["provider"], row["certificate_number"], "Initial record",
                         _txt(data.get("recorded_by"), 100)))
                return created
        except Exception as e:
            logger.error("create_equipment: %s", e); return None

    def update_equipment(self, equipment_id: str, company_id: str, data: dict) -> bool:
        row = self._equipment_row(data)
        if not row["equipment_name"] or not row["equipment_tag"]:
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sets = ", ".join(f"{k}=%s" for k in row)
                cur.execute(f"UPDATE quality_equipment SET {sets}, updated_at=NOW() WHERE id=%s AND company_id=%s",
                            (*row.values(), equipment_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_equipment: %s", e); return False

    def add_calibration(self, equipment_id: str, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_equipment WHERE id=%s AND company_id=%s", (equipment_id, company_id))
                eq = cur.fetchone()
                if not eq:
                    return None
                cal_date = to_date(data.get("calibration_date")) or date.today()
                nxt = to_date(data.get("next_due_date")) or next_due_date(cal_date, eq["calibration_frequency_months"])
                performed = data.get("performed_by") if data.get("performed_by") in ("internal", "external") else eq["performed_by"]
                result = data.get("result") if data.get("result") in ("pass", "adjusted", "fail") else "pass"
                cur.execute(
                    """INSERT INTO quality_calibration_records(id,company_id,equipment_id,calibration_date,next_due_date,
                       performed_by,provider,certificate_number,certificate_doc_id,result,remarks,recorded_by)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (_uid(), company_id, equipment_id, cal_date, nxt, performed, _txt(data.get("provider"), 200),
                     _txt(data.get("certificate_number"), 100), _opt(data.get("certificate_doc_id")), result,
                     _txt(data.get("remarks")), _txt(actor, 100)))
                rec = dict(cur.fetchone())
                if result != "fail":
                    cur.execute(
                        """UPDATE quality_equipment SET last_calibration_date=%s, next_due_date=%s, performed_by=%s,
                           provider=CASE WHEN %s<>'' THEN %s ELSE provider END,
                           certificate_number=CASE WHEN %s<>'' THEN %s ELSE certificate_number END,
                           status=%s, updated_at=NOW() WHERE id=%s""",
                        (cal_date, nxt, performed, rec["provider"], rec["provider"], rec["certificate_number"],
                         rec["certificate_number"], calibration_status(nxt), equipment_id))
                return rec
        except Exception as e:
            logger.error("add_calibration: %s", e); return None

    def set_equipment_certificate(self, equipment_id: str, company_id: str, doc_id: str) -> bool:
        """Link the latest uploaded calibration certificate to the equipment and its newest record."""
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE quality_equipment SET certificate_doc_id=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                            (doc_id, equipment_id, company_id))
                ok = cur.rowcount > 0
                cur.execute("""UPDATE quality_calibration_records SET certificate_doc_id=%s WHERE id = (
                                   SELECT id FROM quality_calibration_records WHERE equipment_id=%s AND company_id=%s
                                   ORDER BY calibration_date DESC, created_at DESC LIMIT 1)""",
                            (doc_id, equipment_id, company_id))
                return ok
        except Exception as e:
            logger.error("set_equipment_certificate: %s", e); return False

    def refresh_equipment_status(self, company_id: str = None, today: Optional[date] = None) -> dict:
        """Recompute the stored status column (job); returns counts per status."""
        today = today or date.today()
        soon = today + timedelta(days=DUE_SOON_DAYS)
        counts = {"valid": 0, "due_soon": 0, "expired": 0, "unknown": 0}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                where = " WHERE company_id=%s" if company_id else ""
                params = (today, today, soon) + ((company_id,) if company_id else ())
                cur.execute(f"""UPDATE quality_equipment SET status = CASE
                                  WHEN next_due_date IS NULL THEN 'unknown'
                                  WHEN next_due_date < %s THEN 'expired'
                                  WHEN next_due_date BETWEEN %s AND %s THEN 'due_soon'
                                  ELSE 'valid' END, updated_at=NOW(){where}""", params)
                cur.execute(f"SELECT status, COUNT(*) AS c FROM quality_equipment{where} GROUP BY status",
                            ((company_id,) if company_id else ()))
                for r in cur.fetchall():
                    counts[r["status"]] = r["c"]
        except Exception as e:
            logger.error("refresh_equipment_status: %s", e)
        return counts

    def equipment_attention(self, company_id: str) -> List[dict]:
        return [e for e in self.list_equipment(company_id) if e["status"] in ("due_soon", "expired")]

    # ── complaints ─────────────────────────────────────────────────
    @staticmethod
    def _complaint_row(data: dict) -> dict:
        return {
            "customer_name": _txt(data.get("customer_name"), 200), "customer_id": _opt(_txt(data.get("customer_id"), 64)),
            "product_details": _txt(data.get("product_details")), "batch_number": _txt(data.get("batch_number"), 100),
            "complaint_type": data.get("complaint_type") if data.get("complaint_type") in ("electrical", "physical", "packaging", "other") else "other",
            "description": _txt(data.get("description"), 5000),
            "date_received": to_date(data.get("date_received")) or date.today(),
            "root_cause_analysis": _txt(data.get("root_cause_analysis"), 5000),
            "responsible_department": _txt(data.get("responsible_department"), 120),
            "supporting_data": _txt(data.get("supporting_data")), "resolution": _txt(data.get("resolution"), 5000),
        }

    def list_complaints(self, company_id: str, status: str = None, q: str = None,
                        date_from=None, date_to=None) -> List[dict]:
        try:
            sql = "SELECT * FROM quality_complaints WHERE company_id=%s"
            params: list = [company_id]
            if status:
                sql += " AND status=%s"; params.append(status)
            if q:
                sql += " AND (complaint_no ILIKE %s OR customer_name ILIKE %s OR product_details ILIKE %s OR batch_number ILIKE %s)"
                params += [f"%{q}%"] * 4
            if date_from:
                sql += " AND date_received >= %s"; params.append(date_from)
            if date_to:
                sql += " AND date_received <= %s"; params.append(date_to)
            sql += " ORDER BY date_received DESC, created_at DESC"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_complaints: %s", e); return []

    def get_complaint(self, complaint_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_complaints WHERE id=%s AND company_id=%s", (complaint_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_complaint: %s", e); return None

    def create_complaint(self, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        row = self._complaint_row(data)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                self._resolve_refs(cur, company_id, "final", row)
                row["complaint_no"] = self._next_no(cur, company_id, "complaint", row["date_received"].year)
                cols = ["id", "company_id", "created_by", "status"] + list(row)
                cur.execute(f"INSERT INTO quality_complaints({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                            (_uid(), company_id, _txt(actor, 100), "open", *row.values()))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_complaint: %s", e); return None

    def update_complaint(self, complaint_id: str, company_id: str, data: dict) -> bool:
        row = self._complaint_row(data)
        status = data.get("status")
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sets = ", ".join(f"{k}=%s" for k in row)
                vals = list(row.values())
                if status in ("open", "investigating", "resolved"):
                    sets += ", status=%s"; vals.append(status)
                cur.execute(f"UPDATE quality_complaints SET {sets}, updated_at=NOW() WHERE id=%s AND company_id=%s AND status<>'closed'",
                            (*vals, complaint_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_complaint: %s", e); return False

    def close_complaint(self, complaint_id: str, company_id: str, actor: str, resolution: str = "") -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE quality_complaints SET status='closed', closed_at=NOW(), closed_by=%s,
                               resolution=CASE WHEN %s<>'' THEN %s ELSE resolution END, updated_at=NOW()
                               WHERE id=%s AND company_id=%s""",
                            (_txt(actor, 100), _txt(resolution), _txt(resolution), complaint_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("close_complaint: %s", e); return False

    def link_capa(self, table: str, record_id: str, company_id: str, capa_id: str) -> bool:
        if table not in ("quality_complaints", "quality_ncrs", "quality_audit_checklist"):
            return False
        col = "capa_id" if table == "quality_audit_checklist" else "linked_capa_id"
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {table} SET {col}=%s WHERE id=%s AND company_id=%s", (capa_id, record_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("link_capa: %s", e); return False

    # ── CAPA ───────────────────────────────────────────────────────
    @staticmethod
    def _capa_row(data: dict) -> dict:
        return {
            "initiated_date": to_date(data.get("initiated_date")) or date.today(),
            "problem_description": _txt(data.get("problem_description"), 5000),
            "nc_kind": "potential" if data.get("nc_kind") == "potential" else "actual",
            "possible_cause": _txt(data.get("possible_cause"), 5000), "proposed_action": _txt(data.get("proposed_action"), 5000),
            "action_type": "preventive" if data.get("action_type") == "preventive" else "corrective",
            "conditions_for_closing": _txt(data.get("conditions_for_closing"), 5000),
            "responsible_person": _txt(data.get("responsible_person"), 120),
            "target_date": to_date(data.get("target_date")), "completion_date": to_date(data.get("completion_date")),
            "requested_by": _txt(data.get("requested_by"), 120),
            "source": data.get("source") if data.get("source") in ("complaint", "ncr", "audit", "other") else "other",
            "source_id": _opt(_txt(data.get("source_id"), 64)),
            "effectiveness_check": _txt(data.get("effectiveness_check"), 5000),
        }

    @staticmethod
    def _decorate_capa(rows: List[dict], today: Optional[date] = None) -> List[dict]:
        today = today or date.today()
        for r in rows:
            r["effective_status"] = capa_effective_status(r.get("status"), r.get("target_date"), today, r.get("completion_date"))
            r["days_overdue"] = max(0, (today - r["target_date"]).days) if r.get("target_date") and r["effective_status"] not in ("closed",) else 0
        return rows

    def list_capa(self, company_id: str, status: str = None, responsible: str = None, requested_by: str = None,
                  source: str = None, q: str = None, date_from=None, date_to=None) -> List[dict]:
        try:
            sql = "SELECT * FROM quality_capa WHERE company_id=%s"
            params: list = [company_id]
            if responsible:
                sql += " AND responsible_person ILIKE %s"; params.append(f"%{responsible}%")
            if requested_by:
                sql += " AND requested_by ILIKE %s"; params.append(f"%{requested_by}%")
            if source:
                sql += " AND source=%s"; params.append(source)
            if q:
                sql += " AND (capa_no ILIKE %s OR problem_description ILIKE %s OR proposed_action ILIKE %s)"
                params += [f"%{q}%"] * 3
            if date_from:
                sql += " AND initiated_date >= %s"; params.append(date_from)
            if date_to:
                sql += " AND initiated_date <= %s"; params.append(date_to)
            sql += " ORDER BY (status='closed'), target_date NULLS LAST, initiated_date DESC"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = self._decorate_capa([dict(r) for r in cur.fetchall()])
            if status:
                rows = [r for r in rows if r["effective_status"] == status]
            return rows
        except Exception as e:
            logger.error("list_capa: %s", e); return []

    def get_capa(self, capa_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_capa WHERE id=%s AND company_id=%s", (capa_id, company_id))
                row = cur.fetchone()
                return self._decorate_capa([dict(row)])[0] if row else None
        except Exception as e:
            logger.error("get_capa: %s", e); return None

    def create_capa(self, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        row = self._capa_row(data)
        row["requested_by"] = row["requested_by"] or _txt(actor, 120)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                row["capa_no"] = self._next_no(cur, company_id, "capa", row["initiated_date"].year)
                cols = ["id", "company_id", "status"] + list(row)
                cur.execute(f"INSERT INTO quality_capa({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                            (_uid(), company_id, "open", *row.values()))
                created = dict(cur.fetchone())
                if created["source"] == "complaint" and created.get("source_id"):
                    cur.execute("UPDATE quality_complaints SET linked_capa_id=%s WHERE id=%s AND company_id=%s",
                                (created["id"], created["source_id"], company_id))
                elif created["source"] == "ncr" and created.get("source_id"):
                    cur.execute("UPDATE quality_ncrs SET linked_capa_id=%s WHERE id=%s AND company_id=%s",
                                (created["id"], created["source_id"], company_id))
                elif created["source"] == "audit" and created.get("source_id"):
                    cur.execute("UPDATE quality_audit_checklist SET capa_id=%s WHERE id=%s AND company_id=%s",
                                (created["id"], created["source_id"], company_id))
                return self._decorate_capa([created])[0]
        except Exception as e:
            logger.error("create_capa: %s", e); return None

    def update_capa(self, capa_id: str, company_id: str, data: dict) -> bool:
        row = self._capa_row(data)
        status = data.get("status")
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sets = ", ".join(f"{k}=%s" for k in row)
                vals = list(row.values())
                if status in ("open", "in_progress", "pending_verification"):
                    sets += ", status=%s"; vals.append(status)
                cur.execute(f"UPDATE quality_capa SET {sets}, updated_at=NOW() WHERE id=%s AND company_id=%s AND status<>'closed'",
                            (*vals, capa_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_capa: %s", e); return False

    def approve_capa(self, capa_id: str, company_id: str, actor: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE quality_capa SET approved_by=%s, approved_at=NOW(),
                               status=CASE WHEN status='open' THEN 'in_progress' ELSE status END, updated_at=NOW()
                               WHERE id=%s AND company_id=%s AND status<>'closed'""", (_txt(actor, 120), capa_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("approve_capa: %s", e); return False

    def close_capa(self, capa_id: str, company_id: str, actor: str, effectiveness_check: str = "") -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE quality_capa SET status='closed', closed_by=%s, closed_at=NOW(),
                               completion_date=COALESCE(completion_date, CURRENT_DATE),
                               effectiveness_check=CASE WHEN %s<>'' THEN %s ELSE effectiveness_check END, updated_at=NOW()
                               WHERE id=%s AND company_id=%s""",
                            (_txt(actor, 120), _txt(effectiveness_check), _txt(effectiveness_check), capa_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("close_capa: %s", e); return False

    def mark_overdue_capa(self, company_id: str = None, today: Optional[date] = None) -> int:
        today = today or date.today()
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sql = """UPDATE quality_capa SET status='overdue', updated_at=NOW()
                         WHERE status IN ('open','in_progress','pending_verification') AND target_date IS NOT NULL
                           AND target_date < %s AND completion_date IS NULL"""
                params: list = [today]
                if company_id:
                    sql += " AND company_id=%s"; params.append(company_id)
                cur.execute(sql, tuple(params))
                return cur.rowcount
        except Exception as e:
            logger.error("mark_overdue_capa: %s", e); return 0

    def capa_reminder_report(self, company_id: str, today: Optional[date] = None) -> dict:
        """Past-due responses, pending implementations, grouped by requestor and assignee."""
        rows = self.list_capa(company_id)
        today = today or date.today()
        open_rows = [r for r in rows if r["effective_status"] != "closed"]
        past_due = [r for r in open_rows if r["effective_status"] == "overdue"]
        pending = [r for r in open_rows if r["effective_status"] in ("open", "in_progress", "pending_verification")]
        by_requestor: dict = {}
        by_assignee: dict = {}
        for r in open_rows:
            for key, bucket in ((r.get("requested_by") or "—", by_requestor), (r.get("responsible_person") or "—", by_assignee)):
                b = bucket.setdefault(key, {"name": key, "open": 0, "overdue": 0, "items": []})
                b["open"] += 1
                b["overdue"] += 1 if r["effective_status"] == "overdue" else 0
                b["items"].append(r)
        return {"past_due": past_due, "pending": pending,
                "by_requestor": sorted(by_requestor.values(), key=lambda b: (-b["overdue"], -b["open"], b["name"])),
                "by_assignee": sorted(by_assignee.values(), key=lambda b: (-b["overdue"], -b["open"], b["name"])),
                "closed_30d": [r for r in rows if r["effective_status"] == "closed" and r.get("closed_at")
                               and r["closed_at"].date() >= today - timedelta(days=30)],
                "total_open": len(open_rows), "today": today}

    # ── NCR ────────────────────────────────────────────────────────
    @staticmethod
    def _ncr_row(data: dict) -> dict:
        return {
            "date_of_inspection": to_date(data.get("date_of_inspection")) or date.today(),
            "ncr_type": data.get("ncr_type") if data.get("ncr_type") in ("inspection", "measurement", "analysis") else "inspection",
            "product_or_material": _txt(data.get("product_or_material"), 300),
            "item_kind": data.get("item_kind") if data.get("item_kind") in ("raw_material", "packaging", "finished_good", "process") else "finished_good",
            "location": _txt(data.get("location"), 200), "measurement": _txt(data.get("measurement"), 5000),
            "description": _txt(data.get("description"), 5000), "objective_evidence": _txt(data.get("objective_evidence"), 5000),
            "inspector_name": _txt(data.get("inspector_name"), 120),
            "source_inspection_kind": _opt(data.get("source_inspection_kind")) if data.get("source_inspection_kind") in INSPECTION_KINDS else None,
            "source_inspection_id": _opt(_txt(data.get("source_inspection_id"), 64)),
            "lot_no": _txt(data.get("lot_no"), 100), "quantity": to_num(data.get("quantity")), "unit": _txt(data.get("unit"), 30),
        }

    def list_ncrs(self, company_id: str, status: str = None, item_kind: str = None, q: str = None,
                  date_from=None, date_to=None) -> List[dict]:
        try:
            sql = "SELECT * FROM quality_ncrs WHERE company_id=%s"
            params: list = [company_id]
            if status:
                sql += " AND status=%s"; params.append(status)
            if item_kind:
                sql += " AND item_kind=%s"; params.append(item_kind)
            if q:
                sql += " AND (ncr_no ILIKE %s OR product_or_material ILIKE %s OR description ILIKE %s OR lot_no ILIKE %s)"
                params += [f"%{q}%"] * 4
            if date_from:
                sql += " AND date_of_inspection >= %s"; params.append(date_from)
            if date_to:
                sql += " AND date_of_inspection <= %s"; params.append(date_to)
            sql += " ORDER BY (status='closed'), date_of_inspection DESC, created_at DESC"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_ncrs: %s", e); return []

    def get_ncr(self, ncr_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_ncrs WHERE id=%s AND company_id=%s", (ncr_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_ncr: %s", e); return None

    def create_ncr(self, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        row = self._ncr_row(data)
        row["inspector_name"] = row["inspector_name"] or _txt(actor, 120)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                row["ncr_no"] = self._next_no(cur, company_id, "ncr", row["date_of_inspection"].year)
                cols = ["id", "company_id", "status", "created_by", "inspector_signed_at"] + list(row)
                cur.execute(f"INSERT INTO quality_ncrs({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                            (_uid(), company_id, "open", _txt(actor, 100), datetime.now(), *row.values()))
                created = dict(cur.fetchone())
            _emit(company_id, "quality.ncr_raised", {"id": created["id"], "ncr_no": created["ncr_no"],
                                                     "item_kind": created["item_kind"],
                                                     "product_or_material": created["product_or_material"],
                                                     "lot_no": created["lot_no"],
                                                     "source_inspection_kind": created.get("source_inspection_kind"),
                                                     "source_inspection_id": created.get("source_inspection_id")})
            return created
        except Exception as e:
            logger.error("create_ncr: %s", e); return None

    def update_ncr(self, ncr_id: str, company_id: str, data: dict) -> bool:
        row = self._ncr_row(data)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sets = ", ".join(f"{k}=%s" for k in row)
                cur.execute(f"UPDATE quality_ncrs SET {sets}, updated_at=NOW() WHERE id=%s AND company_id=%s AND status<>'closed'",
                            (*row.values(), ncr_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_ncr: %s", e); return False

    def disposition_ncr(self, ncr_id: str, company_id: str, disposition: str, actor: str, note: str = "") -> bool:
        if disposition not in ("use_as_is", "rework", "reject", "return_to_supplier", "scrap"):
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE quality_ncrs SET disposition=%s, disposition_by=%s, disposition_at=NOW(),
                               disposition_note=%s, status='dispositioned', updated_at=NOW()
                               WHERE id=%s AND company_id=%s AND status<>'closed'""",
                            (disposition, _txt(actor, 120), _txt(note), ncr_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("disposition_ncr: %s", e); return False

    def close_ncr(self, ncr_id: str, company_id: str, actor: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE quality_ncrs SET status='closed', closed_by=%s, closed_at=NOW(), updated_at=NOW()
                               WHERE id=%s AND company_id=%s AND disposition IS NOT NULL""", (_txt(actor, 120), ncr_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("close_ncr: %s", e); return False

    def ncr_prefill_from_inspection(self, kind: str, inspection_id: str, company_id: str) -> dict:
        """Pre-populate the 'Raise NCR' form from a failed inspection."""
        insp = self.get_inspection(kind, inspection_id, company_id)
        if not insp:
            return {}
        failed = [ln for ln in insp.get("lines", []) if ln.get("result") == "fail"]
        item_kind = {"rm": "raw_material", "packing": "packaging", "inprocess": "process", "insulation": "process"}.get(kind, "finished_good")
        measurement = "; ".join(
            f"{ln['parameter']}: measured {ln['measured_value']} {ln.get('unit') or ''}"
            + (f" (spec {ln['spec_min'] if ln.get('spec_min') is not None else ''}–{ln['spec_max'] if ln.get('spec_max') is not None else ''})"
               if (ln.get("spec_min") is not None or ln.get("spec_max") is not None)
               else (f" (spec {ln['spec_value']} {ln.get('spec_kind') or ''})" if ln.get("spec_value") is not None else ""))
            for ln in failed)
        product = insp.get(_PRODUCT_COL[kind]) or ""
        return {
            "date_of_inspection": insp.get("inspection_date"), "ncr_type": "inspection",
            "product_or_material": product, "item_kind": item_kind,
            "location": insp.get("machine_name") or insp.get("location") or "",
            "measurement": measurement,
            "description": f"{INSPECTION_KINDS[kind]['label']} {insp.get('ref_no')} failed"
                           + (f" — order {insp['order_number']}" if insp.get("order_number") else "")
                           + (f" — lot {insp['lot_no']}" if insp.get("lot_no") else ""),
            "objective_evidence": f"Inspection {insp.get('ref_no')} ({len(failed)} failed parameter(s))",
            "inspector_name": insp.get("inspected_by") or insp.get("prepared_by") or "",
            "source_inspection_kind": kind, "source_inspection_id": inspection_id,
            "lot_no": insp.get("lot_no") or insp.get("order_number") or "",
            "quantity": insp.get("quantity") or insp.get("total_length_m"), "unit": insp.get("unit") or ("m" if insp.get("total_length_m") else ""),
        }

    # ── audits ─────────────────────────────────────────────────────
    @staticmethod
    def _audit_row(data: dict) -> dict:
        return {
            "scope": _txt(data.get("scope"), 2000), "standard": _txt(data.get("standard"), 120) or "ISO 9001:2015",
            "planned_date": to_date(data.get("planned_date")), "actual_date": to_date(data.get("actual_date")),
            "auditor": _txt(data.get("auditor"), 120), "auditee_department": _txt(data.get("auditee_department"), 120),
            "summary": _txt(data.get("summary"), 5000),
        }

    def list_audits(self, company_id: str, status: str = None, date_from=None, date_to=None) -> List[dict]:
        try:
            sql = """SELECT a.*,
                       (SELECT COUNT(*) FROM quality_audit_checklist c WHERE c.audit_id=a.id) AS item_count,
                       (SELECT COUNT(*) FROM quality_audit_checklist c WHERE c.audit_id=a.id AND c.result IN ('minor_nc','major_nc')) AS nc_count
                     FROM quality_audits a WHERE a.company_id=%s"""
            params: list = [company_id]
            if status:
                sql += " AND a.status=%s"; params.append(status)
            if date_from:
                sql += " AND COALESCE(a.actual_date, a.planned_date) >= %s"; params.append(date_from)
            if date_to:
                sql += " AND COALESCE(a.actual_date, a.planned_date) <= %s"; params.append(date_to)
            sql += " ORDER BY (a.status='closed'), a.planned_date NULLS LAST, a.created_at DESC"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("list_audits: %s", e); return []

    def get_audit(self, audit_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_audits WHERE id=%s AND company_id=%s", (audit_id, company_id))
                row = cur.fetchone()
                if not row:
                    return None
                a = dict(row)
                cur.execute("""SELECT c.*, k.capa_no, k.status AS capa_status FROM quality_audit_checklist c
                               LEFT JOIN quality_capa k ON k.id=c.capa_id WHERE c.audit_id=%s ORDER BY c.seq, c.item""", (audit_id,))
                a["checklist"] = [dict(r) for r in cur.fetchall()]
                return a
        except Exception as e:
            logger.error("get_audit: %s", e); return None

    def create_audit(self, company_id: str, data: dict, actor: str = "") -> Optional[dict]:
        row = self._audit_row(data)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                year = (row["planned_date"] or date.today()).year
                row["audit_no"] = self._next_no(cur, company_id, "audit", year)
                cols = ["id", "company_id", "status", "created_by"] + list(row)
                cur.execute(f"INSERT INTO quality_audits({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                            (_uid(), company_id, "planned", _txt(actor, 100), *row.values()))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_audit: %s", e); return None

    def update_audit(self, audit_id: str, company_id: str, data: dict) -> bool:
        row = self._audit_row(data)
        status = data.get("status")
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sets = ", ".join(f"{k}=%s" for k in row)
                vals = list(row.values())
                if status in ("planned", "done"):
                    sets += ", status=%s"; vals.append(status)
                cur.execute(f"UPDATE quality_audits SET {sets}, updated_at=NOW() WHERE id=%s AND company_id=%s AND status<>'closed'",
                            (*vals, audit_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_audit: %s", e); return False

    def set_audit_status(self, audit_id: str, company_id: str, status: str) -> bool:
        if status not in ("planned", "done", "closed"):
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE quality_audits SET status=%s, updated_at=NOW() WHERE id=%s AND company_id=%s",
                            (status, audit_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("set_audit_status: %s", e); return False

    def add_checklist_item(self, audit_id: str, company_id: str, data: dict) -> Optional[dict]:
        item = _txt(data.get("item"), 1000)
        if not item:
            return None
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM quality_audit_checklist WHERE audit_id=%s", (audit_id,))
                seq = cur.fetchone()["n"]
                cur.execute("""INSERT INTO quality_audit_checklist(id,company_id,audit_id,seq,item,requirement_ref,result,evidence)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                            (_uid(), company_id, audit_id, seq, item, _txt(data.get("requirement_ref"), 120),
                             data.get("result") if data.get("result") in ("conforming", "minor_nc", "major_nc", "observation", "na") else "na",
                             _txt(data.get("evidence"), 2000)))
                return dict(cur.fetchone())
        except Exception as e:
            logger.error("add_checklist_item: %s", e); return None

    def update_checklist_item(self, item_id: str, company_id: str, data: dict) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE quality_audit_checklist SET item=%s, requirement_ref=%s, result=%s, evidence=%s
                               WHERE id=%s AND company_id=%s""",
                            (_txt(data.get("item"), 1000), _txt(data.get("requirement_ref"), 120),
                             data.get("result") if data.get("result") in ("conforming", "minor_nc", "major_nc", "observation", "na") else "na",
                             _txt(data.get("evidence"), 2000), item_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("update_checklist_item: %s", e); return False

    def delete_checklist_item(self, item_id: str, company_id: str) -> bool:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM quality_audit_checklist WHERE id=%s AND company_id=%s", (item_id, company_id))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_checklist_item: %s", e); return False

    def get_checklist_item(self, item_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM quality_audit_checklist WHERE id=%s AND company_id=%s", (item_id, company_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_checklist_item: %s", e); return None

    def audit_summary(self, company_id: str, date_from=None, date_to=None) -> dict:
        audits = self.list_audits(company_id, date_from=date_from, date_to=date_to)
        today = date.today()
        results = {k: 0 for k in ("conforming", "minor_nc", "major_nc", "observation", "na")}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sql = """SELECT c.result, COUNT(*) AS n FROM quality_audit_checklist c JOIN quality_audits a ON a.id=c.audit_id
                         WHERE a.company_id=%s"""
                params: list = [company_id]
                if date_from:
                    sql += " AND COALESCE(a.actual_date,a.planned_date) >= %s"; params.append(date_from)
                if date_to:
                    sql += " AND COALESCE(a.actual_date,a.planned_date) <= %s"; params.append(date_to)
                cur.execute(sql + " GROUP BY c.result", tuple(params))
                for r in cur.fetchall():
                    results[r["result"]] = r["n"]
        except Exception as e:
            logger.error("audit_summary: %s", e)
        capas = [c for c in self.list_capa(company_id, source="audit", date_from=date_from, date_to=date_to)]
        return {
            "schedule": [a for a in audits if a["status"] == "planned"],
            "overdue_schedule": [a for a in audits if a["status"] == "planned" and a.get("planned_date") and a["planned_date"] < today],
            "history": [a for a in audits if a["status"] in ("done", "closed")],
            "checklist_results": results,
            "corrective_actions": capas,
            "total": len(audits),
        }

    # ── reports ────────────────────────────────────────────────────
    def measurement_rows(self, company_id: str, date_from=None, date_to=None, product: str = None,
                         parameter: str = None, machine: str = None, kinds: Iterable[str] = None,
                         order_number: str = None, limit: int = 5000) -> List[dict]:
        """Every numeric measurement (one row per inspection line) across the inspection kinds."""
        kinds = [k for k in (kinds or INSPECTION_KINDS) if k in INSPECTION_KINDS and INSPECTION_KINDS[k]["default_lines"]]
        parts, params = [], []
        for k in kinds:
            cfg = INSPECTION_KINDS[k]
            mcol = _MACHINE_COL.get(k)
            has_order = any(f["name"] == "order_number" for f in cfg["fields"])
            sql = f"""SELECT h.inspection_date AS date, h.{_PRODUCT_COL[k]} AS product, l.parameter, l.unit,
                             l.measured_value AS value, l.spec_min, l.spec_max, l.spec_value, l.spec_kind, l.tolerance_pct,
                             l.result, h.ref_no AS ref, h.id AS inspection_id, '{k}' AS kind,
                             {('h.' + mcol) if mcol else "''"} AS machine,
                             {'h.order_number' if has_order else "''"} AS order_number
                      FROM quality_inspection_lines l JOIN {cfg['table']} h ON h.id=l.inspection_id AND l.inspection_kind='{k}'
                      WHERE h.company_id=%s AND l.measured_value IS NOT NULL"""
            p: list = [company_id]
            if date_from:
                sql += " AND h.inspection_date >= %s"; p.append(date_from)
            if date_to:
                sql += " AND h.inspection_date <= %s"; p.append(date_to)
            if product:
                sql += f" AND h.{_PRODUCT_COL[k]} ILIKE %s"; p.append(f"%{product}%")
            if parameter:
                sql += " AND l.parameter ILIKE %s"; p.append(parameter)
            if machine:
                if not mcol:
                    continue
                sql += f" AND h.{mcol} ILIKE %s"; p.append(f"%{machine}%")
            if order_number:
                if not has_order:
                    continue
                sql += " AND h.order_number ILIKE %s"; p.append(f"%{order_number}%")
            parts.append(f"({sql})"); params += p
        if not parts:
            return []
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(" UNION ALL ".join(parts) + " ORDER BY date, ref LIMIT %s", tuple(params) + (limit,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("measurement_rows: %s", e); return []

    def filter_options(self, company_id: str) -> dict:
        """Distinct products / parameters / machines / suppliers for report filters."""
        out = {"products": set(), "parameters": set(), "machines": set(), "suppliers": set()}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT DISTINCT parameter FROM quality_inspection_lines WHERE company_id=%s ORDER BY 1 LIMIT 200", (company_id,))
                out["parameters"] = {r["parameter"] for r in cur.fetchall()}
                for k, cfg in INSPECTION_KINDS.items():
                    cur.execute(f"SELECT DISTINCT {_PRODUCT_COL[k]} AS v FROM {cfg['table']} WHERE company_id=%s AND {_PRODUCT_COL[k]}<>'' LIMIT 200", (company_id,))
                    out["products"] |= {r["v"] for r in cur.fetchall()}
                    if k in _MACHINE_COL:
                        cur.execute(f"SELECT DISTINCT machine_name AS v FROM {cfg['table']} WHERE company_id=%s AND machine_name<>'' LIMIT 200", (company_id,))
                        out["machines"] |= {r["v"] for r in cur.fetchall()}
                cur.execute("SELECT DISTINCT supplier_name AS v FROM quality_rm_inspections WHERE company_id=%s AND supplier_name<>'' LIMIT 200", (company_id,))
                out["suppliers"] = {r["v"] for r in cur.fetchall()}
        except Exception as e:
            logger.debug("filter_options: %s", e)
        return {k: sorted(v) for k, v in out.items()}

    def supplier_compliance(self, company_id: str, date_from=None, date_to=None, supplier: str = None) -> List[dict]:
        try:
            sql = """SELECT supplier_name, COUNT(*) AS inspections,
                            SUM(CASE WHEN overall_result='pass' THEN 1 ELSE 0 END) AS passed,
                            SUM(CASE WHEN overall_result='fail' THEN 1 ELSE 0 END) AS failed,
                            SUM(CASE WHEN overall_result='conditional' THEN 1 ELSE 0 END) AS conditional,
                            SUM(CASE WHEN disposition='reject' THEN 1 ELSE 0 END) AS rejections,
                            SUM(CASE WHEN disposition='return_to_supplier' THEN 1 ELSE 0 END) AS returns,
                            SUM(CASE WHEN disposition='request_replacement' THEN 1 ELSE 0 END) AS replacements,
                            SUM(CASE WHEN disposition='accept' THEN 1 ELSE 0 END) AS accepted,
                            MAX(inspection_date) AS last_inspection
                     FROM quality_rm_inspections WHERE company_id=%s"""
            params: list = [company_id]
            if date_from:
                sql += " AND inspection_date >= %s"; params.append(date_from)
            if date_to:
                sql += " AND inspection_date <= %s"; params.append(date_to)
            if supplier:
                sql += " AND supplier_name ILIKE %s"; params.append(f"%{supplier}%")
            sql += " GROUP BY supplier_name ORDER BY inspections DESC, supplier_name"
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                n = int(r["inspections"] or 0)
                r["pass_rate"] = (int(r["passed"] or 0) / n * 100.0) if n else None
                r["rating"] = ("A" if r["pass_rate"] is not None and r["pass_rate"] >= 95 else
                               "B" if r["pass_rate"] is not None and r["pass_rate"] >= 80 else
                               "C" if r["pass_rate"] is not None else "—")
            return rows
        except Exception as e:
            logger.error("supplier_compliance: %s", e); return []

    def packing_rows(self, company_id: str, date_from=None, date_to=None, product: str = None) -> List[dict]:
        return [r for r in self.list_inspections("packing", company_id, date_from=date_from, date_to=date_to, limit=5000)
                if not product or (product.lower() in (r.get("product_type_mm2") or "").lower())]

    def result_rows(self, company_id: str, date_from=None, date_to=None, kinds: Iterable[str] = None,
                    product: str = None) -> List[dict]:
        """(kind, date, product, overall_result, status) for every inspection — defect-rate reports."""
        parts, params = [], []
        for k in (kinds or INSPECTION_KINDS):
            if k not in INSPECTION_KINDS:
                continue
            cfg = INSPECTION_KINDS[k]
            sql = f"""SELECT '{k}' AS kind, inspection_date AS date, {_PRODUCT_COL[k]} AS product, overall_result, status,
                             ref_no AS ref, id FROM {cfg['table']} WHERE company_id=%s"""
            p: list = [company_id]
            if date_from:
                sql += " AND inspection_date >= %s"; p.append(date_from)
            if date_to:
                sql += " AND inspection_date <= %s"; p.append(date_to)
            if product:
                sql += f" AND {_PRODUCT_COL[k]} ILIKE %s"; p.append(f"%{product}%")
            parts.append(f"({sql})"); params += p
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(" UNION ALL ".join(parts) + " ORDER BY date", tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("result_rows: %s", e); return []

    def lot_history(self, company_id: str, ref: str) -> List[dict]:
        """Every inspection / NCR / complaint touching a lot, order or drum number, in time order."""
        ref = (ref or "").strip()
        if not ref:
            return []
        events: List[dict] = []
        try:
            with get_conn() as conn, conn.cursor() as cur:
                for k, cfg in INSPECTION_KINDS.items():
                    cols = _LOT_COLS[k]
                    where = " OR ".join(f"{c} ILIKE %s" for c in cols)
                    cur.execute(f"SELECT * FROM {cfg['table']} WHERE company_id=%s AND ({where}) ORDER BY inspection_date, created_at",
                                (company_id, *[f"%{ref}%"] * len(cols)))
                    for r in cur.fetchall():
                        d = dict(r)
                        events.append({"date": d["inspection_date"], "at": d.get("created_at"), "kind": k,
                                       "label": cfg["label"], "ref_no": d.get("ref_no"), "id": d["id"],
                                       "result": d.get("overall_result"), "status": d.get("status"),
                                       "product": d.get(_PRODUCT_COL[k]) or "", "detail": d.get("machine_name") or d.get("supplier_name") or d.get("customer_name") or ""})
                cur.execute("SELECT * FROM quality_ncrs WHERE company_id=%s AND (lot_no ILIKE %s OR product_or_material ILIKE %s OR description ILIKE %s) ORDER BY date_of_inspection",
                            (company_id, f"%{ref}%", f"%{ref}%", f"%{ref}%"))
                for r in cur.fetchall():
                    d = dict(r)
                    events.append({"date": d["date_of_inspection"], "at": d.get("created_at"), "kind": "ncr", "label": "NCR",
                                   "ref_no": d["ncr_no"], "id": d["id"], "result": d.get("disposition") or "open",
                                   "status": d["status"], "product": d["product_or_material"], "detail": d["description"][:120]})
                cur.execute("SELECT * FROM quality_complaints WHERE company_id=%s AND (batch_number ILIKE %s OR product_details ILIKE %s) ORDER BY date_received",
                            (company_id, f"%{ref}%", f"%{ref}%"))
                for r in cur.fetchall():
                    d = dict(r)
                    events.append({"date": d["date_received"], "at": d.get("created_at"), "kind": "complaint", "label": "Complaint",
                                   "ref_no": d["complaint_no"], "id": d["id"], "result": d["complaint_type"],
                                   "status": d["status"], "product": d["product_details"][:80], "detail": d["customer_name"]})
        except Exception as e:
            logger.error("lot_history: %s", e)
        events.sort(key=lambda e: (e["date"] or date.min, e["at"] or datetime.min))
        return events

    def nc_summary(self, company_id: str, date_from=None, date_to=None) -> dict:
        out = {"by_item_kind": {}, "by_disposition": {}, "by_status": {}, "failed_by_kind": {}, "total_ncrs": 0}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                sql = "SELECT item_kind, COALESCE(disposition,'pending') AS disposition, status, COUNT(*) AS n FROM quality_ncrs WHERE company_id=%s"
                params: list = [company_id]
                if date_from:
                    sql += " AND date_of_inspection >= %s"; params.append(date_from)
                if date_to:
                    sql += " AND date_of_inspection <= %s"; params.append(date_to)
                cur.execute(sql + " GROUP BY item_kind, disposition, status", tuple(params))
                for r in cur.fetchall():
                    n = int(r["n"])
                    out["total_ncrs"] += n
                    out["by_item_kind"][r["item_kind"]] = out["by_item_kind"].get(r["item_kind"], 0) + n
                    out["by_disposition"][r["disposition"]] = out["by_disposition"].get(r["disposition"], 0) + n
                    out["by_status"][r["status"]] = out["by_status"].get(r["status"], 0) + n
        except Exception as e:
            logger.error("nc_summary: %s", e)
        for r in self.result_rows(company_id, date_from, date_to):
            b = out["failed_by_kind"].setdefault(r["kind"], {"label": INSPECTION_KINDS[r["kind"]]["label"], "inspected": 0, "failed": 0, "conditional": 0})
            b["inspected"] += 1
            if r["overall_result"] == "fail":
                b["failed"] += 1
            elif r["overall_result"] == "conditional":
                b["conditional"] += 1
        return out

    # ── dashboard / integration ────────────────────────────────────
    def open_issues(self, company_id: str) -> dict:
        counts = {"open_ncrs": 0, "open_capas": 0, "overdue_capas": 0, "open_complaints": 0,
                  "equipment_due_soon": 0, "equipment_expired": 0, "failed_inspections_30d": 0,
                  "pending_approval": 0, "planned_audits": 0}
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM quality_ncrs WHERE company_id=%s AND status<>'closed'", (company_id,))
                counts["open_ncrs"] = cur.fetchone()["n"]
                cur.execute("SELECT COUNT(*) AS n FROM quality_complaints WHERE company_id=%s AND status NOT IN ('resolved','closed')", (company_id,))
                counts["open_complaints"] = cur.fetchone()["n"]
                cur.execute("""SELECT COUNT(*) AS n,
                                      SUM(CASE WHEN target_date < CURRENT_DATE AND completion_date IS NULL THEN 1 ELSE 0 END) AS overdue
                               FROM quality_capa WHERE company_id=%s AND status<>'closed'""", (company_id,))
                r = cur.fetchone()
                counts["open_capas"], counts["overdue_capas"] = int(r["n"] or 0), int(r["overdue"] or 0)
                cur.execute("""SELECT SUM(CASE WHEN next_due_date < CURRENT_DATE THEN 1 ELSE 0 END) AS expired,
                                      SUM(CASE WHEN next_due_date >= CURRENT_DATE AND next_due_date <= CURRENT_DATE + %s * INTERVAL '1 day' THEN 1 ELSE 0 END) AS soon
                               FROM quality_equipment WHERE company_id=%s""", (DUE_SOON_DAYS, company_id))
                r = cur.fetchone()
                counts["equipment_expired"], counts["equipment_due_soon"] = int(r["expired"] or 0), int(r["soon"] or 0)
                cur.execute("SELECT COUNT(*) AS n FROM quality_audits WHERE company_id=%s AND status='planned'", (company_id,))
                counts["planned_audits"] = cur.fetchone()["n"]
                failed = pending = 0
                for cfg in INSPECTION_KINDS.values():
                    cur.execute(f"""SELECT SUM(CASE WHEN overall_result='fail' AND inspection_date >= CURRENT_DATE - INTERVAL '30 days' THEN 1 ELSE 0 END) AS f,
                                           SUM(CASE WHEN status='submitted' THEN 1 ELSE 0 END) AS p
                                    FROM {cfg['table']} WHERE company_id=%s""", (company_id,))
                    r = cur.fetchone()
                    failed += int(r["f"] or 0); pending += int(r["p"] or 0)
                counts["failed_inspections_30d"], counts["pending_approval"] = failed, pending
        except Exception as e:
            logger.error("open_issues: %s", e)
        return counts

    def dashboard(self, company_id: str) -> dict:
        issues = self.open_issues(company_id)
        month_start = date.today().replace(day=1)
        by_kind = []
        totals = {"inspected": 0, "passed": 0, "failed": 0}
        for kind, cfg in INSPECTION_KINDS.items():
            rows = self.list_inspections(kind, company_id, date_from=month_start, limit=5000)
            passed = sum(1 for r in rows if r["overall_result"] == "pass")
            failed = sum(1 for r in rows if r["overall_result"] == "fail")
            by_kind.append({"kind": kind, "label": cfg["label"], "icon": cfg["icon"], "count": len(rows),
                            "passed": passed, "failed": failed})
            totals["inspected"] += len(rows); totals["passed"] += passed; totals["failed"] += failed
        return {"issues": issues, "by_kind": by_kind, "month": totals,
                "recent_failures": self.recent_failures(company_id, 8),
                "equipment_attention": self.equipment_attention(company_id)[:8],
                "overdue_capa": [c for c in self.list_capa(company_id, status="overdue")][:8],
                "open_complaints": self.list_complaints(company_id)[:8]}

    def companies(self) -> List[str]:
        ids: set = set()
        try:
            with get_conn() as conn, conn.cursor() as cur:
                for t in ("quality_equipment", "quality_capa", "quality_complaints"):
                    cur.execute(f"SELECT DISTINCT company_id FROM {t}")
                    ids |= {r["company_id"] for r in cur.fetchall()}
        except Exception as e:
            logger.error("quality companies: %s", e)
        return sorted(ids)

    def entity_exists(self, entity_kind: str, entity_id: str, company_id: str) -> bool:
        table = {"equipment": "quality_equipment", "complaint": "quality_complaints", "capa": "quality_capa",
                 "ncr": "quality_ncrs", "audit": "quality_audits", "spec": "quality_spec_sets"}.get(entity_kind) \
            or (INSPECTION_KINDS[entity_kind]["table"] if entity_kind in INSPECTION_KINDS else None)
        if not table:
            return False
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {table} WHERE id=%s AND company_id=%s", (entity_id, company_id))
                return cur.fetchone() is not None
        except Exception as e:
            logger.error("entity_exists: %s", e); return False


quality_store = QualityDataStore()


# ── public integration API ─────────────────────────────────────────

def latest_rm_result(company_id: str, material_code: str = None, lot_no: str = None) -> Optional[dict]:
    """Most recent raw-material inspection for a material and/or lot (None when nothing recorded)."""
    if not material_code and not lot_no:
        return None
    try:
        sql = "SELECT * FROM quality_rm_inspections WHERE company_id=%s"
        params: list = [company_id or "default"]
        if material_code:
            sql += " AND LOWER(material_code)=LOWER(%s)"; params.append(material_code)
        if lot_no:
            sql += " AND LOWER(lot_no)=LOWER(%s)"; params.append(lot_no)
        sql += " ORDER BY inspection_date DESC, created_at DESC LIMIT 1"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if not row:
            return None
        r = dict(row)
        return {"id": r["id"], "ref_no": r["ref_no"], "overall_result": r["overall_result"], "disposition": r["disposition"],
                "status": r["status"], "inspection_date": r["inspection_date"], "supplier_name": r["supplier_name"],
                "material_code": r["material_code"], "lot_no": r["lot_no"],
                "released": r["status"] == "approved" and r["overall_result"] == "pass" and r["disposition"] == "accept"}
    except Exception as e:
        logger.error("latest_rm_result: %s", e); return None


def inspections_for_order(company_id: str, order_number: str) -> dict:
    """All inspections recorded against a production / sales order number."""
    out = {"in_process": [], "insulation": [], "final": [], "packing": [], "conductor": []}
    if not order_number:
        return out
    names = {"inprocess": "in_process", "insulation": "insulation", "final": "final", "packing": "packing", "conductor": "conductor"}
    try:
        with get_conn() as conn, conn.cursor() as cur:
            for kind, key in names.items():
                cur.execute(f"SELECT * FROM {INSPECTION_KINDS[kind]['table']} WHERE company_id=%s AND LOWER(order_number)=LOWER(%s) ORDER BY inspection_date DESC, created_at DESC",
                            (company_id or "default", order_number))
                out[key] = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("inspections_for_order: %s", e)
    return out


def open_quality_issues(company_id: str) -> dict:
    return quality_store.open_issues(company_id or "default")


def record_procurement_sample_result(company_id: str, supplier: str, material_code: str, pr_no: str, result: str,
                                     lot_no: str = "", quantity=None, unit: str = "", remarks: str = "",
                                     actor: str = "", supplier_id: str = None, invoice_number: str = "",
                                     invoice_date=None, type_of_material: str = "") -> Optional[dict]:
    """Create a submitted raw-material inspection from a procurement sample approval."""
    passed = str(result or "").lower() in ("pass", "passed", "accept", "accepted", "ok", "approved", "true", "1")
    data = {"type_of_material": type_of_material or material_code or "Procurement sample", "material_code": material_code or "",
            "supplier_name": supplier or "", "supplier_id": supplier_id, "purchase_requisition_no": pr_no or "",
            "invoice_number": invoice_number or "", "invoice_date": invoice_date, "sample_type": "Procurement sample",
            "quantity": quantity, "unit": unit or "", "lot_no": lot_no or "", "disposition": "accept" if passed else "reject",
            "remarks": remarks or "", "prepared_by": actor or "", "inspected_by": actor or ""}
    lines = [{"parameter": "Procurement sample approval", "unit": "", "spec_kind": "min", "spec_value": 1,
              "measured_value": 1 if passed else 0, "mandatory": True, "remarks": remarks or ""}]
    created = quality_store.create_inspection("rm", company_id or "default", data, lines, actor or "procurement")
    if created:
        quality_store.set_inspection_status("rm", created["id"], company_id or "default", "submitted", actor or "procurement")
        created["status"] = "submitted"
    return created


__all__ = ["quality_store", "QualityDataStore", "ensure_schema", "latest_rm_result", "inspections_for_order",
           "open_quality_issues", "record_procurement_sample_result", "SEQ_PREFIX"]
