"""
Manufacturing / Production Data Store — PostgreSQL backend.

Cable-manufacturing ERP module (plants, work centres, machines, products,
TDS, BOM, routings, calendar/shifts, planning, capacity, raw-material plans,
production orders MTS/MTO, shop-floor logging, costing, FG transfers, KPIs).
Every table is company-scoped (``company_id``).

Public Python API (for the Commercial and Quality modules)
----------------------------------------------------------
Import lazily (``import manufacturing_data_store as mfg``) and guard with
try/except — the module may be absent on some deployments.

* ``create_order_from_sales_order(company_id, *, source_ref, customer_name,
  product_code, qty, unit, cutting_length=None, packing=None,
  delivery_date=None, prepared_by="") -> dict | None``
      Creates a make-to-order production order for the product with
      ``product_code``; returns the order row (with ``order_no``) or None when
      the product is unknown / insert failed. Released automatically when the
      company's ``mto_auto_release`` setting is on.
* ``get_order_by_source(company_id, source_ref) -> list[dict]``
      All production orders raised for a sales order reference.
* ``product_by_code(company_id, code) -> dict | None``
* ``active_bom_for(product_id) -> dict | None``   (header + ``lines``)
* ``approved_tds_for(product_id) -> dict | None``
* ``order_status_summary(company_id, source_ref) -> dict``
      ``{"orders": n, "by_status": {...}, "qty_ordered": x, "qty_produced": y,
      "latest_status": "...", "order_nos": [...], "delivery_date": ...}``

Approval engine entity types owned by this module: ``production_order``
(release) and ``raw_material_plan`` (submission).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, time as dtime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── constants ───────────────────────────────────────────────────────

ORDER_STATUSES = ("planned", "released", "in_progress", "confirmed", "closed", "cancelled")
ORDER_TYPES = ("make_to_stock", "make_to_order")
PLAN_PERIODS = ("weekly", "monthly", "quarterly", "annual")
PLAN_STATUSES = ("draft", "approved", "closed")
RM_PLAN_STATUSES = ("draft", "submitted", "approved")
WC_TYPES = ("drawing", "stranding", "insulation", "sheathing", "armouring", "packing", "other")
CAPACITY_UNITS = ("kg", "m", "pcs")
PRODUCT_UNITS = ("m", "kg", "roll", "drum")
PRODUCT_TYPES = ("finished", "semi_finished")
MACHINE_STATUSES = ("active", "maintenance", "down", "retired")
CALENDAR_KINDS = ("holiday", "shutdown", "maintenance", "overhaul", "renovation", "upgrade")
ISSUE_KINDS = ("issue", "return", "consumption")
PLAN_BASIS = ("demand", "capacity", "trend")
DEFAULT_DOWNTIME_CATEGORIES = ("Mechanical", "Electrical", "Operational", "Other")
DEFAULT_SCRAP_TYPES = ("Copper", "Aluminium", "PVC", "XLPE", "Steel wire", "Other")

TDS_FIELDS = (
    "conductor_diameter_mm", "insulation_thickness_mm", "insulation_diameter_mm",
    "laid_up_diameter_mm", "bedding_diameter_mm", "sheath_thickness_mm", "sheath_diameter_mm",
    "resistance_ohm_km", "rated_voltage_kv", "standard",
)
PROCESS_FIELDS = ("die", "nipple", "zone_temperature", "diameter", "lay_length", "thickness")

# The 16-step end-to-end flow from the tender. Each step: key, title, owner,
# link (module page), and the source of its live status.
PROCESS_STEPS: Tuple[dict, ...] = (
    {"seq": 1, "key": "customer_po", "title": "Customer purchase order received", "owner": "Sales", "url": "/commercial/"},
    {"seq": 2, "key": "sales_order", "title": "Sales order prepared and approved (3 approvals)", "owner": "Sales / Management", "url": "/commercial/"},
    {"seq": 3, "key": "mo_tds_plan", "title": "Manufacturing order, TDS and raw-material plan prepared", "owner": "Planning & Engineering", "url": "/manufacturing/orders"},
    {"seq": 4, "key": "sr_pr", "title": "Store requisition and purchase requisition raised", "owner": "Property Administration", "url": "/manufacturing/rm-plans"},
    {"seq": 5, "key": "procurement_office", "title": "Purchase requisition received by Procurement office", "owner": "Procurement", "url": "/procurement/pr"},
    {"seq": 6, "key": "management_approval", "title": "Management approval of the purchase", "owner": "Management", "url": "/approvals/"},
    {"seq": 7, "key": "procurement_exec", "title": "Quotations, samples and purchase order", "owner": "Procurement", "url": "/procurement/po"},
    {"seq": 8, "key": "incoming_inspection", "title": "Incoming raw-material inspection", "owner": "Quality", "url": "/quality/"},
    {"seq": 9, "key": "rm_receipt", "title": "Raw material received into store", "owner": "Property Administration", "url": "/inventory/movements"},
    {"seq": 10, "key": "release", "title": "TDS and manufacturing order released to production", "owner": "Planning & Engineering", "url": "/manufacturing/orders"},
    {"seq": 11, "key": "production", "title": "Production (drawing, stranding, insulation, sheathing, packing)", "owner": "Production", "url": "/manufacturing/logs"},
    {"seq": 12, "key": "quality_final", "title": "In-process and final inspection", "owner": "Quality", "url": "/quality/"},
    {"seq": 13, "key": "fg_inventory", "title": "Finished goods received into inventory", "owner": "Property Administration", "url": "/inventory/items"},
    {"seq": 14, "key": "fg_market_store", "title": "Transfer to Market finished-goods store", "owner": "Property Administration / Market", "url": "/manufacturing/reports/fg-delivered"},
    {"seq": 15, "key": "dispatch", "title": "Sales dispatch to customer", "owner": "Sales", "url": "/commercial/"},
    {"seq": 16, "key": "acceptance", "title": "Customer acceptance", "owner": "Sales", "url": "/commercial/"},
)

# ── schema ──────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mfg_sequences (
    company_id TEXT NOT NULL DEFAULT 'default',
    seq_name   TEXT NOT NULL,
    next_val   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, seq_name)
);
CREATE TABLE IF NOT EXISTS mfg_settings (
    company_id                TEXT PRIMARY KEY,
    gl_wip_account            TEXT NOT NULL DEFAULT '',
    gl_raw_material_account   TEXT NOT NULL DEFAULT '',
    gl_finished_goods_account TEXT NOT NULL DEFAULT '',
    gl_scrap_account          TEXT NOT NULL DEFAULT '',
    default_plant_id          TEXT,
    mto_auto_release          BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at                TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS mfg_plants (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default',
    code TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
    product_group TEXT NOT NULL DEFAULT '', is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_plants_company ON mfg_plants(company_id);
CREATE TABLE IF NOT EXISTS mfg_work_centers (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', plant_id TEXT,
    code TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'other',
    std_capacity_per_hour NUMERIC(18,4) NOT NULL DEFAULT 0, capacity_unit TEXT NOT NULL DEFAULT 'kg',
    cost_rate_per_hour NUMERIC(18,2) NOT NULL DEFAULT 0, is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_wc_company ON mfg_work_centers(company_id);
CREATE TABLE IF NOT EXISTS mfg_machines (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', work_center_id TEXT,
    code TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '', install_date DATE, std_capacity_per_hour NUMERIC(18,4) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active', created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_machines_company ON mfg_machines(company_id);
CREATE TABLE IF NOT EXISTS mfg_products (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default',
    code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    product_type TEXT NOT NULL DEFAULT 'finished', size_mm2 NUMERIC(12,3), color TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'm', std_length_per_roll NUMERIC(12,2), sku TEXT NOT NULL DEFAULT '',
    inventory_item_id TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, code)
);
CREATE TABLE IF NOT EXISTS mfg_tds (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', product_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'draft',
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb, notes TEXT NOT NULL DEFAULT '',
    approved_by TEXT, approved_at TIMESTAMP, file_doc_id TEXT, created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_tds_product ON mfg_tds(product_id);
CREATE TABLE IF NOT EXISTS mfg_bom_headers (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', product_id TEXT NOT NULL, tds_id TEXT,
    version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'draft',
    output_qty NUMERIC(18,4) NOT NULL DEFAULT 1, output_unit TEXT NOT NULL DEFAULT 'm',
    notes TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_bom_product ON mfg_bom_headers(product_id);
CREATE TABLE IF NOT EXISTS mfg_bom_lines (
    id TEXT PRIMARY KEY, bom_id TEXT NOT NULL, component_product_id TEXT,
    material_name TEXT NOT NULL DEFAULT '', material_code TEXT NOT NULL DEFAULT '',
    qty_per_output NUMERIC(18,6) NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'kg',
    scrap_pct NUMERIC(6,2) NOT NULL DEFAULT 0, is_semi_finished BOOLEAN NOT NULL DEFAULT FALSE,
    work_center_id TEXT, operation_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mfg_bom_lines_bom ON mfg_bom_lines(bom_id);
CREATE TABLE IF NOT EXISTS mfg_routings (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', product_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'draft', notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_routings_product ON mfg_routings(product_id);
CREATE TABLE IF NOT EXISTS mfg_routing_ops (
    id TEXT PRIMARY KEY, routing_id TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 10, work_center_id TEXT,
    operation_name TEXT NOT NULL DEFAULT '', std_setup_minutes NUMERIC(10,2) NOT NULL DEFAULT 0,
    std_run_minutes_per_unit NUMERIC(12,4) NOT NULL DEFAULT 0, process_params JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_mfg_routing_ops_routing ON mfg_routing_ops(routing_id);
CREATE TABLE IF NOT EXISTS mfg_calendar (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', plant_id TEXT, date DATE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'holiday', description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mfg_calendar_company ON mfg_calendar(company_id, date);
CREATE TABLE IF NOT EXISTS mfg_shifts (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', work_center_id TEXT,
    name TEXT NOT NULL DEFAULT '', start_time TIME, end_time TIME, shift_leader TEXT NOT NULL DEFAULT '',
    line_supervisor TEXT NOT NULL DEFAULT '', is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS mfg_downtime_categories (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS mfg_downtime_reasons (
    id TEXT PRIMARY KEY, category_id TEXT NOT NULL, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mfg_scrap_types (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mfg_material_costs (
    company_id TEXT NOT NULL DEFAULT 'default', material_code TEXT NOT NULL,
    material_name TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'kg',
    std_cost NUMERIC(18,4) NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'ETB',
    PRIMARY KEY (company_id, material_code)
);
CREATE TABLE IF NOT EXISTS mfg_production_plans (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', plant_id TEXT,
    period_type TEXT NOT NULL DEFAULT 'monthly', period_start DATE, period_end DATE,
    status TEXT NOT NULL DEFAULT 'draft', notes TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_plans_company ON mfg_production_plans(company_id);
CREATE TABLE IF NOT EXISTS mfg_plan_lines (
    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, product_id TEXT NOT NULL, work_center_id TEXT,
    planned_qty NUMERIC(18,3) NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'm',
    planned_hours NUMERIC(12,2) NOT NULL DEFAULT 0, basis TEXT NOT NULL DEFAULT 'demand'
);
CREATE INDEX IF NOT EXISTS idx_mfg_plan_lines_plan ON mfg_plan_lines(plan_id);
CREATE TABLE IF NOT EXISTS mfg_capacity_plans (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', work_center_id TEXT NOT NULL,
    period_start DATE, period_end DATE, available_hours NUMERIC(12,2) NOT NULL DEFAULT 0,
    planned_hours NUMERIC(12,2) NOT NULL DEFAULT 0, utilisation_pct NUMERIC(8,2) NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS mfg_raw_material_plans (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', year INTEGER NOT NULL,
    production_plan_id TEXT, status TEXT NOT NULL DEFAULT 'draft', prepared_by TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '', store_req_ids TEXT NOT NULL DEFAULT '', purchase_req_id TEXT,
    approval_request_id TEXT, submitted_at TIMESTAMP, approved_at TIMESTAMP, approved_by TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS mfg_rm_plan_lines (
    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, material_code TEXT NOT NULL DEFAULT '',
    material_name TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'kg',
    required_qty NUMERIC(18,3) NOT NULL DEFAULT 0, on_hand_qty NUMERIC(18,3) NOT NULL DEFAULT 0,
    to_procure_qty NUMERIC(18,3) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mfg_rm_lines_plan ON mfg_rm_plan_lines(plan_id);
CREATE TABLE IF NOT EXISTS mfg_production_orders (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', order_no TEXT NOT NULL,
    product_id TEXT NOT NULL, tds_id TEXT, bom_id TEXT, routing_id TEXT, plant_id TEXT,
    order_type TEXT NOT NULL DEFAULT 'make_to_stock', source_ref TEXT NOT NULL DEFAULT '',
    customer_name TEXT NOT NULL DEFAULT '', qty_ordered NUMERIC(18,3) NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'm', cutting_length NUMERIC(12,2), packing TEXT NOT NULL DEFAULT '',
    delivery_date DATE, priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'planned',
    planned_start DATE, planned_end DATE, actual_start TIMESTAMP, actual_end TIMESTAMP,
    qty_produced NUMERIC(18,3) NOT NULL DEFAULT 0, qty_scrap NUMERIC(18,3) NOT NULL DEFAULT 0,
    planned_cost NUMERIC(18,2) NOT NULL DEFAULT 0, actual_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    prepared_by TEXT NOT NULL DEFAULT '', checked_by TEXT NOT NULL DEFAULT '', approved_by TEXT NOT NULL DEFAULT '',
    approval_request_id TEXT, approval_status TEXT NOT NULL DEFAULT 'none',
    gl_status TEXT NOT NULL DEFAULT '', gl_entry_id TEXT, notes TEXT NOT NULL DEFAULT '',
    released_at TIMESTAMP, confirmed_at TIMESTAMP, closed_at TIMESTAMP, past_due BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, order_no)
);
CREATE INDEX IF NOT EXISTS idx_mfg_orders_company ON mfg_production_orders(company_id, status);
CREATE INDEX IF NOT EXISTS idx_mfg_orders_source ON mfg_production_orders(company_id, source_ref);
CREATE TABLE IF NOT EXISTS mfg_order_operations (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 10, work_center_id TEXT,
    machine_id TEXT, operation_name TEXT NOT NULL DEFAULT '', process_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    planned_qty NUMERIC(18,3) NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMP, finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mfg_order_ops_order ON mfg_order_operations(order_id);
CREATE TABLE IF NOT EXISTS mfg_order_materials (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, material_code TEXT NOT NULL DEFAULT '',
    material_name TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'kg',
    planned_qty NUMERIC(18,4) NOT NULL DEFAULT 0, issued_qty NUMERIC(18,4) NOT NULL DEFAULT 0,
    consumed_qty NUMERIC(18,4) NOT NULL DEFAULT 0, returned_qty NUMERIC(18,4) NOT NULL DEFAULT 0,
    lot_no TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mfg_order_mat_order ON mfg_order_materials(order_id);
CREATE TABLE IF NOT EXISTS mfg_production_logs (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', order_id TEXT NOT NULL,
    operation_id TEXT, machine_id TEXT, shift_id TEXT, log_date DATE NOT NULL, hour_slot INTEGER,
    input_qty NUMERIC(18,3) NOT NULL DEFAULT 0, input_unit TEXT NOT NULL DEFAULT 'kg',
    output_qty NUMERIC(18,3) NOT NULL DEFAULT 0, output_unit TEXT NOT NULL DEFAULT 'kg',
    output_rolls INTEGER NOT NULL DEFAULT 0, under_length_rolls INTEGER NOT NULL DEFAULT 0,
    length_m NUMERIC(18,2) NOT NULL DEFAULT 0, weight_kg NUMERIC(18,3) NOT NULL DEFAULT 0,
    scrap_qty NUMERIC(18,3) NOT NULL DEFAULT 0, scrap_unit TEXT NOT NULL DEFAULT 'kg', scrap_type_id TEXT,
    rework_qty NUMERIC(18,3) NOT NULL DEFAULT 0, lot_no TEXT NOT NULL DEFAULT '', drum_no TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '', remarks TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_logs_company_date ON mfg_production_logs(company_id, log_date);
CREATE INDEX IF NOT EXISTS idx_mfg_logs_order ON mfg_production_logs(order_id);
CREATE TABLE IF NOT EXISTS mfg_material_issues (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', order_id TEXT NOT NULL,
    material_code TEXT NOT NULL DEFAULT '', material_name TEXT NOT NULL DEFAULT '',
    qty NUMERIC(18,4) NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'kg', kind TEXT NOT NULL DEFAULT 'issue',
    lot_no TEXT NOT NULL DEFAULT '', store_ref TEXT NOT NULL DEFAULT '', drum_ref TEXT NOT NULL DEFAULT '',
    unit_cost NUMERIC(18,4) NOT NULL DEFAULT 0, gl_status TEXT NOT NULL DEFAULT '', gl_entry_id TEXT,
    created_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_issues_order ON mfg_material_issues(order_id);
CREATE TABLE IF NOT EXISTS mfg_downtime_logs (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', machine_id TEXT, work_center_id TEXT,
    order_id TEXT, shift_id TEXT, started_at TIMESTAMP, ended_at TIMESTAMP, minutes NUMERIC(10,2) NOT NULL DEFAULT 0,
    category_id TEXT, reason_id TEXT, description TEXT NOT NULL DEFAULT '', reported_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_downtime_company ON mfg_downtime_logs(company_id, started_at);
CREATE TABLE IF NOT EXISTS mfg_labor_logs (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', order_id TEXT, work_center_id TEXT,
    shift_id TEXT, date DATE NOT NULL, workers INTEGER NOT NULL DEFAULT 0, hours NUMERIC(10,2) NOT NULL DEFAULT 0,
    setup_hours NUMERIC(10,2) NOT NULL DEFAULT 0, run_hours NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_labor_company ON mfg_labor_logs(company_id, date);
CREATE TABLE IF NOT EXISTS mfg_fg_transfers (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', order_id TEXT NOT NULL, product_id TEXT,
    qty NUMERIC(18,3) NOT NULL DEFAULT 0, rolls INTEGER NOT NULL DEFAULT 0, under_length_rolls INTEGER NOT NULL DEFAULT 0,
    length_m NUMERIC(18,2) NOT NULL DEFAULT 0, weight_kg NUMERIC(18,3) NOT NULL DEFAULT 0,
    from_store TEXT NOT NULL DEFAULT 'Property Administration', to_store TEXT NOT NULL DEFAULT 'Market Finished Goods Store',
    ref_no TEXT NOT NULL DEFAULT '', transferred_by TEXT NOT NULL DEFAULT '', transferred_at TIMESTAMP NOT NULL DEFAULT NOW(),
    inventory_movement_ok BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_mfg_fg_company ON mfg_fg_transfers(company_id, transferred_at);
CREATE TABLE IF NOT EXISTS mfg_order_events (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT 'note',
    note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mfg_order_events_order ON mfg_order_events(order_id);
CREATE TABLE IF NOT EXISTS mfg_daily_kpis (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL DEFAULT 'default', kpi_date DATE NOT NULL,
    work_center_id TEXT, machine_id TEXT, input_qty NUMERIC(18,3) NOT NULL DEFAULT 0,
    output_qty NUMERIC(18,3) NOT NULL DEFAULT 0, scrap_qty NUMERIC(18,3) NOT NULL DEFAULT 0,
    planned_qty NUMERIC(18,3) NOT NULL DEFAULT 0, available_hours NUMERIC(10,2) NOT NULL DEFAULT 0,
    run_hours NUMERIC(10,2) NOT NULL DEFAULT 0, downtime_minutes NUMERIC(10,2) NOT NULL DEFAULT 0,
    yield_pct NUMERIC(8,2) NOT NULL DEFAULT 0, utilisation_pct NUMERIC(8,2) NOT NULL DEFAULT 0,
    computed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, kpi_date, work_center_id, machine_id)
);
"""


# ── pure helpers (no DB) ────────────────────────────────────────────

def D(value: Any) -> Decimal:
    """Lenient Decimal: ''/None/garbage → 0."""
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def q2(value: Any) -> Decimal:
    return D(value).quantize(Decimal("0.01"))


def _opt(value: Any):
    """'' → None for DATE/NUMERIC/foreign-key columns."""
    return value if value not in ("", None) else None


def _num(value: Any) -> Decimal:
    return D(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip()) if value not in ("", None) else default
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _uid() -> str:
    return str(uuid.uuid4())


def format_order_no(year: int, seq: int) -> str:
    """MO-2026-000123 — gapless per company and year."""
    return f"MO-{int(year)}-{int(seq):06d}"


def yield_pct(output_qty: Any, input_qty: Any) -> Decimal:
    """output / input × 100 (0 when there is no input)."""
    i = D(input_qty)
    if i <= 0:
        return Decimal(0)
    return (D(output_qty) / i * 100).quantize(Decimal("0.01"))


def utilisation_pct(run_hours: Any, available_hours: Any) -> Decimal:
    a = D(available_hours)
    if a <= 0:
        return Decimal(0)
    return (D(run_hours) / a * 100).quantize(Decimal("0.01"))


def variance(planned: Any, actual: Any) -> Tuple[Decimal, Decimal]:
    """Returns (absolute variance actual-planned, percentage of planned)."""
    p, a = D(planned), D(actual)
    diff = a - p
    pct = (diff / p * 100).quantize(Decimal("0.01")) if p else Decimal(0)
    return diff, pct


def parse_params(text: str) -> Dict[str, str]:
    """'key=value' per line (also 'key: value') → dict; blank/invalid lines skipped."""
    out: Dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sep = "=" if "=" in line else (":" if ":" in line else None)
        if not sep:
            continue
        k, v = line.split(sep, 1)
        k = k.strip().lower().replace(" ", "_")
        if k:
            out[k] = v.strip()
    return out


def params_text(params: Any) -> str:
    """dict → 'key=value' lines (inverse of parse_params) for textarea prefill."""
    if not params:
        return ""
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            return params
    return "\n".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))


def params_from_form(form: dict, fields: Iterable[str], extra_key: str = "extra_params") -> Dict[str, Any]:
    """Collect ``fields`` named ``p_<field>`` from a form plus free key=value extras."""
    out: Dict[str, Any] = {}
    for f in fields:
        v = _str(form.get(f"p_{f}"))
        if v:
            out[f] = v
    out.update(parse_params(form.get(extra_key) or ""))
    return out


def explode_bom(plan_lines: List[dict], boms: Dict[str, dict], on_hand: Optional[Dict[str, Any]] = None) -> List[dict]:
    """
    Raw-material requirement = Σ plan qty / BOM output_qty × qty_per_output × (1 + scrap%).

    ``plan_lines``: [{product_id, planned_qty}], ``boms``: {product_id: {output_qty, lines:[...]}}.
    Semi-finished component lines with their own BOM are exploded recursively.
    Returns lines sorted by material_code with required/on_hand/to_procure.
    """
    on_hand = on_hand or {}
    req: Dict[str, dict] = {}

    def _add(product_id: str, qty: Decimal, depth: int = 0):
        bom = boms.get(product_id)
        if not bom or depth > 10:
            return
        out_qty = D(bom.get("output_qty")) or Decimal(1)
        for ln in bom.get("lines") or []:
            per = D(ln.get("qty_per_output")) * (1 + D(ln.get("scrap_pct")) / 100)
            need = qty / out_qty * per
            comp = ln.get("component_product_id")
            if ln.get("is_semi_finished") and comp and comp in boms:
                _add(comp, need, depth + 1)
                continue
            code = _str(ln.get("material_code")) or _str(ln.get("material_name"))
            row = req.setdefault(code, {"material_code": code, "material_name": ln.get("material_name") or code,
                                        "unit": ln.get("unit") or "kg", "required_qty": Decimal(0)})
            row["required_qty"] += need
    for pl in plan_lines:
        _add(pl.get("product_id"), D(pl.get("planned_qty")))
    out = []
    for code in sorted(req):
        r = req[code]
        r["required_qty"] = r["required_qty"].quantize(Decimal("0.001"))
        r["on_hand_qty"] = D(on_hand.get(code)).quantize(Decimal("0.001"))
        r["to_procure_qty"] = max(r["required_qty"] - r["on_hand_qty"], Decimal(0))
        out.append(r)
    return out


def planned_material_cost(bom: Optional[dict], qty: Any, costs: Dict[str, Any]) -> Decimal:
    if not bom:
        return Decimal(0)
    out_qty = D(bom.get("output_qty")) or Decimal(1)
    total = Decimal(0)
    for ln in bom.get("lines") or []:
        per = D(ln.get("qty_per_output")) * (1 + D(ln.get("scrap_pct")) / 100)
        total += D(qty) / out_qty * per * D(costs.get(_str(ln.get("material_code"))))
    return q2(total)


def planned_conversion_cost(routing: Optional[dict], qty: Any, wc_rates: Dict[str, Any]) -> Decimal:
    """Σ (setup min + run min/unit × qty) / 60 × work-centre hourly rate."""
    if not routing:
        return Decimal(0)
    total = Decimal(0)
    for op in routing.get("ops") or []:
        minutes = D(op.get("std_setup_minutes")) + D(op.get("std_run_minutes_per_unit")) * D(qty)
        total += minutes / 60 * D(wc_rates.get(op.get("work_center_id") or ""))
    return q2(total)


def routing_hours(routing: Optional[dict], qty: Any) -> Decimal:
    if not routing:
        return Decimal(0)
    minutes = sum((D(op.get("std_setup_minutes")) + D(op.get("std_run_minutes_per_unit")) * D(qty)
                   for op in routing.get("ops") or []), Decimal(0))
    return (minutes / 60).quantize(Decimal("0.01"))


def shift_hours(start: Any, end: Any) -> Decimal:
    """Duration of a shift in hours; overnight shifts wrap past midnight."""
    def _t(v):
        if isinstance(v, dtime):
            return v
        if isinstance(v, datetime):
            return v.time()
        try:
            return datetime.strptime(str(v)[:5], "%H:%M").time()
        except Exception:
            return None
    s, e = _t(start), _t(end)
    if not s or not e:
        return Decimal(0)
    mins = (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)
    if mins <= 0:
        mins += 24 * 60
    return (Decimal(mins) / 60).quantize(Decimal("0.01"))


def available_hours(period_start: date, period_end: date, non_working_days: Iterable[date],
                    shifts: List[dict], work_center_id: Optional[str] = None) -> Decimal:
    """Calendar days minus holidays/shutdowns × Σ daily shift hours for the work centre
    (shifts with no work centre apply everywhere; no shifts at all → 8 h/day)."""
    if not period_start or not period_end or period_end < period_start:
        return Decimal(0)
    blocked = set(non_working_days or ())
    days = sum(1 for i in range((period_end - period_start).days + 1)
               if (period_start + timedelta(days=i)) not in blocked)
    relevant = [s for s in shifts or [] if s.get("is_active", True)
                and (not s.get("work_center_id") or s.get("work_center_id") == work_center_id)]
    per_day = sum((shift_hours(s.get("start_time"), s.get("end_time")) for s in relevant), Decimal(0)) \
        if relevant else Decimal(8)
    return (Decimal(days) * per_day).quantize(Decimal("0.01"))


def pivot_materials(rows: List[dict], key_fields: Tuple[str, ...], material_field: str = "material_code",
                    value_fields: Tuple[str, ...] = ("issued", "consumed", "returned")) -> Tuple[List[str], List[dict]]:
    """Rows (one per order × material) → one row per order with <material>__<value> columns.
    Returns (sorted material codes, pivoted rows)."""
    mats = sorted({_str(r.get(material_field)) for r in rows if _str(r.get(material_field))})
    out: Dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r.get(f) for f in key_fields)
        row = out.setdefault(k, {f: r.get(f) for f in key_fields})
        m = _str(r.get(material_field))
        for vf in value_fields:
            row[f"{m}__{vf}"] = D(row.get(f"{m}__{vf}")) + D(r.get(vf))
            row[f"total_{vf}"] = D(row.get(f"total_{vf}")) + D(r.get(vf))
        for extra in ("drum_refs", "size_mm2", "product_name", "order_status"):
            if extra in r and extra not in row:
                row[extra] = r[extra]
    return mats, list(out.values())


def kpi_row(input_qty: Any, output_qty: Any, scrap_qty: Any, avail_hours: Any,
            downtime_minutes: Any, labor_run_hours: Any) -> dict:
    """Daily KPI for one machine/line. Run hours = logged labour run hours when
    present, else available minus downtime."""
    avail = D(avail_hours)
    dt_h = D(downtime_minutes) / 60
    run = D(labor_run_hours) if D(labor_run_hours) > 0 else max(avail - dt_h, Decimal(0))
    run = min(run, avail) if avail > 0 else run
    return {
        "input_qty": D(input_qty), "output_qty": D(output_qty), "scrap_qty": D(scrap_qty),
        "available_hours": avail.quantize(Decimal("0.01")), "run_hours": run.quantize(Decimal("0.01")),
        "downtime_minutes": D(downtime_minutes).quantize(Decimal("0.01")),
        "yield_pct": yield_pct(output_qty, input_qty), "utilisation_pct": utilisation_pct(run, avail),
    }


def order_is_past_due(order: dict, today: Optional[date] = None) -> bool:
    today = today or date.today()
    if order.get("status") in ("confirmed", "closed", "cancelled"):
        return False
    due = order.get("delivery_date") or order.get("planned_end")
    if isinstance(due, datetime):
        due = due.date()
    return bool(due) and due < today


def build_process_status(steps: Iterable[dict], found: Dict[str, dict]) -> List[dict]:
    """Merge live findings {key: {status, detail}} into the step list."""
    out = []
    for s in steps:
        f = found.get(s["key"]) or {}
        out.append({**s, "status": f.get("status") or "not_started",
                    "detail": f.get("detail") or "", "link": f.get("url") or s["url"]})
    return out


# ── DB access ───────────────────────────────────────────────────────

def _conn():
    from db import get_conn
    return get_conn()


def _json(value: Any):
    from psycopg2.extras import Json
    return Json(value or {})


def table_exists(cur, name: str) -> bool:
    try:
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1", (name,))
        return cur.fetchone() is not None
    except Exception:
        return False


def table_columns(cur, name: str) -> set:
    try:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (name,))
        return {r["column_name"] for r in cur.fetchall()}
    except Exception:
        return set()


def ensure_schema():
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("manufacturing schema ready")
    except Exception as e:
        logger.error("manufacturing schema init failed: %s", e)
    _install_approval_hook()


_HOOK_INSTALLED = False


def _install_approval_hook():
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return
    try:
        from approval_data_store import register_on_decided
        register_on_decided(_on_approval_decided)
        _HOOK_INSTALLED = True
    except Exception as exc:
        logger.debug("manufacturing approval hook not installed: %s", exc)


def _on_approval_decided(req: dict, status: str, actor: str, comment: str) -> None:
    et = req.get("entity_type")
    if et == "production_order":
        manufacturing_store.on_release_decided(req["entity_id"], req["company_id"], status, actor, comment)
    elif et == "raw_material_plan":
        manufacturing_store.on_rm_plan_decided(req["entity_id"], req["company_id"], status, actor, comment)


class ManufacturingDataStore:
    """All DB access. Methods never raise; they log and return None/[]/False."""

    def ensure_schema(self):
        ensure_schema()

    # ── generic helpers ─────────────────────────────────────────
    def _all(self, sql: str, params: tuple = ()) -> List[dict]:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("mfg query failed: %s | %s", e, sql[:120]); return []

    def _one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("mfg query failed: %s | %s", e, sql[:120]); return None

    def _exec(self, sql: str, params: tuple = ()) -> bool:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            return True
        except Exception as e:
            logger.error("mfg exec failed: %s | %s", e, sql[:120]); return False

    def next_sequence(self, cur, company_id: str, seq_name: str) -> int:
        """Gapless counter: UPDATE ... RETURNING under row lock (insert on first use)."""
        cur.execute("INSERT INTO mfg_sequences(company_id, seq_name, next_val) VALUES(%s,%s,0) ON CONFLICT DO NOTHING",
                    (company_id, seq_name))
        cur.execute("UPDATE mfg_sequences SET next_val=next_val+1 WHERE company_id=%s AND seq_name=%s RETURNING next_val",
                    (company_id, seq_name))
        return int(cur.fetchone()["next_val"])

    # ── settings ────────────────────────────────────────────────
    def get_settings(self, company_id: str) -> dict:
        row = self._one("SELECT * FROM mfg_settings WHERE company_id=%s", (company_id,))
        return row or {"company_id": company_id, "gl_wip_account": "", "gl_raw_material_account": "",
                       "gl_finished_goods_account": "", "gl_scrap_account": "", "default_plant_id": None,
                       "mto_auto_release": False}

    def save_settings(self, company_id: str, data: dict) -> bool:
        return self._exec(
            """INSERT INTO mfg_settings(company_id,gl_wip_account,gl_raw_material_account,gl_finished_goods_account,
               gl_scrap_account,default_plant_id,mto_auto_release,updated_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,NOW())
               ON CONFLICT (company_id) DO UPDATE SET gl_wip_account=EXCLUDED.gl_wip_account,
               gl_raw_material_account=EXCLUDED.gl_raw_material_account,
               gl_finished_goods_account=EXCLUDED.gl_finished_goods_account, gl_scrap_account=EXCLUDED.gl_scrap_account,
               default_plant_id=EXCLUDED.default_plant_id, mto_auto_release=EXCLUDED.mto_auto_release, updated_at=NOW()""",
            (company_id, _str(data.get("gl_wip_account")), _str(data.get("gl_raw_material_account")),
             _str(data.get("gl_finished_goods_account")), _str(data.get("gl_scrap_account")),
             _opt(data.get("default_plant_id")), _bool(data.get("mto_auto_release"))))

    def get_gl_accounts(self, company_id: str) -> List[dict]:
        return self._all("SELECT account_code, account_name, account_type FROM chart_of_accounts "
                         "WHERE company_id=%s ORDER BY account_code", (company_id,))

    # ── plants ──────────────────────────────────────────────────
    def get_plants(self, company_id: str, active_only: bool = False) -> List[dict]:
        sql = "SELECT * FROM mfg_plants WHERE company_id=%s" + (" AND is_active" if active_only else "") + " ORDER BY code, name"
        return self._all(sql, (company_id,))

    def get_plant(self, plant_id: str, company_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_plants WHERE id=%s AND company_id=%s", (plant_id, company_id))

    def save_plant(self, company_id: str, data: dict, plant_id: str = None) -> Optional[str]:
        if not _str(data.get("name")):
            return None
        vals = (_str(data.get("code")), _str(data.get("name")), _str(data.get("location")),
                _str(data.get("product_group")), _bool(data.get("is_active", "on")))
        if plant_id:
            ok = self._exec("UPDATE mfg_plants SET code=%s,name=%s,location=%s,product_group=%s,is_active=%s "
                            "WHERE id=%s AND company_id=%s", vals + (plant_id, company_id))
            return plant_id if ok else None
        pid = _uid()
        ok = self._exec("INSERT INTO mfg_plants(id,company_id,code,name,location,product_group,is_active) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s)", (pid, company_id) + vals)
        return pid if ok else None

    # ── work centres ────────────────────────────────────────────
    def get_work_centers(self, company_id: str, plant_id: str = None) -> List[dict]:
        sql = """SELECT w.*, p.name AS plant_name FROM mfg_work_centers w
                 LEFT JOIN mfg_plants p ON p.id=w.plant_id WHERE w.company_id=%s"""
        params: list = [company_id]
        if plant_id:
            sql += " AND w.plant_id=%s"; params.append(plant_id)
        return self._all(sql + " ORDER BY w.code, w.name", tuple(params))

    def get_work_center(self, wc_id: str, company_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_work_centers WHERE id=%s AND company_id=%s", (wc_id, company_id))

    def save_work_center(self, company_id: str, data: dict, wc_id: str = None) -> Optional[str]:
        if not _str(data.get("name")):
            return None
        vals = (_opt(data.get("plant_id")), _str(data.get("code")), _str(data.get("name")),
                data.get("type") if data.get("type") in WC_TYPES else "other",
                _num(data.get("std_capacity_per_hour")),
                data.get("capacity_unit") if data.get("capacity_unit") in CAPACITY_UNITS else "kg",
                _num(data.get("cost_rate_per_hour")), _bool(data.get("is_active", "on")))
        if wc_id:
            ok = self._exec("""UPDATE mfg_work_centers SET plant_id=%s,code=%s,name=%s,type=%s,std_capacity_per_hour=%s,
                               capacity_unit=%s,cost_rate_per_hour=%s,is_active=%s WHERE id=%s AND company_id=%s""",
                            vals + (wc_id, company_id))
            return wc_id if ok else None
        wid = _uid()
        ok = self._exec("""INSERT INTO mfg_work_centers(id,company_id,plant_id,code,name,type,std_capacity_per_hour,
                           capacity_unit,cost_rate_per_hour,is_active) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (wid, company_id) + vals)
        return wid if ok else None

    def wc_rates(self, company_id: str) -> Dict[str, Decimal]:
        return {r["id"]: D(r["cost_rate_per_hour"]) for r in self.get_work_centers(company_id)}

    # ── machines ────────────────────────────────────────────────
    def get_machines(self, company_id: str, work_center_id: str = None) -> List[dict]:
        sql = """SELECT m.*, w.name AS work_center_name, w.code AS work_center_code FROM mfg_machines m
                 LEFT JOIN mfg_work_centers w ON w.id=m.work_center_id WHERE m.company_id=%s"""
        params: list = [company_id]
        if work_center_id:
            sql += " AND m.work_center_id=%s"; params.append(work_center_id)
        return self._all(sql + " ORDER BY m.code, m.name", tuple(params))

    def get_machine(self, machine_id: str, company_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_machines WHERE id=%s AND company_id=%s", (machine_id, company_id))

    def save_machine(self, company_id: str, data: dict, machine_id: str = None) -> Optional[str]:
        if not _str(data.get("name")):
            return None
        vals = (_opt(data.get("work_center_id")), _str(data.get("code")), _str(data.get("name")),
                _str(data.get("manufacturer")), _str(data.get("model")), _opt(data.get("install_date")),
                _num(data.get("std_capacity_per_hour")),
                data.get("status") if data.get("status") in MACHINE_STATUSES else "active")
        if machine_id:
            ok = self._exec("""UPDATE mfg_machines SET work_center_id=%s,code=%s,name=%s,manufacturer=%s,model=%s,
                               install_date=%s,std_capacity_per_hour=%s,status=%s WHERE id=%s AND company_id=%s""",
                            vals + (machine_id, company_id))
            return machine_id if ok else None
        mid = _uid()
        ok = self._exec("""INSERT INTO mfg_machines(id,company_id,work_center_id,code,name,manufacturer,model,install_date,
                           std_capacity_per_hour,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (mid, company_id) + vals)
        return mid if ok else None

    def set_machine_status(self, machine_id: str, company_id: str, status: str) -> bool:
        if status not in MACHINE_STATUSES:
            return False
        return self._exec("UPDATE mfg_machines SET status=%s WHERE id=%s AND company_id=%s", (status, machine_id, company_id))

    # ── products ────────────────────────────────────────────────
    def get_products(self, company_id: str, product_type: str = None, search: str = None, active_only=False) -> List[dict]:
        sql = """SELECT p.*,
                   (SELECT COUNT(*) FROM mfg_bom_headers b WHERE b.product_id=p.id AND b.status='active') AS active_boms,
                   (SELECT COUNT(*) FROM mfg_tds t WHERE t.product_id=p.id AND t.status='approved') AS approved_tds,
                   (SELECT COUNT(*) FROM mfg_routings r WHERE r.product_id=p.id AND r.status='active') AS active_routings
                 FROM mfg_products p WHERE p.company_id=%s"""
        params: list = [company_id]
        if product_type:
            sql += " AND p.product_type=%s"; params.append(product_type)
        if search:
            sql += " AND (p.code ILIKE %s OR p.name ILIKE %s OR p.sku ILIKE %s)"
            params += [f"%{search}%"] * 3
        if active_only:
            sql += " AND p.is_active"
        return self._all(sql + " ORDER BY p.code", tuple(params))

    def get_product(self, product_id: str, company_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_products WHERE id=%s AND company_id=%s", (product_id, company_id))

    def product_by_code(self, company_id: str, code: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_products WHERE company_id=%s AND code=%s", (company_id, _str(code)))

    def save_product(self, company_id: str, data: dict, product_id: str = None) -> Optional[str]:
        if not _str(data.get("code")) or not _str(data.get("name")):
            return None
        vals = (_str(data.get("code")), _str(data.get("name")), _str(data.get("description")),
                data.get("product_type") if data.get("product_type") in PRODUCT_TYPES else "finished",
                _opt(data.get("size_mm2")), _str(data.get("color")),
                data.get("unit") if data.get("unit") in PRODUCT_UNITS else "m",
                _opt(data.get("std_length_per_roll")), _str(data.get("sku")), _opt(data.get("inventory_item_id")),
                _bool(data.get("is_active", "on")))
        if product_id:
            ok = self._exec("""UPDATE mfg_products SET code=%s,name=%s,description=%s,product_type=%s,size_mm2=%s,color=%s,
                               unit=%s,std_length_per_roll=%s,sku=%s,inventory_item_id=%s,is_active=%s
                               WHERE id=%s AND company_id=%s""", vals + (product_id, company_id))
            return product_id if ok else None
        pid = _uid()
        ok = self._exec("""INSERT INTO mfg_products(id,company_id,code,name,description,product_type,size_mm2,color,unit,
                           std_length_per_roll,sku,inventory_item_id,is_active) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (pid, company_id) + vals)
        if ok and _bool(data.get("mirror_inventory")):
            self.mirror_to_inventory(pid, company_id)
        return pid if ok else None

    def mirror_to_inventory(self, product_id: str, company_id: str) -> Optional[str]:
        """Create (or link) an inventory_items row for the product when that table exists."""
        p = self.get_product(product_id, company_id)
        if not p:
            return None
        if p.get("inventory_item_id"):
            return p["inventory_item_id"]
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cols = table_columns(cur, "inventory_items")
                    if not {"id", "company_id", "sku", "name"} <= cols:
                        return None
                    cur.execute("SELECT id FROM inventory_items WHERE company_id=%s AND sku=%s LIMIT 1",
                                (company_id, p.get("sku") or p["code"]))
                    row = cur.fetchone()
                    if row:
                        iid = row["id"]
                    else:
                        iid = _uid()
                        now = datetime.utcnow().isoformat()
                        data = {"id": iid, "company_id": company_id, "sku": p.get("sku") or p["code"], "name": p["name"],
                                "description": p.get("description") or "", "category": "Finished goods",
                                "unit": p.get("unit") or "m", "status": "active", "created_at": now, "updated_at": now}
                        use = {k: v for k, v in data.items() if k in cols}
                        cur.execute(f"INSERT INTO inventory_items({','.join(use)}) VALUES({','.join(['%s'] * len(use))})",
                                    tuple(use.values()))
                    cur.execute("UPDATE mfg_products SET inventory_item_id=%s WHERE id=%s", (iid, product_id))
                    return iid
        except Exception as e:
            logger.error("mirror_to_inventory: %s", e); return None

    def products_missing_bom(self, company_id: str) -> List[dict]:
        return self._all("""SELECT p.* FROM mfg_products p WHERE p.company_id=%s AND p.is_active
                            AND NOT EXISTS (SELECT 1 FROM mfg_bom_headers b WHERE b.product_id=p.id AND b.status='active')
                            ORDER BY p.code""", (company_id,))

    # ── TDS ─────────────────────────────────────────────────────
    def get_tds_list(self, product_id: str) -> List[dict]:
        return self._all("SELECT * FROM mfg_tds WHERE product_id=%s ORDER BY version DESC", (product_id,))

    def get_tds(self, tds_id: str, company_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_tds WHERE id=%s AND company_id=%s", (tds_id, company_id))

    def approved_tds_for(self, product_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM mfg_tds WHERE product_id=%s AND status='approved' ORDER BY version DESC LIMIT 1",
                         (product_id,))

    def create_tds(self, company_id: str, product_id: str, params: dict, notes: str, actor: str) -> Optional[str]:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM mfg_tds WHERE product_id=%s", (product_id,))
                    v = cur.fetchone()["v"]
                    tid = _uid()
                    cur.execute("""INSERT INTO mfg_tds(id,company_id,product_id,version,status,parameters,notes,created_by)
                                   VALUES(%s,%s,%s,%s,'draft',%s,%s,%s)""",
                                (tid, company_id, product_id, v, _json(params), _str(notes), actor))
                    return tid
        except Exception as e:
            logger.error("create_tds: %s", e); return None

    def approve_tds(self, tds_id: str, company_id: str, actor: str) -> bool:
        """Approve a version; any previously approved version becomes superseded."""
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT product_id FROM mfg_tds WHERE id=%s AND company_id=%s", (tds_id, company_id))
                    row = cur.fetchone()
                    if not row:
                        return False
                    cur.execute("UPDATE mfg_tds SET status='superseded' WHERE product_id=%s AND status='approved' AND id<>%s",
                                (row["product_id"], tds_id))
                    cur.execute("UPDATE mfg_tds SET status='approved', approved_by=%s, approved_at=NOW() WHERE id=%s",
                                (actor, tds_id))
            return True
        except Exception as e:
            logger.error("approve_tds: %s", e); return False

    # ── BOM ─────────────────────────────────────────────────────
    def get_boms(self, product_id: str) -> List[dict]:
        return self._all("""SELECT b.*, (SELECT COUNT(*) FROM mfg_bom_lines l WHERE l.bom_id=b.id) AS line_count
                            FROM mfg_bom_headers b WHERE b.product_id=%s ORDER BY b.version DESC""", (product_id,))

    def get_bom(self, bom_id: str, company_id: str = None) -> Optional[dict]:
        sql = "SELECT * FROM mfg_bom_headers WHERE id=%s" + (" AND company_id=%s" if company_id else "")
        b = self._one(sql, (bom_id, company_id) if company_id else (bom_id,))
        if b:
            b["lines"] = self._all("""SELECT l.*, w.name AS work_center_name, c.code AS component_code
                                      FROM mfg_bom_lines l LEFT JOIN mfg_work_centers w ON w.id=l.work_center_id
                                      LEFT JOIN mfg_products c ON c.id=l.component_product_id
                                      WHERE l.bom_id=%s ORDER BY l.operation_seq, l.material_code""", (bom_id,))
        return b

    def active_bom_for(self, product_id: str) -> Optional[dict]:
        b = self._one("SELECT * FROM mfg_bom_headers WHERE product_id=%s AND status='active' ORDER BY version DESC LIMIT 1",
                      (product_id,))
        return self.get_bom(b["id"]) if b else None

    def active_boms_map(self, company_id: str) -> Dict[str, dict]:
        heads = self._all("""SELECT DISTINCT ON (product_id) * FROM mfg_bom_headers
                             WHERE company_id=%s AND status='active' ORDER BY product_id, version DESC""", (company_id,))
        out = {}
        for h in heads:
            h["lines"] = self._all("SELECT * FROM mfg_bom_lines WHERE bom_id=%s", (h["id"],))
            out[h["product_id"]] = h
        return out

    def create_bom(self, company_id: str, product_id: str, data: dict, copy_from: str = None) -> Optional[str]:
        """New BOM version (draft). ``copy_from`` copies lines of an earlier version."""
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM mfg_bom_headers WHERE product_id=%s", (product_id,))
                    v = cur.fetchone()["v"]
                    bid = _uid()
                    cur.execute("""INSERT INTO mfg_bom_headers(id,company_id,product_id,tds_id,version,status,output_qty,output_unit,notes)
                                   VALUES(%s,%s,%s,%s,%s,'draft',%s,%s,%s)""",
                                (bid, company_id, product_id, _opt(data.get("tds_id")), v,
                                 D(data.get("output_qty")) or Decimal(1), _str(data.get("output_unit")) or "m",
                                 _str(data.get("notes"))))
                    if copy_from:
                        cur.execute("""INSERT INTO mfg_bom_lines(id,bom_id,component_product_id,material_name,material_code,
                                       qty_per_output,unit,scrap_pct,is_semi_finished,work_center_id,operation_seq)
                                       SELECT md5(random()::text || id), %s, component_product_id, material_name, material_code,
                                       qty_per_output, unit, scrap_pct, is_semi_finished, work_center_id, operation_seq
                                       FROM mfg_bom_lines WHERE bom_id=%s""", (bid, copy_from))
                    return bid
        except Exception as e:
            logger.error("create_bom: %s", e); return None

    def add_bom_line(self, bom_id: str, data: dict) -> bool:
        if not (_str(data.get("material_name")) or _str(data.get("material_code"))):
            return False
        return self._exec("""INSERT INTO mfg_bom_lines(id,bom_id,component_product_id,material_name,material_code,qty_per_output,
                             unit,scrap_pct,is_semi_finished,work_center_id,operation_seq) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), bom_id, _opt(data.get("component_product_id")), _str(data.get("material_name")),
                           _str(data.get("material_code")), _num(data.get("qty_per_output")), _str(data.get("unit")) or "kg",
                           _num(data.get("scrap_pct")), _bool(data.get("is_semi_finished")), _opt(data.get("work_center_id")),
                           _int(data.get("operation_seq"))))

    def delete_bom_line(self, line_id: str, bom_id: str) -> bool:
        return self._exec("DELETE FROM mfg_bom_lines WHERE id=%s AND bom_id=%s", (line_id, bom_id))

    def set_bom_status(self, bom_id: str, company_id: str, status: str) -> bool:
        """Activating a BOM obsoletes the previously active version (history kept)."""
        if status not in ("draft", "active", "obsolete"):
            return False
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT product_id FROM mfg_bom_headers WHERE id=%s AND company_id=%s", (bom_id, company_id))
                    row = cur.fetchone()
                    if not row:
                        return False
                    if status == "active":
                        cur.execute("UPDATE mfg_bom_headers SET status='obsolete' WHERE product_id=%s AND status='active' AND id<>%s",
                                    (row["product_id"], bom_id))
                    cur.execute("UPDATE mfg_bom_headers SET status=%s WHERE id=%s", (status, bom_id))
            return True
        except Exception as e:
            logger.error("set_bom_status: %s", e); return False

    # ── routings ────────────────────────────────────────────────
    def get_routings(self, product_id: str) -> List[dict]:
        return self._all("""SELECT r.*, (SELECT COUNT(*) FROM mfg_routing_ops o WHERE o.routing_id=r.id) AS op_count
                            FROM mfg_routings r WHERE r.product_id=%s ORDER BY r.version DESC""", (product_id,))

    def get_routing(self, routing_id: str, company_id: str = None) -> Optional[dict]:
        sql = "SELECT * FROM mfg_routings WHERE id=%s" + (" AND company_id=%s" if company_id else "")
        r = self._one(sql, (routing_id, company_id) if company_id else (routing_id,))
        if r:
            r["ops"] = self._all("""SELECT o.*, w.name AS work_center_name, w.cost_rate_per_hour FROM mfg_routing_ops o
                                    LEFT JOIN mfg_work_centers w ON w.id=o.work_center_id
                                    WHERE o.routing_id=%s ORDER BY o.seq""", (routing_id,))
        return r

    def active_routing_for(self, product_id: str) -> Optional[dict]:
        r = self._one("SELECT * FROM mfg_routings WHERE product_id=%s AND status='active' ORDER BY version DESC LIMIT 1",
                      (product_id,))
        return self.get_routing(r["id"]) if r else None

    def create_routing(self, company_id: str, product_id: str, notes: str = "") -> Optional[str]:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM mfg_routings WHERE product_id=%s", (product_id,))
                    v = cur.fetchone()["v"]
                    rid = _uid()
                    cur.execute("INSERT INTO mfg_routings(id,company_id,product_id,version,status,notes) VALUES(%s,%s,%s,%s,'draft',%s)",
                                (rid, company_id, product_id, v, _str(notes)))
                    return rid
        except Exception as e:
            logger.error("create_routing: %s", e); return None

    def add_routing_op(self, routing_id: str, data: dict, params: dict) -> bool:
        if not _str(data.get("operation_name")):
            return False
        return self._exec("""INSERT INTO mfg_routing_ops(id,routing_id,seq,work_center_id,operation_name,std_setup_minutes,
                             std_run_minutes_per_unit,process_params) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), routing_id, _int(data.get("seq"), 10), _opt(data.get("work_center_id")),
                           _str(data.get("operation_name")), _num(data.get("std_setup_minutes")),
                           _num(data.get("std_run_minutes_per_unit")), _json(params)))

    def delete_routing_op(self, op_id: str, routing_id: str) -> bool:
        return self._exec("DELETE FROM mfg_routing_ops WHERE id=%s AND routing_id=%s", (op_id, routing_id))

    def set_routing_status(self, routing_id: str, company_id: str, status: str) -> bool:
        if status not in ("draft", "active", "obsolete"):
            return False
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT product_id FROM mfg_routings WHERE id=%s AND company_id=%s", (routing_id, company_id))
                    row = cur.fetchone()
                    if not row:
                        return False
                    if status == "active":
                        cur.execute("UPDATE mfg_routings SET status='obsolete' WHERE product_id=%s AND status='active' AND id<>%s",
                                    (row["product_id"], routing_id))
                    cur.execute("UPDATE mfg_routings SET status=%s WHERE id=%s", (status, routing_id))
            return True
        except Exception as e:
            logger.error("set_routing_status: %s", e); return False

    # ── calendar & shifts ───────────────────────────────────────
    def get_calendar(self, company_id: str, year: int = None) -> List[dict]:
        sql = """SELECT c.*, p.name AS plant_name FROM mfg_calendar c LEFT JOIN mfg_plants p ON p.id=c.plant_id
                 WHERE c.company_id=%s"""
        params: list = [company_id]
        if year:
            sql += " AND EXTRACT(YEAR FROM c.date)=%s"; params.append(year)
        return self._all(sql + " ORDER BY c.date", tuple(params))

    def add_calendar_entry(self, company_id: str, data: dict) -> bool:
        if not _opt(data.get("date")):
            return False
        return self._exec("INSERT INTO mfg_calendar(id,company_id,plant_id,date,kind,description) VALUES(%s,%s,%s,%s,%s,%s)",
                          (_uid(), company_id, _opt(data.get("plant_id")), data.get("date"),
                           data.get("kind") if data.get("kind") in CALENDAR_KINDS else "holiday", _str(data.get("description"))))

    def delete_calendar_entry(self, entry_id: str, company_id: str) -> bool:
        return self._exec("DELETE FROM mfg_calendar WHERE id=%s AND company_id=%s", (entry_id, company_id))

    def non_working_days(self, company_id: str, start: date, end: date, plant_id: str = None) -> List[date]:
        rows = self._all("""SELECT date FROM mfg_calendar WHERE company_id=%s AND date BETWEEN %s AND %s
                            AND (plant_id IS NULL OR plant_id=%s)""", (company_id, start, end, plant_id))
        return [r["date"] for r in rows]

    def get_shifts(self, company_id: str, active_only: bool = False) -> List[dict]:
        sql = """SELECT s.*, w.name AS work_center_name FROM mfg_shifts s LEFT JOIN mfg_work_centers w ON w.id=s.work_center_id
                 WHERE s.company_id=%s""" + (" AND s.is_active" if active_only else "")
        return self._all(sql + " ORDER BY s.start_time, s.name", (company_id,))

    def save_shift(self, company_id: str, data: dict, shift_id: str = None) -> Optional[str]:
        if not _str(data.get("name")):
            return None
        vals = (_opt(data.get("work_center_id")), _str(data.get("name")), _opt(data.get("start_time")),
                _opt(data.get("end_time")), _str(data.get("shift_leader")), _str(data.get("line_supervisor")),
                _bool(data.get("is_active", "on")))
        if shift_id:
            ok = self._exec("""UPDATE mfg_shifts SET work_center_id=%s,name=%s,start_time=%s,end_time=%s,shift_leader=%s,
                               line_supervisor=%s,is_active=%s WHERE id=%s AND company_id=%s""", vals + (shift_id, company_id))
            return shift_id if ok else None
        sid = _uid()
        ok = self._exec("""INSERT INTO mfg_shifts(id,company_id,work_center_id,name,start_time,end_time,shift_leader,
                           line_supervisor,is_active) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (sid, company_id) + vals)
        return sid if ok else None

    # ── downtime categories / reasons / scrap types / material costs ──
    def seed_defaults(self, company_id: str) -> int:
        """Lazily create the 4 downtime categories and default scrap types per company."""
        n = 0
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM mfg_downtime_categories WHERE company_id=%s", (company_id,))
                    if cur.fetchone()["c"] == 0:
                        for name in DEFAULT_DOWNTIME_CATEGORIES:
                            cur.execute("INSERT INTO mfg_downtime_categories(id,company_id,name) VALUES(%s,%s,%s)",
                                        (_uid(), company_id, name)); n += 1
                    cur.execute("SELECT COUNT(*) AS c FROM mfg_scrap_types WHERE company_id=%s", (company_id,))
                    if cur.fetchone()["c"] == 0:
                        for name in DEFAULT_SCRAP_TYPES:
                            cur.execute("INSERT INTO mfg_scrap_types(id,company_id,name) VALUES(%s,%s,%s)",
                                        (_uid(), company_id, name)); n += 1
        except Exception as e:
            logger.error("seed_defaults: %s", e)
        return n

    def get_downtime_categories(self, company_id: str) -> List[dict]:
        cats = self._all("SELECT * FROM mfg_downtime_categories WHERE company_id=%s ORDER BY name", (company_id,))
        reasons = self._all("""SELECT r.* FROM mfg_downtime_reasons r JOIN mfg_downtime_categories c ON c.id=r.category_id
                               WHERE c.company_id=%s ORDER BY r.name""", (company_id,))
        by_cat: Dict[str, list] = {}
        for r in reasons:
            by_cat.setdefault(r["category_id"], []).append(r)
        for c in cats:
            c["reasons"] = by_cat.get(c["id"], [])
        return cats

    def add_downtime_category(self, company_id: str, name: str) -> bool:
        return bool(_str(name)) and self._exec("INSERT INTO mfg_downtime_categories(id,company_id,name) VALUES(%s,%s,%s)",
                                               (_uid(), company_id, _str(name)))

    def add_downtime_reason(self, category_id: str, name: str) -> bool:
        return bool(_str(name)) and self._exec("INSERT INTO mfg_downtime_reasons(id,category_id,name) VALUES(%s,%s,%s)",
                                               (_uid(), category_id, _str(name)))

    def get_scrap_types(self, company_id: str) -> List[dict]:
        return self._all("SELECT * FROM mfg_scrap_types WHERE company_id=%s ORDER BY name", (company_id,))

    def add_scrap_type(self, company_id: str, name: str) -> bool:
        return bool(_str(name)) and self._exec("INSERT INTO mfg_scrap_types(id,company_id,name) VALUES(%s,%s,%s)",
                                               (_uid(), company_id, _str(name)))

    def get_material_costs(self, company_id: str) -> List[dict]:
        return self._all("SELECT * FROM mfg_material_costs WHERE company_id=%s ORDER BY material_code", (company_id,))

    def material_cost_map(self, company_id: str) -> Dict[str, Decimal]:
        return {r["material_code"]: D(r["std_cost"]) for r in self.get_material_costs(company_id)}

    def save_material_cost(self, company_id: str, data: dict) -> bool:
        if not _str(data.get("material_code")):
            return False
        return self._exec("""INSERT INTO mfg_material_costs(company_id,material_code,material_name,unit,std_cost,currency)
                             VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT (company_id, material_code) DO UPDATE SET
                             material_name=EXCLUDED.material_name, unit=EXCLUDED.unit, std_cost=EXCLUDED.std_cost, currency=EXCLUDED.currency""",
                          (company_id, _str(data.get("material_code")), _str(data.get("material_name")),
                           _str(data.get("unit")) or "kg", _num(data.get("std_cost")), _str(data.get("currency")) or "ETB"))

    def delete_material_cost(self, company_id: str, code: str) -> bool:
        return self._exec("DELETE FROM mfg_material_costs WHERE company_id=%s AND material_code=%s", (company_id, code))

    # ── production plans ────────────────────────────────────────
    def get_plans(self, company_id: str, status: str = None) -> List[dict]:
        sql = """SELECT pl.*, p.name AS plant_name,
                   (SELECT COUNT(*) FROM mfg_plan_lines l WHERE l.plan_id=pl.id) AS line_count,
                   (SELECT COALESCE(SUM(planned_qty),0) FROM mfg_plan_lines l WHERE l.plan_id=pl.id) AS total_qty
                 FROM mfg_production_plans pl LEFT JOIN mfg_plants p ON p.id=pl.plant_id WHERE pl.company_id=%s"""
        params: list = [company_id]
        if status:
            sql += " AND pl.status=%s"; params.append(status)
        return self._all(sql + " ORDER BY pl.period_start DESC NULLS LAST, pl.created_at DESC", tuple(params))

    def get_plan(self, plan_id: str, company_id: str) -> Optional[dict]:
        p = self._one("""SELECT pl.*, p.name AS plant_name FROM mfg_production_plans pl
                         LEFT JOIN mfg_plants p ON p.id=pl.plant_id WHERE pl.id=%s AND pl.company_id=%s""", (plan_id, company_id))
        if p:
            p["lines"] = self._all("""SELECT l.*, pr.code AS product_code, pr.name AS product_name, pr.size_mm2, w.name AS work_center_name
                                      FROM mfg_plan_lines l JOIN mfg_products pr ON pr.id=l.product_id
                                      LEFT JOIN mfg_work_centers w ON w.id=l.work_center_id WHERE l.plan_id=%s ORDER BY pr.code""",
                                   (plan_id,))
        return p

    def create_plan(self, company_id: str, data: dict, actor: str) -> Optional[str]:
        pid = _uid()
        ok = self._exec("""INSERT INTO mfg_production_plans(id,company_id,plant_id,period_type,period_start,period_end,status,notes,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,'draft',%s,%s)""",
                        (pid, company_id, _opt(data.get("plant_id")),
                         data.get("period_type") if data.get("period_type") in PLAN_PERIODS else "monthly",
                         _opt(data.get("period_start")), _opt(data.get("period_end")), _str(data.get("notes")), actor))
        return pid if ok else None

    def add_plan_line(self, plan_id: str, data: dict) -> bool:
        if not _opt(data.get("product_id")):
            return False
        return self._exec("""INSERT INTO mfg_plan_lines(id,plan_id,product_id,work_center_id,planned_qty,unit,planned_hours,basis)
                             VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), plan_id, data.get("product_id"), _opt(data.get("work_center_id")), _num(data.get("planned_qty")),
                           _str(data.get("unit")) or "m", _num(data.get("planned_hours")),
                           data.get("basis") if data.get("basis") in PLAN_BASIS else "demand"))

    def delete_plan_line(self, line_id: str, plan_id: str) -> bool:
        return self._exec("DELETE FROM mfg_plan_lines WHERE id=%s AND plan_id=%s", (line_id, plan_id))

    def set_plan_status(self, plan_id: str, company_id: str, status: str) -> bool:
        return status in PLAN_STATUSES and self._exec(
            "UPDATE mfg_production_plans SET status=%s WHERE id=%s AND company_id=%s", (status, plan_id, company_id))

    def plan_vs_actual(self, company_id: str, plan: dict) -> List[dict]:
        """Planned qty per product in the plan vs produced qty (production logs) in the period."""
        if not plan or not plan.get("period_start") or not plan.get("period_end"):
            return [dict(l, actual_qty=Decimal(0)) for l in (plan or {}).get("lines", [])]
        actual = {r["product_id"]: D(r["q"]) for r in self._all(
            """SELECT o.product_id, SUM(l.output_qty) AS q FROM mfg_production_logs l
               JOIN mfg_production_orders o ON o.id=l.order_id
               WHERE l.company_id=%s AND l.log_date BETWEEN %s AND %s GROUP BY o.product_id""",
            (company_id, plan["period_start"], plan["period_end"]))}
        out = []
        for l in plan.get("lines", []):
            a = actual.get(l["product_id"], Decimal(0))
            diff, pct = variance(l["planned_qty"], a)
            out.append(dict(l, actual_qty=a, variance=diff, variance_pct=pct))
        return out

    # ── capacity ────────────────────────────────────────────────
    def get_capacity_plans(self, company_id: str) -> List[dict]:
        return self._all("""SELECT c.*, w.name AS work_center_name, w.code AS work_center_code FROM mfg_capacity_plans c
                            LEFT JOIN mfg_work_centers w ON w.id=c.work_center_id WHERE c.company_id=%s
                            ORDER BY c.period_start DESC NULLS LAST""", (company_id,))

    def compute_capacity(self, company_id: str, start: date, end: date, plant_id: str = None) -> List[dict]:
        """Per work centre: available hours (calendar − holidays × shifts) vs planned hours
        (approved/draft plan lines in the window + open production orders' routing hours)."""
        shifts = self.get_shifts(company_id, active_only=True)
        blocked = self.non_working_days(company_id, start, end, plant_id)
        planned = {r["work_center_id"]: D(r["h"]) for r in self._all(
            """SELECT l.work_center_id, SUM(l.planned_hours) AS h FROM mfg_plan_lines l
               JOIN mfg_production_plans p ON p.id=l.plan_id WHERE p.company_id=%s AND p.status<>'closed'
               AND l.work_center_id IS NOT NULL AND p.period_start<=%s AND p.period_end>=%s GROUP BY l.work_center_id""",
            (company_id, end, start))}
        out = []
        for wc in self.get_work_centers(company_id, plant_id):
            if not wc.get("is_active"):
                continue
            avail = available_hours(start, end, blocked, shifts, wc["id"])
            ph = planned.get(wc["id"], Decimal(0))
            out.append({"work_center_id": wc["id"], "work_center": wc["name"], "code": wc["code"], "type": wc["type"],
                        "available_hours": avail, "planned_hours": ph, "utilisation_pct": utilisation_pct(ph, avail),
                        "std_capacity_per_hour": wc["std_capacity_per_hour"], "capacity_unit": wc["capacity_unit"],
                        "capacity_qty": (avail * D(wc["std_capacity_per_hour"])).quantize(Decimal("0.01"))})
        return out

    def save_capacity_plan(self, company_id: str, data: dict) -> bool:
        if not _opt(data.get("work_center_id")):
            return False
        avail, ph = _num(data.get("available_hours")), _num(data.get("planned_hours"))
        return self._exec("""INSERT INTO mfg_capacity_plans(id,company_id,work_center_id,period_start,period_end,available_hours,
                             planned_hours,utilisation_pct,notes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), company_id, data.get("work_center_id"), _opt(data.get("period_start")),
                           _opt(data.get("period_end")), avail, ph, utilisation_pct(ph, avail), _str(data.get("notes"))))

    # ── raw-material plans ──────────────────────────────────────
    def get_rm_plans(self, company_id: str) -> List[dict]:
        return self._all("""SELECT r.*, (SELECT COUNT(*) FROM mfg_rm_plan_lines l WHERE l.plan_id=r.id) AS line_count,
                            (SELECT COALESCE(SUM(to_procure_qty),0) FROM mfg_rm_plan_lines l WHERE l.plan_id=r.id) AS total_to_procure
                            FROM mfg_raw_material_plans r WHERE r.company_id=%s ORDER BY r.year DESC, r.created_at DESC""", (company_id,))

    def get_rm_plan(self, plan_id: str, company_id: str) -> Optional[dict]:
        p = self._one("SELECT * FROM mfg_raw_material_plans WHERE id=%s AND company_id=%s", (plan_id, company_id))
        if p:
            p["lines"] = self._all("SELECT * FROM mfg_rm_plan_lines WHERE plan_id=%s ORDER BY material_code", (plan_id,))
        return p

    def create_rm_plan(self, company_id: str, data: dict, actor: str) -> Optional[str]:
        pid = _uid()
        ok = self._exec("""INSERT INTO mfg_raw_material_plans(id,company_id,year,production_plan_id,status,prepared_by,notes)
                           VALUES(%s,%s,%s,%s,'draft',%s,%s)""",
                        (pid, company_id, _int(data.get("year"), date.today().year), _opt(data.get("production_plan_id")),
                         actor, _str(data.get("notes"))))
        return pid if ok else None

    def add_rm_line(self, plan_id: str, data: dict) -> bool:
        if not _str(data.get("material_code")):
            return False
        req, oh = _num(data.get("required_qty")), _num(data.get("on_hand_qty"))
        return self._exec("""INSERT INTO mfg_rm_plan_lines(id,plan_id,material_code,material_name,unit,required_qty,on_hand_qty,to_procure_qty)
                             VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), plan_id, _str(data.get("material_code")), _str(data.get("material_name")),
                           _str(data.get("unit")) or "kg", req, oh, max(req - oh, Decimal(0))))

    def delete_rm_line(self, line_id: str, plan_id: str) -> bool:
        return self._exec("DELETE FROM mfg_rm_plan_lines WHERE id=%s AND plan_id=%s", (line_id, plan_id))

    def inventory_on_hand(self, company_id: str, codes: Iterable[str]) -> Dict[str, Decimal]:
        """Stock per material code from inventory_items (matched on sku) when the table exists."""
        codes = [c for c in codes if c]
        if not codes:
            return {}
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    if not {"sku", "current_stock", "company_id"} <= table_columns(cur, "inventory_items"):
                        return {}
                    cur.execute("SELECT sku, current_stock FROM inventory_items WHERE company_id=%s AND sku = ANY(%s)",
                                (company_id, codes))
                    return {r["sku"]: D(r["current_stock"]) for r in cur.fetchall()}
        except Exception as e:
            logger.debug("inventory_on_hand: %s", e); return {}

    def explode_rm_plan(self, plan_id: str, company_id: str) -> int:
        """Replace the plan's lines with the BOM explosion of its production plan
        (or of every non-closed plan of the year when none is linked)."""
        plan = self.get_rm_plan(plan_id, company_id)
        if not plan or plan["status"] != "draft":
            return -1
        if plan.get("production_plan_id"):
            src = [self.get_plan(plan["production_plan_id"], company_id)]
        else:
            src = [self.get_plan(p["id"], company_id) for p in self.get_plans(company_id)
                   if p.get("period_start") and p["period_start"].year == plan["year"] and p["status"] != "closed"]
        plan_lines = [l for p in src if p for l in p.get("lines", [])]
        boms = self.active_boms_map(company_id)
        lines = explode_bom(plan_lines, boms)
        on_hand = self.inventory_on_hand(company_id, [l["material_code"] for l in lines])
        lines = explode_bom(plan_lines, boms, on_hand)
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM mfg_rm_plan_lines WHERE plan_id=%s", (plan_id,))
                    for l in lines:
                        cur.execute("""INSERT INTO mfg_rm_plan_lines(id,plan_id,material_code,material_name,unit,required_qty,on_hand_qty,to_procure_qty)
                                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), plan_id, l["material_code"], l["material_name"], l["unit"], l["required_qty"],
                                     l["on_hand_qty"], l["to_procure_qty"]))
            return len(lines)
        except Exception as e:
            logger.error("explode_rm_plan: %s", e); return -1

    def submit_rm_plan(self, plan_id: str, company_id: str, actor: str) -> dict:
        """Submit to Property Administration: creates Store Requisition rows
        (inventory_requisitions) + one Purchase Requisition (proc_purchase_requisitions)
        when those tables exist, then opens an approval request (raw_material_plan)."""
        res = {"ok": False, "store_reqs": 0, "purchase_req": None, "approval": None, "manual": [], "error": None}
        plan = self.get_rm_plan(plan_id, company_id)
        if not plan or plan["status"] != "draft":
            res["error"] = "Plan not found or already submitted"; return res
        lines = [l for l in plan["lines"] if D(l["to_procure_qty"]) > 0]
        costs = self.material_cost_map(company_id)
        total = sum((D(l["to_procure_qty"]) * costs.get(l["material_code"], Decimal(0)) for l in lines), Decimal(0))
        sr_ids: List[str] = []
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cols = table_columns(cur, "inventory_requisitions")
                    if cols and "id" in cols:
                        item_ids = {}
                        if {"sku", "id"} <= table_columns(cur, "inventory_items"):
                            cur.execute("SELECT id, sku FROM inventory_items WHERE company_id=%s AND sku = ANY(%s)",
                                        (company_id, [l["material_code"] for l in lines]))
                            item_ids = {r["sku"]: r["id"] for r in cur.fetchall()}
                        for l in lines:
                            now = datetime.utcnow().isoformat()
                            rec = {"id": _uid(), "company_id": company_id, "item_id": item_ids.get(l["material_code"], ""),
                                   "item_name": f"{l['material_code']} {l['material_name']}".strip(),
                                   "quantity_needed": float(D(l["to_procure_qty"])), "quantity": float(D(l["to_procure_qty"])),
                                   "current_stock": float(D(l["on_hand_qty"])), "priority": "medium", "status": "pending",
                                   "requested_by": actor, "reason": f"Raw material plan {plan['year']}",
                                   "notes": f"Raw material plan {plan['year']} ({plan_id[:8]})", "date": now[:10],
                                   "requested_at": now, "created_at": now}
                            use = {k: v for k, v in rec.items() if k in cols}
                            if "item_id" in cols and not use.get("item_id"):
                                use["item_id"] = l["material_code"]
                            cur.execute(f"INSERT INTO inventory_requisitions({','.join(use)}) VALUES({','.join(['%s'] * len(use))})",
                                        tuple(use.values()))
                            sr_ids.append(rec["id"])
                    else:
                        res["manual"].append("store requisition")
        except Exception as e:
            logger.warning("submit_rm_plan store requisitions: %s", e); res["manual"].append("store requisition")
        pr_id = None
        try:
            from procurement_data_store import procurement_store
            if hasattr(procurement_store, "create_pr") and lines:
                desc = "\n".join(f"{l['material_code']} {l['material_name']}: {D(l['to_procure_qty'])} {l['unit']}" for l in lines)
                pr = procurement_store.create_pr(company_id, {"department": "Production", "requested_by": actor,
                                                              "title": f"Raw material plan {plan['year']}",
                                                              "description": desc, "total_amount": float(q2(total))})
                pr_id = pr["id"] if pr else None
            if not pr_id:
                res["manual"].append("purchase requisition")
        except Exception as e:
            logger.warning("submit_rm_plan purchase requisition: %s", e); res["manual"].append("purchase requisition")
        approval_id = None
        try:
            from approval_hooks import request_approval
            approval_id = request_approval(company_id, "raw_material_plan", plan_id, f"Raw material plan {plan['year']}",
                                           float(q2(total)), actor, {"lines": len(lines), "purchase_req_id": pr_id})
        except Exception as e:
            logger.debug("rm plan approval: %s", e)
        status = "submitted" if approval_id else "approved"
        self._exec("""UPDATE mfg_raw_material_plans SET status=%s, store_req_ids=%s, purchase_req_id=%s, approval_request_id=%s,
                      submitted_at=NOW(), approved_at=CASE WHEN %s='approved' THEN NOW() ELSE NULL END WHERE id=%s""",
                   (status, ",".join(sr_ids), pr_id, approval_id, status, plan_id))
        res.update(ok=True, store_reqs=len(sr_ids), purchase_req=pr_id, approval=approval_id, status=status)
        return res

    def on_rm_plan_decided(self, plan_id: str, company_id: str, status: str, actor: str, comment: str) -> None:
        new = "approved" if status == "approved" else "draft"
        self._exec("UPDATE mfg_raw_material_plans SET status=%s, approved_by=%s, approved_at=CASE WHEN %s='approved' THEN NOW() END, "
                   "notes=CASE WHEN %s<>'' THEN notes || E'\\n' || %s ELSE notes END WHERE id=%s AND company_id=%s",
                   (new, actor, new, comment or "", f"[{status}] {comment or ''}", plan_id, company_id))

    def approve_rm_plan(self, plan_id: str, company_id: str, actor: str) -> bool:
        return self._exec("UPDATE mfg_raw_material_plans SET status='approved', approved_by=%s, approved_at=NOW() "
                          "WHERE id=%s AND company_id=%s AND status IN ('draft','submitted')", (actor, plan_id, company_id))

    # ── production orders ───────────────────────────────────────
    _ORDER_SELECT = """SELECT o.*, p.code AS product_code, p.name AS product_name, p.size_mm2, p.color, p.product_type,
                         p.std_length_per_roll, pl.name AS plant_name
                       FROM mfg_production_orders o JOIN mfg_products p ON p.id=o.product_id
                       LEFT JOIN mfg_plants pl ON pl.id=o.plant_id"""

    def get_orders(self, company_id: str, status: str = None, order_type: str = None, search: str = None,
                   past_due: bool = False, limit: int = 500) -> List[dict]:
        sql = self._ORDER_SELECT + " WHERE o.company_id=%s"
        params: list = [company_id]
        if status:
            sql += " AND o.status=%s"; params.append(status)
        if order_type:
            sql += " AND o.order_type=%s"; params.append(order_type)
        if search:
            sql += " AND (o.order_no ILIKE %s OR o.source_ref ILIKE %s OR o.customer_name ILIKE %s OR p.code ILIKE %s OR p.name ILIKE %s)"
            params += [f"%{search}%"] * 5
        if past_due:
            sql += " AND o.past_due"
        sql += " ORDER BY o.created_at DESC LIMIT %s"; params.append(limit)
        return self._all(sql, tuple(params))

    def get_order(self, order_id: str, company_id: str) -> Optional[dict]:
        return self._one(self._ORDER_SELECT + " WHERE o.id=%s AND o.company_id=%s", (order_id, company_id))

    def get_order_by_no(self, company_id: str, order_no: str) -> Optional[dict]:
        return self._one(self._ORDER_SELECT + " WHERE o.company_id=%s AND o.order_no=%s", (company_id, _str(order_no)))

    def get_order_by_source(self, company_id: str, source_ref: str) -> List[dict]:
        return self._all(self._ORDER_SELECT + " WHERE o.company_id=%s AND o.source_ref=%s ORDER BY o.created_at",
                         (company_id, _str(source_ref)))

    def order_details(self, order_id: str) -> dict:
        return {
            "operations": self._all("""SELECT op.*, w.name AS work_center_name, m.name AS machine_name FROM mfg_order_operations op
                                       LEFT JOIN mfg_work_centers w ON w.id=op.work_center_id
                                       LEFT JOIN mfg_machines m ON m.id=op.machine_id WHERE op.order_id=%s ORDER BY op.seq""", (order_id,)),
            "materials": self._all("SELECT * FROM mfg_order_materials WHERE order_id=%s ORDER BY material_code", (order_id,)),
            "logs": self._all("""SELECT l.*, m.name AS machine_name, s.name AS shift_name FROM mfg_production_logs l
                                 LEFT JOIN mfg_machines m ON m.id=l.machine_id LEFT JOIN mfg_shifts s ON s.id=l.shift_id
                                 WHERE l.order_id=%s ORDER BY l.log_date DESC, l.hour_slot DESC NULLS LAST, l.created_at DESC""", (order_id,)),
            "issues": self._all("SELECT * FROM mfg_material_issues WHERE order_id=%s ORDER BY created_at DESC", (order_id,)),
            "labor": self._all("""SELECT l.*, w.name AS work_center_name FROM mfg_labor_logs l
                                  LEFT JOIN mfg_work_centers w ON w.id=l.work_center_id WHERE l.order_id=%s ORDER BY l.date DESC""", (order_id,)),
            "downtime": self._all("""SELECT d.*, m.name AS machine_name, c.name AS category_name, r.name AS reason_name
                                     FROM mfg_downtime_logs d LEFT JOIN mfg_machines m ON m.id=d.machine_id
                                     LEFT JOIN mfg_downtime_categories c ON c.id=d.category_id
                                     LEFT JOIN mfg_downtime_reasons r ON r.id=d.reason_id WHERE d.order_id=%s ORDER BY d.started_at DESC""", (order_id,)),
            "transfers": self._all("SELECT * FROM mfg_fg_transfers WHERE order_id=%s ORDER BY transferred_at DESC", (order_id,)),
            "events": self._all("SELECT * FROM mfg_order_events WHERE order_id=%s ORDER BY created_at DESC", (order_id,)),
        }

    def add_event(self, order_id: str, event_type: str, note: str, actor: str) -> None:
        self._exec("INSERT INTO mfg_order_events(id,order_id,event_type,note,actor) VALUES(%s,%s,%s,%s,%s)",
                   (_uid(), order_id, event_type, _str(note), actor))

    def create_order(self, company_id: str, data: dict, actor: str) -> Optional[dict]:
        """Create a planned order with a gapless MO number; TDS/BOM/routing default to the active ones."""
        product = self.get_product(_str(data.get("product_id")), company_id)
        if not product or D(data.get("qty_ordered")) <= 0:
            return None
        tds = self.approved_tds_for(product["id"])
        bom = self.active_bom_for(product["id"])
        routing = self.active_routing_for(product["id"])
        settings = self.get_settings(company_id)
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    year = date.today().year
                    seq = self.next_sequence(cur, company_id, f"MO-{year}")
                    oid = _uid()
                    cur.execute("""INSERT INTO mfg_production_orders(id,company_id,order_no,product_id,tds_id,bom_id,routing_id,plant_id,
                                   order_type,source_ref,customer_name,qty_ordered,unit,cutting_length,packing,delivery_date,priority,
                                   status,planned_start,planned_end,prepared_by,notes)
                                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s,%s)""",
                                (oid, company_id, format_order_no(year, seq), product["id"],
                                 _opt(data.get("tds_id")) or (tds["id"] if tds else None),
                                 _opt(data.get("bom_id")) or (bom["id"] if bom else None),
                                 _opt(data.get("routing_id")) or (routing["id"] if routing else None),
                                 _opt(data.get("plant_id")) or settings.get("default_plant_id"),
                                 data.get("order_type") if data.get("order_type") in ORDER_TYPES else "make_to_stock",
                                 _str(data.get("source_ref")), _str(data.get("customer_name")), D(data.get("qty_ordered")),
                                 _str(data.get("unit")) or product.get("unit") or "m", _opt(data.get("cutting_length")),
                                 _str(data.get("packing")), _opt(data.get("delivery_date")), _str(data.get("priority")) or "normal",
                                 _opt(data.get("planned_start")), _opt(data.get("planned_end")), actor or _str(data.get("prepared_by")),
                                 _str(data.get("notes"))))
        except Exception as e:
            logger.error("create_order: %s", e); return None
        self.recompute_planned_cost(oid, company_id)
        self.add_event(oid, "created", "Production order planned", actor)
        return self.get_order(oid, company_id)

    def update_order(self, order_id: str, company_id: str, data: dict) -> bool:
        ok = self._exec("""UPDATE mfg_production_orders SET tds_id=%s,bom_id=%s,routing_id=%s,plant_id=%s,order_type=%s,source_ref=%s,
                           customer_name=%s,qty_ordered=%s,unit=%s,cutting_length=%s,packing=%s,delivery_date=%s,priority=%s,
                           planned_start=%s,planned_end=%s,checked_by=%s,notes=%s,updated_at=NOW()
                           WHERE id=%s AND company_id=%s AND status IN ('planned','released')""",
                        (_opt(data.get("tds_id")), _opt(data.get("bom_id")), _opt(data.get("routing_id")), _opt(data.get("plant_id")),
                         data.get("order_type") if data.get("order_type") in ORDER_TYPES else "make_to_stock",
                         _str(data.get("source_ref")), _str(data.get("customer_name")), D(data.get("qty_ordered")),
                         _str(data.get("unit")) or "m", _opt(data.get("cutting_length")), _str(data.get("packing")),
                         _opt(data.get("delivery_date")), _str(data.get("priority")) or "normal", _opt(data.get("planned_start")),
                         _opt(data.get("planned_end")), _str(data.get("checked_by")), _str(data.get("notes")), order_id, company_id))
        if ok:
            self.recompute_planned_cost(order_id, company_id)
        return ok

    def recompute_planned_cost(self, order_id: str, company_id: str) -> Decimal:
        o = self.get_order(order_id, company_id)
        if not o:
            return Decimal(0)
        bom = self.get_bom(o["bom_id"]) if o.get("bom_id") else None
        routing = self.get_routing(o["routing_id"]) if o.get("routing_id") else None
        cost = planned_material_cost(bom, o["qty_ordered"], self.material_cost_map(company_id)) + \
            planned_conversion_cost(routing, o["qty_ordered"], self.wc_rates(company_id))
        self._exec("UPDATE mfg_production_orders SET planned_cost=%s WHERE id=%s", (cost, order_id))
        return cost

    def recompute_actual_cost(self, order_id: str, company_id: str) -> Decimal:
        """Consumption × std cost + labour hours × work-centre rate + summed output/scrap quantities."""
        costs = self.material_cost_map(company_id)
        rates = self.wc_rates(company_id)
        mat = sum((D(r["qty"]) * (D(r["unit_cost"]) or costs.get(r["material_code"], Decimal(0)))
                   for r in self._all("SELECT material_code, qty, unit_cost FROM mfg_material_issues WHERE order_id=%s AND kind='consumption'", (order_id,))),
                  Decimal(0))
        lab = sum((D(r["hours"]) * rates.get(r["work_center_id"], Decimal(0))
                   for r in self._all("SELECT work_center_id, hours FROM mfg_labor_logs WHERE order_id=%s", (order_id,))), Decimal(0))
        agg = self._one("SELECT COALESCE(SUM(output_qty),0) AS out, COALESCE(SUM(scrap_qty),0) AS scrap FROM mfg_production_logs WHERE order_id=%s",
                        (order_id,)) or {"out": 0, "scrap": 0}
        total = q2(mat + lab)
        self._exec("UPDATE mfg_production_orders SET actual_cost=%s, qty_produced=%s, qty_scrap=%s, updated_at=NOW() WHERE id=%s",
                   (total, D(agg["out"]), D(agg["scrap"]), order_id))
        return total

    def quality_gate(self, company_id: str, material_codes: List[str]) -> Tuple[bool, str]:
        """Incoming-inspection gate: blocked when quality_rm_inspections flags a material as
        rejected. The table belongs to the Quality module; when absent the gate is skipped."""
        codes = [c for c in material_codes if c]
        if not codes:
            return True, ""
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cols = table_columns(cur, "quality_rm_inspections")
                    if not cols:
                        return True, "quality module not installed — gate skipped"
                    code_col = next((c for c in ("material_code", "item_code", "sku", "material") if c in cols), None)
                    status_col = next((c for c in ("result", "status", "decision") if c in cols), None)
                    if not code_col or not status_col:
                        return True, ""
                    where = f"{code_col} = ANY(%s) AND LOWER({status_col}) IN ('rejected','reject','fail','failed')"
                    params: list = [codes]
                    if "company_id" in cols:
                        where += " AND company_id=%s"; params.append(company_id)
                    cur.execute(f"SELECT DISTINCT {code_col} AS code FROM quality_rm_inspections WHERE {where}", tuple(params))
                    bad = [r["code"] for r in cur.fetchall()]
                    if bad:
                        return False, "rejected by incoming inspection: " + ", ".join(bad)
        except Exception as e:
            logger.debug("quality_gate: %s", e)
        return True, ""

    def request_release(self, order_id: str, company_id: str, actor: str) -> dict:
        """Release step: quality gate → approval engine (if a workflow matches) → release."""
        o = self.get_order(order_id, company_id)
        if not o or o["status"] != "planned":
            return {"ok": False, "error": "Order is not in planned status"}
        bom = self.get_bom(o["bom_id"]) if o.get("bom_id") else None
        ok, why = self.quality_gate(company_id, [l["material_code"] for l in (bom or {}).get("lines", [])])
        if not ok:
            return {"ok": False, "error": f"Release blocked — {why}"}
        try:
            from approval_hooks import request_approval
            rid = request_approval(company_id, "production_order", order_id, f"Release {o['order_no']} — {o['product_name']}",
                                   float(D(o["planned_cost"])), actor,
                                   {"order_no": o["order_no"], "qty": float(D(o["qty_ordered"])), "source_ref": o["source_ref"]})
        except Exception as e:
            logger.debug("release approval: %s", e); rid = None
        if rid:
            self._exec("UPDATE mfg_production_orders SET approval_request_id=%s, approval_status='pending', updated_at=NOW() WHERE id=%s",
                       (rid, order_id))
            self.add_event(order_id, "approval_requested", "Release sent for approval", actor)
            return {"ok": True, "pending": True}
        return self.release_order(order_id, company_id, actor)

    def on_release_decided(self, order_id: str, company_id: str, status: str, actor: str, comment: str) -> None:
        if status == "approved":
            self._exec("UPDATE mfg_production_orders SET approval_status='approved', approved_by=%s WHERE id=%s", (actor, order_id))
            self.release_order(order_id, company_id, actor)
        else:
            self._exec("UPDATE mfg_production_orders SET approval_status='rejected' WHERE id=%s", (order_id,))
            self.add_event(order_id, "approval_rejected", comment or "Release rejected", actor)

    def release_order(self, order_id: str, company_id: str, actor: str) -> dict:
        """Planned → released: generate operations from the routing and materials from the BOM."""
        o = self.get_order(order_id, company_id)
        if not o or o["status"] != "planned":
            return {"ok": False, "error": "Order is not in planned status"}
        bom = self.get_bom(o["bom_id"]) if o.get("bom_id") else None
        routing = self.get_routing(o["routing_id"]) if o.get("routing_id") else None
        qty = D(o["qty_ordered"])
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM mfg_order_operations WHERE order_id=%s", (order_id,))
                    cur.execute("DELETE FROM mfg_order_materials WHERE order_id=%s", (order_id,))
                    for op in (routing or {}).get("ops", []):
                        cur.execute("""INSERT INTO mfg_order_operations(id,order_id,seq,work_center_id,operation_name,process_params,planned_qty)
                                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), order_id, op["seq"], op.get("work_center_id"), op["operation_name"],
                                     _json(op.get("process_params") or {}), qty))
                    out_qty = D((bom or {}).get("output_qty")) or Decimal(1)
                    for l in (bom or {}).get("lines", []):
                        per = D(l["qty_per_output"]) * (1 + D(l["scrap_pct"]) / 100)
                        cur.execute("""INSERT INTO mfg_order_materials(id,order_id,material_code,material_name,unit,planned_qty)
                                       VALUES(%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), order_id, l["material_code"], l["material_name"], l["unit"],
                                     (qty / out_qty * per).quantize(Decimal("0.0001"))))
                    cur.execute("""UPDATE mfg_production_orders SET status='released', released_at=NOW(), approved_by=COALESCE(NULLIF(approved_by,''),%s),
                                   approval_status=CASE WHEN approval_status='pending' THEN 'approved' ELSE approval_status END, updated_at=NOW()
                                   WHERE id=%s""", (actor, order_id))
        except Exception as e:
            logger.error("release_order: %s", e); return {"ok": False, "error": str(e)}
        self.add_event(order_id, "released", "Released to production", actor)
        return {"ok": True, "pending": False}

    def start_order(self, order_id: str, company_id: str, actor: str) -> bool:
        ok = self._exec("""UPDATE mfg_production_orders SET status='in_progress', actual_start=COALESCE(actual_start, NOW()), updated_at=NOW()
                           WHERE id=%s AND company_id=%s AND status='released'""", (order_id, company_id))
        if ok:
            self.add_event(order_id, "started", "Production started", actor)
        return ok

    def update_operation(self, op_id: str, order_id: str, data: dict, params: dict) -> bool:
        status = data.get("status") if data.get("status") in ("pending", "running", "done") else "pending"
        return self._exec("""UPDATE mfg_order_operations SET machine_id=%s, process_params=%s, status=%s,
                             started_at=CASE WHEN %s IN ('running','done') THEN COALESCE(started_at, NOW()) ELSE started_at END,
                             finished_at=CASE WHEN %s='done' THEN COALESCE(finished_at, NOW()) ELSE NULL END
                             WHERE id=%s AND order_id=%s""",
                          (_opt(data.get("machine_id")), _json(params), status, status, status, op_id, order_id))

    def confirm_order(self, order_id: str, company_id: str, data: dict, actor: str) -> dict:
        """Confirmation posts finished-goods output: FG transfer record, inventory movement
        (when linked) and the Dr FG / Cr WIP journal. GL failure never blocks."""
        o = self.get_order(order_id, company_id)
        if not o or o["status"] not in ("released", "in_progress"):
            return {"ok": False, "error": "Order must be released or in progress"}
        self.recompute_actual_cost(order_id, company_id)
        o = self.get_order(order_id, company_id)
        agg = self._one("""SELECT COALESCE(SUM(output_rolls),0) AS rolls, COALESCE(SUM(under_length_rolls),0) AS ul,
                           COALESCE(SUM(length_m),0) AS length_m, COALESCE(SUM(weight_kg),0) AS kg FROM mfg_production_logs WHERE order_id=%s""",
                        (order_id,)) or {}
        qty = D(data.get("qty")) if D(data.get("qty")) > 0 else D(o["qty_produced"])
        ref_no = _str(data.get("ref_no")) or f"FG-{o['order_no']}"
        tid = _uid()
        moved = self._inventory_receipt(company_id, o, qty, ref_no, actor)
        self._exec("""INSERT INTO mfg_fg_transfers(id,company_id,order_id,product_id,qty,rolls,under_length_rolls,length_m,weight_kg,
                      from_store,to_store,ref_no,transferred_by,inventory_movement_ok) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                   (tid, company_id, order_id, o["product_id"], qty, _int(data.get("rolls"), int(agg.get("rolls") or 0)),
                    _int(data.get("under_length_rolls"), int(agg.get("ul") or 0)), D(data.get("length_m")) or D(agg.get("length_m")),
                    D(data.get("weight_kg")) or D(agg.get("kg")), _str(data.get("from_store")) or "Property Administration",
                    _str(data.get("to_store")) or "Market Finished Goods Store", ref_no, actor, moved))
        s = self.get_settings(company_id)
        gl_status, entry = "skipped", None
        if D(o["actual_cost"]) > 0 and s.get("gl_finished_goods_account") and s.get("gl_wip_account"):
            entry = self.post_journal(company_id, f"FG output {o['order_no']} — {o['product_name']}", ref_no,
                                      [(s["gl_finished_goods_account"], D(o["actual_cost"]), Decimal(0)),
                                       (s["gl_wip_account"], Decimal(0), D(o["actual_cost"]))], actor)
            gl_status = "posted" if entry else "failed"
        self._exec("""UPDATE mfg_production_orders SET status='confirmed', confirmed_at=NOW(), actual_end=COALESCE(actual_end, NOW()),
                      qty_produced=%s, gl_status=%s, gl_entry_id=%s, checked_by=COALESCE(NULLIF(checked_by,''),%s), updated_at=NOW() WHERE id=%s""",
                   (qty, gl_status, entry, actor, order_id))
        self.add_event(order_id, "confirmed", f"Confirmed {qty} {o['unit']} → {ref_no} (GL: {gl_status})", actor)
        return {"ok": True, "gl_status": gl_status, "inventory": moved, "transfer_id": tid}

    def _inventory_receipt(self, company_id: str, o: dict, qty: Decimal, ref: str, actor: str) -> bool:
        """inventory_movements 'in' + stock increase when the product is linked to an inventory item."""
        item_id = o.get("inventory_item_id") or (self.get_product(o["product_id"], company_id) or {}).get("inventory_item_id")
        if not item_id or qty <= 0:
            return False
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cols = table_columns(cur, "inventory_movements")
                    if not {"id", "item_id", "quantity", "movement_type"} <= cols:
                        return False
                    now = datetime.utcnow().isoformat()
                    rec = {"id": _uid(), "company_id": company_id, "item_id": item_id, "item_name": o.get("product_name") or "",
                           "movement_type": "in", "quantity": float(qty), "unit_cost": 0, "total_cost": 0,
                           "from_location": "Production", "to_location": "Finished Goods Store", "reference_number": ref,
                           "reason": f"Production order {o['order_no']}", "approved_by": actor, "approval_status": "approved",
                           "date": now[:10], "created_at": now}
                    use = {k: v for k, v in rec.items() if k in cols}
                    cur.execute(f"INSERT INTO inventory_movements({','.join(use)}) VALUES({','.join(['%s'] * len(use))})", tuple(use.values()))
                    if "current_stock" in table_columns(cur, "inventory_items"):
                        cur.execute("UPDATE inventory_items SET current_stock=current_stock+%s WHERE id=%s", (float(qty), item_id))
            return True
        except Exception as e:
            logger.warning("inventory receipt for %s failed: %s", o.get("order_no"), e); return False

    def close_order(self, order_id: str, company_id: str, actor: str) -> bool:
        self.recompute_actual_cost(order_id, company_id)
        ok = self._exec("""UPDATE mfg_production_orders SET status='closed', closed_at=NOW(), updated_at=NOW()
                           WHERE id=%s AND company_id=%s AND status='confirmed'""", (order_id, company_id))
        if ok:
            self.add_event(order_id, "closed", "Order closed", actor)
        return ok

    def cancel_order(self, order_id: str, company_id: str, actor: str, reason: str = "") -> bool:
        ok = self._exec("""UPDATE mfg_production_orders SET status='cancelled', updated_at=NOW()
                           WHERE id=%s AND company_id=%s AND status IN ('planned','released')""", (order_id, company_id))
        if ok:
            self.add_event(order_id, "cancelled", reason or "Order cancelled", actor)
        return ok

    def flag_past_due(self, company_id: str = None) -> int:
        """Weekly job: mark open orders whose delivery/planned end has passed."""
        sql = """UPDATE mfg_production_orders SET past_due = (COALESCE(delivery_date, planned_end) < CURRENT_DATE)
                 WHERE status IN ('planned','released','in_progress')"""
        params: tuple = ()
        if company_id:
            sql += " AND company_id=%s"; params = (company_id,)
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.rowcount
        except Exception as e:
            logger.error("flag_past_due: %s", e); return 0

    # ── shop-floor logging ──────────────────────────────────────
    def add_production_log(self, company_id: str, order_id: str, data: dict) -> bool:
        ok = self._exec("""INSERT INTO mfg_production_logs(id,company_id,order_id,operation_id,machine_id,shift_id,log_date,hour_slot,
                           input_qty,input_unit,output_qty,output_unit,output_rolls,under_length_rolls,length_m,weight_kg,scrap_qty,scrap_unit,
                           scrap_type_id,rework_qty,lot_no,drum_no,operator,remarks)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (_uid(), company_id, order_id, _opt(data.get("operation_id")), _opt(data.get("machine_id")),
                         _opt(data.get("shift_id")), _opt(data.get("log_date")) or date.today(), _opt(data.get("hour_slot")),
                         _num(data.get("input_qty")), _str(data.get("input_unit")) or "kg", _num(data.get("output_qty")),
                         _str(data.get("output_unit")) or "kg", _int(data.get("output_rolls")), _int(data.get("under_length_rolls")),
                         _num(data.get("length_m")), _num(data.get("weight_kg")), _num(data.get("scrap_qty")),
                         _str(data.get("scrap_unit")) or "kg", _opt(data.get("scrap_type_id")), _num(data.get("rework_qty")),
                         _str(data.get("lot_no")), _str(data.get("drum_no")), _str(data.get("operator")), _str(data.get("remarks"))))
        if ok:
            self._exec("""UPDATE mfg_production_orders SET status='in_progress', actual_start=COALESCE(actual_start, NOW()), updated_at=NOW()
                          WHERE id=%s AND company_id=%s AND status='released'""", (order_id, company_id))
            self.recompute_actual_cost(order_id, company_id)
        return ok

    def get_production_logs(self, company_id: str, f: dict) -> List[dict]:
        sql = """SELECT l.*, o.order_no, p.code AS product_code, p.name AS product_name, p.size_mm2, m.name AS machine_name,
                   s.name AS shift_name, st.name AS scrap_type_name
                 FROM mfg_production_logs l JOIN mfg_production_orders o ON o.id=l.order_id JOIN mfg_products p ON p.id=o.product_id
                 LEFT JOIN mfg_machines m ON m.id=l.machine_id LEFT JOIN mfg_shifts s ON s.id=l.shift_id
                 LEFT JOIN mfg_scrap_types st ON st.id=l.scrap_type_id WHERE l.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="l.log_date", machine_col="l.machine_id",
                                          wc_col="m.work_center_id", plant_col="o.plant_id")
        return self._all(sql + " ORDER BY l.log_date DESC, l.created_at DESC LIMIT 1000", tuple(params))

    def add_material_issue(self, company_id: str, order_id: str, data: dict, actor: str) -> dict:
        """issue / return / consumption; consumption posts Dr WIP / Cr Raw material."""
        kind = data.get("kind") if data.get("kind") in ISSUE_KINDS else "issue"
        qty = D(data.get("qty"))
        code = _str(data.get("material_code"))
        if qty <= 0 or not code:
            return {"ok": False, "error": "Material code and a positive quantity are required"}
        costs = self.material_cost_map(company_id)
        unit_cost = D(data.get("unit_cost")) or costs.get(code, Decimal(0))
        iid = _uid()
        ok = self._exec("""INSERT INTO mfg_material_issues(id,company_id,order_id,material_code,material_name,qty,unit,kind,lot_no,store_ref,
                           drum_ref,unit_cost,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (iid, company_id, order_id, code, _str(data.get("material_name")), qty, _str(data.get("unit")) or "kg", kind,
                         _str(data.get("lot_no")), _str(data.get("store_ref")), _str(data.get("drum_ref")), unit_cost, actor))
        if not ok:
            return {"ok": False, "error": "Could not record material movement"}
        col = {"issue": "issued_qty", "return": "returned_qty", "consumption": "consumed_qty"}[kind]
        # upsert the order material line so unplanned materials show up too
        exists = self._one("SELECT id FROM mfg_order_materials WHERE order_id=%s AND material_code=%s", (order_id, code))
        if not exists:
            self._exec("INSERT INTO mfg_order_materials(id,order_id,material_code,material_name,unit,planned_qty) VALUES(%s,%s,%s,%s,%s,0)",
                       (_uid(), order_id, code, _str(data.get("material_name")), _str(data.get("unit")) or "kg"))
        self._exec(f"UPDATE mfg_order_materials SET {col}={col}+%s, lot_no=CASE WHEN %s<>'' THEN %s ELSE lot_no END "
                   "WHERE order_id=%s AND material_code=%s", (qty, _str(data.get("lot_no")), _str(data.get("lot_no")), order_id, code))
        gl_status = ""
        if kind == "consumption":
            s = self.get_settings(company_id)
            amount = q2(qty * unit_cost)
            if amount > 0 and s.get("gl_wip_account") and s.get("gl_raw_material_account"):
                o = self.get_order(order_id, company_id) or {}
                entry = self.post_journal(company_id, f"RM consumption {o.get('order_no', '')} — {code}", o.get("order_no", ""),
                                          [(s["gl_wip_account"], amount, Decimal(0)), (s["gl_raw_material_account"], Decimal(0), amount)], actor)
                gl_status = "posted" if entry else "failed"
                self._exec("UPDATE mfg_material_issues SET gl_status=%s, gl_entry_id=%s WHERE id=%s", (gl_status, entry, iid))
            else:
                gl_status = "skipped"
                self._exec("UPDATE mfg_material_issues SET gl_status=%s WHERE id=%s", (gl_status, iid))
            self.recompute_actual_cost(order_id, company_id)
        return {"ok": True, "gl_status": gl_status}

    def add_downtime(self, company_id: str, data: dict, actor: str) -> bool:
        started, ended = _opt(data.get("started_at")), _opt(data.get("ended_at"))
        minutes = D(data.get("minutes"))
        if minutes <= 0 and started and ended:
            try:
                minutes = D((datetime.fromisoformat(str(ended)) - datetime.fromisoformat(str(started))).total_seconds() / 60)
            except Exception:
                minutes = Decimal(0)
        machine = self.get_machine(_str(data.get("machine_id")), company_id) if _opt(data.get("machine_id")) else None
        wc = _opt(data.get("work_center_id")) or (machine or {}).get("work_center_id")
        return self._exec("""INSERT INTO mfg_downtime_logs(id,company_id,machine_id,work_center_id,order_id,shift_id,started_at,ended_at,minutes,
                             category_id,reason_id,description,reported_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), company_id, _opt(data.get("machine_id")), wc, _opt(data.get("order_id")), _opt(data.get("shift_id")),
                           started, ended, minutes.quantize(Decimal("0.01")), _opt(data.get("category_id")), _opt(data.get("reason_id")),
                           _str(data.get("description")), actor))

    def get_downtime(self, company_id: str, f: dict) -> List[dict]:
        sql = """SELECT d.*, m.name AS machine_name, w.name AS work_center_name, c.name AS category_name, r.name AS reason_name,
                   o.order_no FROM mfg_downtime_logs d LEFT JOIN mfg_machines m ON m.id=d.machine_id
                 LEFT JOIN mfg_work_centers w ON w.id=d.work_center_id LEFT JOIN mfg_downtime_categories c ON c.id=d.category_id
                 LEFT JOIN mfg_downtime_reasons r ON r.id=d.reason_id LEFT JOIN mfg_production_orders o ON o.id=d.order_id
                 WHERE d.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="d.started_at::date", machine_col="d.machine_id",
                                          wc_col="d.work_center_id", plant_col="w.plant_id")
        return self._all(sql + " ORDER BY d.started_at DESC NULLS LAST LIMIT 1000", tuple(params))

    def add_labor_log(self, company_id: str, data: dict) -> bool:
        return self._exec("""INSERT INTO mfg_labor_logs(id,company_id,order_id,work_center_id,shift_id,date,workers,hours,setup_hours,run_hours)
                             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (_uid(), company_id, _opt(data.get("order_id")), _opt(data.get("work_center_id")), _opt(data.get("shift_id")),
                           _opt(data.get("date")) or date.today(), _int(data.get("workers")), _num(data.get("hours")),
                           _num(data.get("setup_hours")), _num(data.get("run_hours"))))

    # ── GL ──────────────────────────────────────────────────────
    def post_journal(self, company_id: str, description: str, ref: str, lines: List[Tuple[str, Decimal, Decimal]],
                     actor: str) -> Optional[str]:
        """Write a balanced entry to journal_entries/journal_entry_lines (init_db.sql shape).
        Returns the entry id or None; never raises."""
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    je, jl = table_columns(cur, "journal_entries"), table_columns(cur, "journal_entry_lines")
                    if not {"entry_id", "company_id", "entry_date", "description", "total_debit", "total_credit"} <= je \
                            or not {"line_id", "entry_id", "account_code", "debit_amount", "credit_amount"} <= jl:
                        return None
                    total = sum((d for _, d, _ in lines), Decimal(0))
                    if total != sum((c for _, _, c in lines), Decimal(0)):
                        return None
                    eid = _uid()
                    today = date.today().isoformat()
                    cur.execute("""INSERT INTO journal_entries(entry_id,company_id,entry_date,description,reference_number,total_debit,
                                   total_credit,created_by,created_date,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'posted')""",
                                (eid, company_id, today, description[:250], (ref or "")[:100], float(total), float(total), actor or "system", today))
                    for n, (acct, dr, cr) in enumerate(lines, 1):
                        cur.execute("SELECT account_name FROM chart_of_accounts WHERE company_id=%s AND account_code=%s LIMIT 1", (company_id, acct))
                        row = cur.fetchone()
                        cur.execute("""INSERT INTO journal_entry_lines(line_id,entry_id,account_code,account_name,description,debit_amount,
                                       credit_amount,line_number,created_date) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (_uid(), eid, acct, (row or {}).get("account_name", ""), description[:250], float(dr), float(cr), n, today))
                    return eid
        except Exception as e:
            logger.error("mfg post_journal: %s", e); return None

    # ── dashboard ───────────────────────────────────────────────
    def dashboard(self, company_id: str) -> dict:
        d = {"by_status": {s: 0 for s in ORDER_STATUSES}, "past_due": 0, "open_orders": [], "missing_bom": [],
             "products": 0, "machines": 0, "machines_down": 0, "today_output": Decimal(0), "today_scrap": Decimal(0),
             "month_output": Decimal(0), "month_yield": Decimal(0), "pending_release": 0, "recent_logs": [], "kpis": []}
        for r in self._all("SELECT status, COUNT(*) AS c FROM mfg_production_orders WHERE company_id=%s GROUP BY status", (company_id,)):
            d["by_status"][r["status"]] = r["c"]
        row = self._one("SELECT COUNT(*) AS c FROM mfg_production_orders WHERE company_id=%s AND past_due AND status NOT IN ('confirmed','closed','cancelled')", (company_id,))
        d["past_due"] = (row or {}).get("c", 0)
        row = self._one("SELECT COUNT(*) AS c FROM mfg_production_orders WHERE company_id=%s AND approval_status='pending'", (company_id,))
        d["pending_release"] = (row or {}).get("c", 0)
        d["open_orders"] = self._all(self._ORDER_SELECT + " WHERE o.company_id=%s AND o.status IN ('planned','released','in_progress') "
                                     "ORDER BY o.delivery_date NULLS LAST, o.created_at LIMIT 10", (company_id,))
        d["missing_bom"] = self.products_missing_bom(company_id)
        row = self._one("SELECT COUNT(*) AS c FROM mfg_products WHERE company_id=%s AND is_active", (company_id,)); d["products"] = (row or {}).get("c", 0)
        row = self._one("SELECT COUNT(*) AS c, COUNT(*) FILTER (WHERE status IN ('down','maintenance')) AS dn FROM mfg_machines WHERE company_id=%s AND status<>'retired'", (company_id,))
        d["machines"], d["machines_down"] = (row or {}).get("c", 0), (row or {}).get("dn", 0)
        row = self._one("SELECT COALESCE(SUM(output_qty),0) AS o, COALESCE(SUM(scrap_qty),0) AS s FROM mfg_production_logs WHERE company_id=%s AND log_date=CURRENT_DATE", (company_id,))
        d["today_output"], d["today_scrap"] = D((row or {}).get("o")), D((row or {}).get("s"))
        row = self._one("SELECT COALESCE(SUM(output_qty),0) AS o, COALESCE(SUM(input_qty),0) AS i FROM mfg_production_logs WHERE company_id=%s AND date_trunc('month', log_date)=date_trunc('month', CURRENT_DATE)", (company_id,))
        d["month_output"], d["month_yield"] = D((row or {}).get("o")), yield_pct((row or {}).get("o"), (row or {}).get("i"))
        d["recent_logs"] = self.get_production_logs(company_id, {})[:8]
        d["kpis"] = self._all("""SELECT k.*, m.name AS machine_name, w.name AS work_center_name FROM mfg_daily_kpis k
                                 LEFT JOIN mfg_machines m ON m.id=k.machine_id LEFT JOIN mfg_work_centers w ON w.id=k.work_center_id
                                 WHERE k.company_id=%s AND k.kpi_date >= CURRENT_DATE - 7 ORDER BY k.kpi_date DESC, w.name LIMIT 20""", (company_id,))
        return d

    def companies(self) -> List[str]:
        return [r["company_id"] for r in self._all("SELECT DISTINCT company_id FROM mfg_production_orders UNION SELECT DISTINCT company_id FROM mfg_machines")]

    # ── KPIs (daily job) ────────────────────────────────────────
    def compute_daily_kpis(self, company_id: str, day: date) -> int:
        shifts = self.get_shifts(company_id, active_only=True)
        blocked = set(self.non_working_days(company_id, day, day))
        machines = self.get_machines(company_id)
        logs = self._all("""SELECT machine_id, SUM(input_qty) AS i, SUM(output_qty) AS o, SUM(scrap_qty) AS s
                            FROM mfg_production_logs WHERE company_id=%s AND log_date=%s GROUP BY machine_id""", (company_id, day))
        dts = {r["machine_id"]: D(r["m"]) for r in self._all(
            "SELECT machine_id, SUM(minutes) AS m FROM mfg_downtime_logs WHERE company_id=%s AND started_at::date=%s GROUP BY machine_id", (company_id, day))}
        labor = {r["work_center_id"]: D(r["h"]) for r in self._all(
            "SELECT work_center_id, SUM(run_hours) AS h FROM mfg_labor_logs WHERE company_id=%s AND date=%s GROUP BY work_center_id", (company_id, day))}
        by_machine = {r["machine_id"]: r for r in logs}
        n = 0
        for m in machines:
            if m["status"] == "retired":
                continue
            lg = by_machine.get(m["id"], {})
            if not lg and not dts.get(m["id"]):
                continue
            avail = Decimal(0) if day in blocked else available_hours(day, day, [], shifts, m.get("work_center_id"))
            k = kpi_row(lg.get("i"), lg.get("o"), lg.get("s"), avail, dts.get(m["id"], 0), labor.get(m.get("work_center_id"), 0))
            ok = self._exec("""INSERT INTO mfg_daily_kpis(id,company_id,kpi_date,work_center_id,machine_id,input_qty,output_qty,scrap_qty,
                               available_hours,run_hours,downtime_minutes,yield_pct,utilisation_pct,computed_at)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                               ON CONFLICT (company_id,kpi_date,work_center_id,machine_id) DO UPDATE SET input_qty=EXCLUDED.input_qty,
                               output_qty=EXCLUDED.output_qty, scrap_qty=EXCLUDED.scrap_qty, available_hours=EXCLUDED.available_hours,
                               run_hours=EXCLUDED.run_hours, downtime_minutes=EXCLUDED.downtime_minutes, yield_pct=EXCLUDED.yield_pct,
                               utilisation_pct=EXCLUDED.utilisation_pct, computed_at=NOW()""",
                            (_uid(), company_id, day, m.get("work_center_id") or "", m["id"], k["input_qty"], k["output_qty"], k["scrap_qty"],
                             k["available_hours"], k["run_hours"], k["downtime_minutes"], k["yield_pct"], k["utilisation_pct"]))
            n += 1 if ok else 0
        return n

    # ── reports ─────────────────────────────────────────────────
    @staticmethod
    def _apply_filters(sql: str, params: list, f: dict, *, date_col: str, machine_col: str = None, wc_col: str = None,
                       plant_col: str = None) -> Tuple[str, list]:
        if f.get("date_from"):
            sql += f" AND {date_col} >= %s"; params.append(f["date_from"])
        if f.get("date_to"):
            sql += f" AND {date_col} <= %s"; params.append(f["date_to"])
        if f.get("machine_id") and machine_col:
            sql += f" AND {machine_col} = %s"; params.append(f["machine_id"])
        if f.get("work_center_id") and wc_col:
            sql += f" AND {wc_col} = %s"; params.append(f["work_center_id"])
        if f.get("plant_id") and plant_col:
            sql += f" AND {plant_col} = %s"; params.append(f["plant_id"])
        return sql, params

    def report_machine_performance(self, company_id: str, f: dict) -> List[dict]:
        sql = """SELECT m.code AS machine_code, m.name AS machine, o.order_no, p.code AS product_code, p.name AS product, p.size_mm2,
                   COALESCE((t.parameters->>'sheath_diameter_mm'), (t.parameters->>'insulation_diameter_mm'), '') AS diameter,
                   SUM(l.input_qty) AS input_kg, SUM(l.output_qty) AS output_kg, SUM(l.scrap_qty) AS scrap_kg,
                   MAX(o.qty_ordered) AS plan_qty, SUM(l.length_m) AS length_m, MIN(l.log_date) AS from_date, MAX(l.log_date) AS to_date
                 FROM mfg_production_logs l JOIN mfg_production_orders o ON o.id=l.order_id JOIN mfg_products p ON p.id=o.product_id
                 LEFT JOIN mfg_machines m ON m.id=l.machine_id LEFT JOIN mfg_tds t ON t.id=o.tds_id WHERE l.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="l.log_date", machine_col="l.machine_id",
                                          wc_col="m.work_center_id", plant_col="o.plant_id")
        rows = self._all(sql + " GROUP BY m.code, m.name, o.order_no, p.code, p.name, p.size_mm2, t.parameters ORDER BY m.code, o.order_no", tuple(params))
        for r in rows:
            r["ratio_pct"] = yield_pct(r["output_kg"], r["input_kg"])
            r["plan_vs_actual"] = variance(r["plan_qty"], r["output_kg"])[0]
        return rows

    def report_rm_converted(self, company_id: str, f: dict) -> List[dict]:
        sql = """SELECT o.order_no, p.size_mm2, p.name AS description, p.color, SUM(l.output_rolls) AS output_rolls,
                   SUM(l.under_length_rolls) AS under_length_rolls, SUM(l.length_m) AS length_m, SUM(l.weight_kg) AS weight_kg,
                   SUM(l.input_qty) AS input_kg, SUM(l.output_qty) AS output_qty
                 FROM mfg_production_logs l JOIN mfg_production_orders o ON o.id=l.order_id JOIN mfg_products p ON p.id=o.product_id
                 LEFT JOIN mfg_machines m ON m.id=l.machine_id WHERE l.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="l.log_date", machine_col="l.machine_id",
                                          wc_col="m.work_center_id", plant_col="o.plant_id")
        return self._all(sql + " GROUP BY o.order_no, p.size_mm2, p.name, p.color ORDER BY o.order_no", tuple(params))

    def report_fg_delivered(self, company_id: str, f: dict) -> List[dict]:
        sql = """SELECT t.transferred_at, o.order_no, p.size_mm2, p.name AS description, p.color, t.rolls, t.length_m,
                   t.under_length_rolls, t.weight_kg, t.qty, o.unit, t.ref_no, t.from_store, t.to_store, t.transferred_by
                 FROM mfg_fg_transfers t JOIN mfg_production_orders o ON o.id=t.order_id JOIN mfg_products p ON p.id=o.product_id
                 WHERE t.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="t.transferred_at::date", plant_col="o.plant_id")
        return self._all(sql + " ORDER BY t.transferred_at DESC", tuple(params))

    def report_rm_status(self, company_id: str, f: dict) -> Tuple[List[str], List[dict]]:
        sql = """SELECT o.order_no, p.size_mm2, p.name AS product_name, o.status AS order_status, i.material_code,
                   SUM(CASE WHEN i.kind='issue' THEN i.qty ELSE 0 END) AS issued,
                   SUM(CASE WHEN i.kind='consumption' THEN i.qty ELSE 0 END) AS consumed,
                   SUM(CASE WHEN i.kind='return' THEN i.qty ELSE 0 END) AS returned,
                   STRING_AGG(DISTINCT NULLIF(i.drum_ref,''), ', ') AS drum_refs
                 FROM mfg_material_issues i JOIN mfg_production_orders o ON o.id=i.order_id JOIN mfg_products p ON p.id=o.product_id
                 WHERE i.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="i.created_at::date", plant_col="o.plant_id")
        rows = self._all(sql + " GROUP BY o.order_no, p.size_mm2, p.name, o.status, i.material_code ORDER BY o.order_no", tuple(params))
        return pivot_materials(rows, ("order_no",))

    def report_scrap(self, company_id: str, f: dict) -> Tuple[List[str], List[dict]]:
        sql = """SELECT o.order_no, p.size_mm2, p.name AS description, p.color, COALESCE(st.name,'Unclassified') AS scrap_type,
                   SUM(l.scrap_qty) AS scrap_kg
                 FROM mfg_production_logs l JOIN mfg_production_orders o ON o.id=l.order_id JOIN mfg_products p ON p.id=o.product_id
                 LEFT JOIN mfg_scrap_types st ON st.id=l.scrap_type_id LEFT JOIN mfg_machines m ON m.id=l.machine_id
                 WHERE l.company_id=%s AND l.scrap_qty<>0"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="l.log_date", machine_col="l.machine_id",
                                          wc_col="m.work_center_id", plant_col="o.plant_id")
        rows = self._all(sql + " GROUP BY o.order_no, p.size_mm2, p.name, p.color, st.name ORDER BY o.order_no", tuple(params))
        types = sorted({r["scrap_type"] for r in rows})
        out: Dict[str, dict] = {}
        for r in rows:
            row = out.setdefault(r["order_no"], {"order_no": r["order_no"], "size_mm2": r["size_mm2"], "description": r["description"],
                                                 "color": r["color"], "total_kg": Decimal(0)})
            row[r["scrap_type"]] = D(row.get(r["scrap_type"])) + D(r["scrap_kg"])
            row["total_kg"] += D(r["scrap_kg"])
        return types, list(out.values())

    def report_utilisation(self, company_id: str, f: dict, group: str = "day") -> List[dict]:
        """Daily → monthly machine utilisation from mfg_daily_kpis plus downtime by category."""
        trunc = "month" if group == "month" else "day"
        sql = f"""SELECT date_trunc('{trunc}', k.kpi_date)::date AS period, m.code AS machine_code, m.name AS machine, w.name AS work_center,
                    SUM(k.available_hours) AS available_hours, SUM(k.run_hours) AS run_hours, SUM(k.downtime_minutes)/60 AS downtime_hours,
                    SUM(k.output_qty) AS output_qty, SUM(k.input_qty) AS input_qty
                  FROM mfg_daily_kpis k LEFT JOIN mfg_machines m ON m.id=k.machine_id LEFT JOIN mfg_work_centers w ON w.id=k.work_center_id
                  WHERE k.company_id=%s"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="k.kpi_date", machine_col="k.machine_id",
                                          wc_col="k.work_center_id", plant_col="w.plant_id")
        rows = self._all(sql + " GROUP BY 1, m.code, m.name, w.name ORDER BY 1 DESC, m.code", tuple(params))
        cat_sql = f"""SELECT date_trunc('{trunc}', d.started_at)::date AS period, d.machine_id, COALESCE(c.name,'Other') AS category, SUM(d.minutes)/60 AS h
                      FROM mfg_downtime_logs d LEFT JOIN mfg_downtime_categories c ON c.id=d.category_id WHERE d.company_id=%s"""
        cat_sql, cparams = self._apply_filters(cat_sql, [company_id], f, date_col="d.started_at::date", machine_col="d.machine_id", wc_col="d.work_center_id")
        cats = self._all(cat_sql + " GROUP BY 1, d.machine_id, c.name", tuple(cparams))
        mcode = {m["id"]: m["code"] for m in self.get_machines(company_id)}
        by_key: Dict[tuple, dict] = {}
        for c in cats:
            by_key.setdefault((c["period"], mcode.get(c["machine_id"])), {})[c["category"]] = D(c["h"]).quantize(Decimal("0.01"))
        for r in rows:
            r["utilisation_pct"] = utilisation_pct(r["run_hours"], r["available_hours"])
            r["yield_pct"] = yield_pct(r["output_qty"], r["input_qty"])
            r["downtime_by_category"] = by_key.get((r["period"], r["machine_code"]), {})
        return rows

    def report_plan_vs_actual(self, company_id: str, f: dict) -> List[dict]:
        """Per work centre / machine / product: planned (orders) vs actual (logs), efficiency, yield, wastage per shift."""
        sql = """SELECT w.name AS work_center, m.code AS machine_code, p.code AS product_code, p.name AS product, s.name AS shift,
                   SUM(l.input_qty) AS input_qty, SUM(l.output_qty) AS output_qty, SUM(l.scrap_qty) AS scrap_qty,
                   SUM(l.rework_qty) AS rework_qty, COUNT(DISTINCT o.id) AS orders,
                   (SELECT COALESCE(SUM(qty_ordered),0) FROM mfg_production_orders o2 WHERE o2.product_id=p.id AND o2.company_id=%s
                      AND o2.status<>'cancelled' AND (o2.planned_start IS NULL OR %s IS NULL OR o2.planned_start <= %s)
                      AND (o2.planned_end IS NULL OR %s IS NULL OR o2.planned_end >= %s)) AS planned_qty,
                   MAX(w.std_capacity_per_hour) AS std_capacity
                 FROM mfg_production_logs l JOIN mfg_production_orders o ON o.id=l.order_id JOIN mfg_products p ON p.id=o.product_id
                 LEFT JOIN mfg_machines m ON m.id=l.machine_id LEFT JOIN mfg_work_centers w ON w.id=m.work_center_id
                 LEFT JOIN mfg_shifts s ON s.id=l.shift_id WHERE l.company_id=%s"""
        dt_to, dt_from = f.get("date_to") or None, f.get("date_from") or None
        params = [company_id, dt_to, dt_to, dt_from, dt_from, company_id]
        sql, params = self._apply_filters(sql, params, f, date_col="l.log_date", machine_col="l.machine_id",
                                          wc_col="m.work_center_id", plant_col="o.plant_id")
        rows = self._all(sql + " GROUP BY w.name, m.code, p.id, p.code, p.name, s.name ORDER BY w.name, m.code, p.code, s.name", tuple(params))
        for r in rows:
            r["yield_pct"] = yield_pct(r["output_qty"], r["input_qty"])
            r["wastage_pct"] = yield_pct(r["scrap_qty"], r["input_qty"])
            r["efficiency_pct"] = utilisation_pct(r["output_qty"], r["planned_qty"])
        return rows

    def report_cycle(self, company_id: str, f: dict) -> List[dict]:
        sql = self._ORDER_SELECT + " WHERE o.company_id=%s"
        sql, params = self._apply_filters(sql, [company_id], f, date_col="o.created_at::date", plant_col="o.plant_id")
        rows = self._all(sql + " ORDER BY o.created_at DESC LIMIT 1000", tuple(params))
        for r in rows:
            def _days(a, b):
                if not a or not b:
                    return None
                a = a if isinstance(a, datetime) else datetime.combine(a, dtime())
                b = b if isinstance(b, datetime) else datetime.combine(b, dtime())
                return round((b - a).total_seconds() / 86400, 1)
            r["days_to_release"] = _days(r["created_at"], r["released_at"])
            r["days_in_production"] = _days(r["actual_start"] or r["released_at"], r["confirmed_at"])
            r["days_total"] = _days(r["created_at"], r["closed_at"] or r["confirmed_at"])
            r["cost_variance"], r["cost_variance_pct"] = variance(r["planned_cost"], r["actual_cost"])
        return rows

    def report_journals(self, company_id: str, f: dict) -> List[dict]:
        sql = """SELECT i.created_at AS posted_at, o.order_no, 'consumption' AS kind, i.material_code AS item, i.qty, i.unit,
                   i.qty*i.unit_cost AS amount, i.gl_status, i.gl_entry_id FROM mfg_material_issues i
                 JOIN mfg_production_orders o ON o.id=i.order_id WHERE i.company_id=%s AND i.kind='consumption'"""
        sql, params = self._apply_filters(sql, [company_id], f, date_col="i.created_at::date", plant_col="o.plant_id")
        sql2 = """SELECT t.transferred_at AS posted_at, o.order_no, 'output' AS kind, p.code AS item, t.qty, o.unit, o.actual_cost AS amount,
                    o.gl_status, o.gl_entry_id FROM mfg_fg_transfers t JOIN mfg_production_orders o ON o.id=t.order_id
                  JOIN mfg_products p ON p.id=o.product_id WHERE t.company_id=%s"""
        sql2, params2 = self._apply_filters(sql2, [company_id], f, date_col="t.transferred_at::date", plant_col="o.plant_id")
        return self._all(f"({sql}) UNION ALL ({sql2}) ORDER BY posted_at DESC LIMIT 1000", tuple(params + params2))

    def cost_variance_by_product(self, company_id: str) -> List[dict]:
        return self._all("""SELECT p.code, p.name, COUNT(*) AS orders, SUM(o.planned_cost) AS planned_cost, SUM(o.actual_cost) AS actual_cost,
                            SUM(o.actual_cost)-SUM(o.planned_cost) AS variance FROM mfg_production_orders o JOIN mfg_products p ON p.id=o.product_id
                            WHERE o.company_id=%s AND o.status IN ('confirmed','closed') GROUP BY p.code, p.name ORDER BY p.code""", (company_id,))

    # ── process map ─────────────────────────────────────────────
    def process_status(self, company_id: str, ref: str) -> List[dict]:
        """Live status of the 16 steps for a sales-order or MO reference. Foreign tables are
        consulted only when present (information_schema)."""
        found: Dict[str, dict] = {}
        ref = _str(ref)
        if not ref:
            return build_process_status(PROCESS_STEPS, found)
        orders = self.get_order_by_source(company_id, ref)
        if not orders:
            o = self.get_order_by_no(company_id, ref)
            orders = [o] if o else []
        so_ref = orders[0]["source_ref"] if orders and orders[0].get("source_ref") else ref
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cols = table_columns(cur, "commercial_sales_orders")
                    key = next((c for c in ("order_no", "so_no", "so_number", "number", "reference", "id") if c in cols), None)
                    if key:
                        cur.execute(f"SELECT * FROM commercial_sales_orders WHERE {key}=%s" + (" AND company_id=%s" if "company_id" in cols else "") + " LIMIT 1",
                                    (so_ref, company_id) if "company_id" in cols else (so_ref,))
                        so = cur.fetchone()
                        if so:
                            so = dict(so)
                            st = _str(so.get("status")).lower()
                            found["customer_po"] = {"status": "done", "detail": f"PO {so.get('customer_po') or so.get('po_no') or ''}".strip(),
                                                    "url": f"/commercial/sales-orders/{so.get('id')}"}
                            found["sales_order"] = {"status": "done" if st in ("approved", "confirmed", "in_production", "dispatched", "closed", "delivered") else "in_progress",
                                                    "detail": f"{so_ref} — {st or 'draft'}", "url": f"/commercial/sales-orders/{so.get('id')}"}
                            if st in ("dispatched", "delivered", "closed"):
                                found["dispatch"] = {"status": "done", "detail": st}
                            if st in ("delivered", "closed", "accepted"):
                                found["acceptance"] = {"status": "done", "detail": st}
                    for tbl, keys in (("quality_rm_inspections", ("incoming_inspection",)), ("quality_final_inspections", ("quality_final",)),
                                      ("quality_inspections", ("incoming_inspection", "quality_final"))):
                        qc = table_columns(cur, tbl)
                        rcol = next((c for c in ("source_ref", "reference", "order_no", "mo_no", "ref_no") if c in qc), None)
                        if rcol and orders:
                            refs = [o["order_no"] for o in orders] + [so_ref]
                            cur.execute(f"SELECT * FROM {tbl} WHERE {rcol} = ANY(%s) ORDER BY 1 DESC LIMIT 1", (refs,))
                            q = cur.fetchone()
                            if q:
                                q = dict(q)
                                res = _str(q.get("result") or q.get("status") or q.get("decision"))
                                for k in keys:
                                    found.setdefault(k, {"status": "done" if res else "in_progress", "detail": res, "url": "/quality/"})
                    pcols = table_columns(cur, "proc_purchase_requisitions")
                    if pcols and orders:
                        cur.execute("SELECT status, id FROM proc_purchase_requisitions WHERE company_id=%s AND (title ILIKE %s OR description ILIKE %s) ORDER BY created_at DESC LIMIT 1",
                                    (company_id, f"%{so_ref}%", f"%{so_ref}%"))
                        pr = cur.fetchone()
                        if pr:
                            found["procurement_office"] = {"status": "done", "detail": f"PR {pr['status']}", "url": "/procurement/pr"}
                            found["management_approval"] = {"status": "done" if pr["status"] == "approved" else "in_progress", "detail": pr["status"]}
                    if table_columns(cur, "proc_purchase_orders") and orders:
                        cur.execute("SELECT status, grn_received FROM proc_purchase_orders WHERE company_id=%s AND title ILIKE %s ORDER BY created_at DESC LIMIT 1",
                                    (company_id, f"%{so_ref}%"))
                        po = cur.fetchone()
                        if po:
                            found["procurement_exec"] = {"status": "done", "detail": f"PO {po['status']}", "url": "/procurement/po"}
                            if po.get("grn_received"):
                                found["rm_receipt"] = {"status": "done", "detail": "GRN received"}
        except Exception as e:
            logger.debug("process_status foreign lookups: %s", e)
        if orders:
            o = orders[-1]
            st = o["status"]
            found["mo_tds_plan"] = {"status": "done", "detail": f"{o['order_no']} ({st})" + (" · TDS approved" if o.get("tds_id") else " · no approved TDS"),
                                    "url": f"/manufacturing/orders/{o['id']}"}
            rm = self._one("SELECT id, status, store_req_ids, purchase_req_id FROM mfg_raw_material_plans WHERE company_id=%s AND status<>'draft' ORDER BY created_at DESC LIMIT 1", (company_id,))
            if rm:
                found["sr_pr"] = {"status": "done", "detail": f"RM plan {rm['status']}", "url": f"/manufacturing/rm-plans/{rm['id']}"}
                found.setdefault("procurement_office", {"status": "done" if rm.get("purchase_req_id") else "not_started", "detail": "PR raised" if rm.get("purchase_req_id") else "", "url": "/procurement/pr"})
            if st in ("released", "in_progress", "confirmed", "closed"):
                found["release"] = {"status": "done", "detail": f"released {o.get('released_at') or ''}", "url": f"/manufacturing/orders/{o['id']}"}
                found.setdefault("incoming_inspection", {"status": "done", "detail": "gate passed at release"})
                found.setdefault("rm_receipt", {"status": "done", "detail": "materials available"})
            if st == "in_progress":
                found["production"] = {"status": "in_progress", "detail": f"{D(o['qty_produced'])}/{D(o['qty_ordered'])} {o['unit']}", "url": f"/manufacturing/orders/{o['id']}"}
            if st in ("confirmed", "closed"):
                found["production"] = {"status": "done", "detail": f"{D(o['qty_produced'])} {o['unit']} produced", "url": f"/manufacturing/orders/{o['id']}"}
                found["fg_inventory"] = {"status": "done", "detail": f"GL {o.get('gl_status') or 'n/a'}"}
                tr = self._one("SELECT ref_no, to_store FROM mfg_fg_transfers WHERE order_id=%s ORDER BY transferred_at DESC LIMIT 1", (o["id"],))
                if tr:
                    found["fg_market_store"] = {"status": "done", "detail": f"{tr['ref_no']} → {tr['to_store']}"}
        return build_process_status(PROCESS_STEPS, found)

    # ── public API ──────────────────────────────────────────────
    def create_order_from_sales_order(self, company_id: str, *, source_ref: str, customer_name: str, product_code: str, qty,
                                      unit: str = None, cutting_length=None, packing: str = None, delivery_date=None,
                                      prepared_by: str = "") -> Optional[dict]:
        product = self.product_by_code(company_id, product_code)
        if not product:
            logger.warning("create_order_from_sales_order: unknown product %s", product_code); return None
        o = self.create_order(company_id, {"product_id": product["id"], "order_type": "make_to_order", "source_ref": source_ref,
                                           "customer_name": customer_name, "qty_ordered": qty, "unit": unit or product["unit"],
                                           "cutting_length": cutting_length, "packing": packing, "delivery_date": delivery_date},
                              prepared_by or "commercial")
        if o and self.get_settings(company_id).get("mto_auto_release"):
            self.request_release(o["id"], company_id, prepared_by or "commercial")
            o = self.get_order(o["id"], company_id)
        return o

    def order_status_summary(self, company_id: str, source_ref: str) -> dict:
        orders = self.get_order_by_source(company_id, source_ref)
        by_status: Dict[str, int] = {}
        for o in orders:
            by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        return {"orders": len(orders), "by_status": by_status,
                "qty_ordered": sum((D(o["qty_ordered"]) for o in orders), Decimal(0)),
                "qty_produced": sum((D(o["qty_produced"]) for o in orders), Decimal(0)),
                "latest_status": orders[-1]["status"] if orders else None,
                "order_nos": [o["order_no"] for o in orders],
                "delivery_date": min((o["delivery_date"] for o in orders if o.get("delivery_date")), default=None)}


manufacturing_store = ManufacturingDataStore()


# module-level public API -------------------------------------------------

def create_order_from_sales_order(company_id: str, **kw) -> Optional[dict]:
    return manufacturing_store.create_order_from_sales_order(company_id, **kw)


def get_order_by_source(company_id: str, source_ref: str) -> List[dict]:
    return manufacturing_store.get_order_by_source(company_id, source_ref)


def product_by_code(company_id: str, code: str) -> Optional[dict]:
    return manufacturing_store.product_by_code(company_id, code)


def active_bom_for(product_id: str) -> Optional[dict]:
    return manufacturing_store.active_bom_for(product_id)


def approved_tds_for(product_id: str) -> Optional[dict]:
    return manufacturing_store.approved_tds_for(product_id)


def order_status_summary(company_id: str, source_ref: str) -> dict:
    return manufacturing_store.order_status_summary(company_id, source_ref)


__all__ = ["manufacturing_store", "ensure_schema", "create_order_from_sales_order", "get_order_by_source",
           "product_by_code", "active_bom_for", "approved_tds_for", "order_status_summary", "PROCESS_STEPS"]
