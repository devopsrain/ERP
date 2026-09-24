"""
Quality Management — pure evaluation logic and form definitions.

No database, no FastAPI: everything here is importable from tests, the data
store, the routes and the scheduled job.

Contents
--------
* ``evaluate_line``      spec vs actual → pass | fail | na (min / max / nominal ± tol / range)
* ``overall_result``     roll a set of lines up to pass | fail | conditional | na
* ``spc_stats``          mean, sample σ, UCL/LCL (3σ), Cp / Cpk, out-of-control points
* ``calibration_status`` valid | due_soon | expired | unknown  (due_soon = within 30 days)
* ``capa_effective_status`` open | in_progress | pending_verification | closed | overdue
* ``period_key`` / ``group_stats``  daily / weekly / monthly / quarterly / annual mean & σ
* ``yield_stats``, ``defective_rate``, ``format_number``, ``add_months``
* ``INSPECTION_KINDS``   header fields, default parameter lines, signatures and list
                         columns for the six tender inspection forms
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional

DUE_SOON_DAYS = 30

INSPECTION_STATUSES = ("draft", "submitted", "approved")
OVERALL_RESULTS = ("pass", "fail", "conditional", "na")
LINE_RESULTS = ("pass", "fail", "na")
SPEC_KINDS = ("range", "min", "max", "nominal")
RM_DISPOSITIONS = ("accept", "reject", "return_to_supplier", "request_replacement")
PRODUCT_DEFECTS = ("none", "rework", "reject")
SPEC_APPLIES_TO = ("raw_material", "in_process", "final", "packaging", "audit")
SPEC_STATUSES = ("draft", "active", "obsolete")
EQUIPMENT_STATUSES = ("valid", "due_soon", "expired", "unknown")
COMPLAINT_TYPES = ("electrical", "physical", "packaging", "other")
COMPLAINT_STATUSES = ("open", "investigating", "resolved", "closed")
CAPA_STATUSES = ("open", "in_progress", "pending_verification", "closed", "overdue")
CAPA_SOURCES = ("complaint", "ncr", "audit", "other")
NCR_TYPES = ("inspection", "measurement", "analysis")
NCR_ITEM_KINDS = ("raw_material", "packaging", "finished_good", "process")
NCR_DISPOSITIONS = ("use_as_is", "rework", "reject", "return_to_supplier", "scrap")
NCR_STATUSES = ("open", "dispositioned", "closed")
AUDIT_STATUSES = ("planned", "done", "closed")
AUDIT_RESULTS = ("conforming", "minor_nc", "major_nc", "observation", "na")
GRANULARITIES = ("daily", "weekly", "monthly", "quarterly", "annual")


# ── numeric helpers ───────────────────────────────────────────────

def to_num(value) -> Optional[float]:
    """'' / None / garbage → None; Decimal / str / int → float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        f = float(value)
        return f if math.isfinite(f) else None
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def to_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def format_number(prefix: str, year: int, n: int) -> str:
    """COA-2026-000001 style gapless document numbers."""
    return f"{prefix}-{int(year):04d}-{int(n):06d}"


def add_months(d: date, months: int) -> date:
    """Calendar-month addition clamped to the last day of the target month."""
    months = int(months or 0)
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


# ── spec vs actual ────────────────────────────────────────────────

def effective_limits(spec_value=None, spec_min=None, spec_max=None, tolerance_pct=None,
                     spec_kind: str = "range") -> tuple[Optional[float], Optional[float]]:
    """Resolve a parameter's acceptance window to (lower, upper); None = open."""
    lo, hi = to_num(spec_min), to_num(spec_max)
    if lo is not None or hi is not None:
        return lo, hi
    nominal = to_num(spec_value)
    if nominal is None:
        return None, None
    kind = (spec_kind or "range").lower()
    if kind == "min":
        return nominal, None
    if kind == "max":
        return None, nominal
    tol = to_num(tolerance_pct)
    if tol is None:
        return nominal, nominal
    delta = abs(nominal) * abs(tol) / 100.0
    return nominal - delta, nominal + delta


def evaluate_line(measured, spec_value=None, spec_min=None, spec_max=None,
                  tolerance_pct=None, spec_kind: str = "range") -> str:
    """
    Compare one measured value against its specification.

    * explicit min / max win (either may be open-ended);
    * otherwise ``spec_value`` is read according to ``spec_kind``:
      ``min`` → actual ≥ spec, ``max`` → actual ≤ spec,
      ``nominal``/``range`` → spec ± tolerance_pct (exact match when no tolerance);
    * no measurement or no usable specification → ``na``.
    """
    actual = to_num(measured)
    if actual is None:
        return "na"
    lo, hi = effective_limits(spec_value, spec_min, spec_max, tolerance_pct, spec_kind)
    if lo is None and hi is None:
        return "na"
    eps = 1e-9
    if lo is not None and actual < lo - eps:
        return "fail"
    if hi is not None and actual > hi + eps:
        return "fail"
    return "pass"


def overall_result(lines: Iterable[dict]) -> str:
    """
    pass         every evaluated line passed
    fail         a mandatory line failed
    conditional  only non-mandatory lines failed
    na           nothing could be evaluated
    """
    any_pass = mandatory_fail = optional_fail = False
    for ln in lines or []:
        res = (ln.get("result") or "na").lower()
        mandatory = ln.get("mandatory", True)
        mandatory = True if mandatory is None else bool(mandatory)
        if res == "fail":
            if mandatory:
                mandatory_fail = True
            else:
                optional_fail = True
        elif res == "pass":
            any_pass = True
    if mandatory_fail:
        return "fail"
    if optional_fail:
        return "conditional"
    return "pass" if any_pass else "na"


def evaluate_lines(lines: list[dict]) -> list[dict]:
    """Return copies of ``lines`` with ``result`` filled in from spec vs measured."""
    out = []
    for ln in lines or []:
        d = dict(ln)
        d["result"] = evaluate_line(d.get("measured_value"), d.get("spec_value"), d.get("spec_min"),
                                    d.get("spec_max"), d.get("tolerance_pct"), d.get("spec_kind") or "range")
        out.append(d)
    return out


# ── SPC ───────────────────────────────────────────────────────────

def mean_std(values: Iterable) -> tuple[Optional[float], Optional[float], int]:
    xs = [v for v in (to_num(x) for x in values) if v is not None]
    n = len(xs)
    if n == 0:
        return None, None, 0
    mean = sum(xs) / n
    if n < 2:
        return mean, 0.0, n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return mean, math.sqrt(var), n


def spc_stats(values: Iterable, lsl=None, usl=None, sigma_multiplier: float = 3.0) -> dict:
    """
    Shewhart individuals-chart statistics for one parameter.

    Returns mean, stddev (sample), ucl / lcl (mean ± 3σ), min / max, n,
    cp / cpk (only when limits are given and σ > 0) and the indices of points
    outside the control limits.
    """
    xs = [v for v in (to_num(x) for x in values) if v is not None]
    mean, std, n = mean_std(xs)
    res = {"n": n, "mean": mean, "stddev": std, "ucl": None, "lcl": None,
           "min": min(xs) if xs else None, "max": max(xs) if xs else None,
           "cp": None, "cpk": None, "out_of_control": [], "lsl": to_num(lsl), "usl": to_num(usl),
           "out_of_spec": []}
    if mean is None:
        return res
    std = std or 0.0
    res["ucl"] = mean + sigma_multiplier * std
    res["lcl"] = mean - sigma_multiplier * std
    res["out_of_control"] = [i for i, x in enumerate(xs) if x > res["ucl"] + 1e-12 or x < res["lcl"] - 1e-12]
    lo, hi = res["lsl"], res["usl"]
    if lo is not None or hi is not None:
        res["out_of_spec"] = [i for i, x in enumerate(xs)
                              if (lo is not None and x < lo - 1e-12) or (hi is not None and x > hi + 1e-12)]
    if std > 0:
        if lo is not None and hi is not None:
            res["cp"] = (hi - lo) / (6 * std)
            res["cpk"] = min((hi - mean) / (3 * std), (mean - lo) / (3 * std))
        elif hi is not None:
            res["cpk"] = (hi - mean) / (3 * std)
        elif lo is not None:
            res["cpk"] = (mean - lo) / (3 * std)
    return res


# ── calibration ───────────────────────────────────────────────────

def calibration_status(next_due, today: Optional[date] = None, due_soon_days: int = DUE_SOON_DAYS) -> str:
    due = to_date(next_due)
    if due is None:
        return "unknown"
    today = today or date.today()
    if due < today:
        return "expired"
    if (due - today).days <= due_soon_days:
        return "due_soon"
    return "valid"


def next_due_date(last_calibration, frequency_months) -> Optional[date]:
    last = to_date(last_calibration)
    months = int(to_num(frequency_months) or 0)
    if last is None or months <= 0:
        return None
    return add_months(last, months)


# ── CAPA / complaints ─────────────────────────────────────────────

def capa_effective_status(status: str, target_date, today: Optional[date] = None,
                          completion_date=None) -> str:
    """A CAPA past its target date that is not closed is ``overdue``."""
    status = (status or "open").lower()
    if status == "closed" or completion_date not in (None, ""):
        return "closed" if status == "closed" else status
    tgt = to_date(target_date)
    today = today or date.today()
    if tgt is not None and tgt < today:
        return "overdue"
    return "open" if status == "overdue" else status


def days_overdue(target_date, today: Optional[date] = None) -> int:
    tgt = to_date(target_date)
    if tgt is None:
        return 0
    today = today or date.today()
    return max(0, (today - tgt).days)


def complaint_is_overdue(status: str, date_received, today: Optional[date] = None, sla_days: int = 14) -> bool:
    if (status or "open") in ("resolved", "closed"):
        return False
    rec = to_date(date_received)
    if rec is None:
        return False
    return ((today or date.today()) - rec).days > sla_days


# ── periodic statistics / trends ──────────────────────────────────

def period_key(d, granularity: str = "monthly") -> str:
    dd = to_date(d)
    if dd is None:
        return ""
    g = (granularity or "monthly").lower()
    if g == "daily":
        return dd.isoformat()
    if g == "weekly":
        y, w, _ = dd.isocalendar()
        return f"{y}-W{w:02d}"
    if g == "quarterly":
        return f"{dd.year}-Q{(dd.month - 1) // 3 + 1}"
    if g in ("annual", "yearly"):
        return str(dd.year)
    return f"{dd.year}-{dd.month:02d}"


def group_stats(rows: Iterable[dict], granularity: str = "monthly",
                keys: tuple = ("product", "parameter")) -> list[dict]:
    """
    rows: dicts with ``date``, ``value`` and the grouping ``keys``
    (default product + parameter). Returns one dict per (period, *keys) with
    n / mean / stddev / min / max, ordered by period then keys.
    """
    buckets: dict[tuple, list] = {}
    units: dict[tuple, str] = {}
    for r in rows or []:
        v = to_num(r.get("value"))
        if v is None:
            continue
        k = (period_key(r.get("date"), granularity),) + tuple(str(r.get(x) or "") for x in keys)
        buckets.setdefault(k, []).append(v)
        if r.get("unit") and k not in units:
            units[k] = r["unit"]
    out = []
    for k in sorted(buckets):
        mean, std, n = mean_std(buckets[k])
        d = {"period": k[0], "n": n, "mean": mean, "stddev": std,
             "min": min(buckets[k]), "max": max(buckets[k]), "unit": units.get(k, "")}
        for name, val in zip(keys, k[1:]):
            d[name] = val
        out.append(d)
    return out


def trend_series(rows: Iterable[dict]) -> list[dict]:
    """Chronological (date, value) points with a running mean — for stability charts."""
    pts = sorted(((to_date(r.get("date")), to_num(r.get("value")), r) for r in rows or []),
                 key=lambda t: (t[0] or date.min))
    out, total = [], 0.0
    for i, (d, v, r) in enumerate((p for p in pts if p[1] is not None), start=1):
        total += v
        out.append({"date": d.isoformat() if d else "", "value": v, "running_mean": total / i,
                    "ref": r.get("ref") or ""})
    return out


# ── yield / defects ───────────────────────────────────────────────

def yield_stats(input_length_m, total_length_m, scrap_kg=None, under_length_m=None) -> dict:
    inp = to_num(input_length_m) or 0.0
    out = to_num(total_length_m) or 0.0
    loss = max(0.0, inp - out)
    return {
        "input_m": inp, "output_m": out, "loss_m": loss,
        "yield_pct": (out / inp * 100.0) if inp > 0 else None,
        "loss_pct": (loss / inp * 100.0) if inp > 0 else None,
        "scrap_kg": to_num(scrap_kg) or 0.0,
        "under_length_m": to_num(under_length_m) or 0.0,
    }


def defective_rate(total: int, defective: int) -> Optional[float]:
    total = int(total or 0)
    return (int(defective or 0) / total * 100.0) if total > 0 else None


# ── form definitions (shared by store, routes, templates, tests) ──

def _f(name, label, type="text", col=4, options=None, lookup=None, placeholder="", required=False, step=None):
    return {"name": name, "label": label, "type": type, "col": col, "options": options or (),
            "lookup": lookup, "placeholder": placeholder, "required": required, "step": step}


# (parameter, unit, spec_kind, mandatory) — the tender's spec/actual pairs
INPROCESS_PARAMETERS = (
    ("Wire diameter", "mm", "nominal", True),
    ("Conductor diameter", "mm", "nominal", True),
    ("Conductor resistance", "Ω/km", "max", True),
    ("Insulation thickness", "mm", "min", True),
    ("Insulation diameter", "mm", "nominal", True),
    ("Laid-up diameter", "mm", "nominal", False),
    ("Bedding diameter", "mm", "nominal", False),
    ("Outer sheath thickness", "mm", "min", True),
    ("Outer sheath diameter", "mm", "nominal", True),
    ("Total length", "m", "min", False),
)
INSULATION_PARAMETERS = (
    ("Conductor resistance", "Ω/km", "max", True),
    ("Insulation thickness", "mm", "min", True),
    ("Insulation diameter", "mm", "nominal", True),
)
CONDUCTOR_PARAMETERS = (
    ("Pitch length", "mm", "nominal", True),
    ("Wire diameter", "mm", "nominal", True),
    ("Conductor diameter", "mm", "nominal", True),
    ("Conductor resistance", "Ω/km", "max", True),
)
RM_PARAMETERS = (
    ("Visual / packaging condition", "", "range", True),
    ("Dimension / diameter", "mm", "nominal", True),
    ("Purity / composition", "%", "min", True),
)
FINAL_PARAMETERS = (
    ("Conductor resistance at 20 °C", "Ω/km", "max", True),
    ("High-voltage test", "kV", "min", True),
    ("Insulation resistance", "MΩ·km", "min", True),
    ("Insulation thickness (min)", "mm", "min", True),
    ("Outer sheath thickness (min)", "mm", "min", True),
    ("Overall diameter", "mm", "nominal", False),
    ("Tensile strength of insulation", "N/mm²", "min", False),
    ("Elongation at break", "%", "min", False),
)

_ORDER_FIELDS = [
    _f("order_number", "Order number", lookup="orders", col=3),
    _f("product_type_mm2", "Product type / size (mm²)", lookup="products", col=3, placeholder="NYY 4x16 mm²"),
]
_SIG_FULL = ("prepared_by", "received_by", "inspected_by", "checked_by", "approved_by")

INSPECTION_KINDS: dict[str, dict] = {
    "rm": {
        "label": "Raw Material Inspection", "prefix": "RMI", "table": "quality_rm_inspections",
        "applies_to": "raw_material", "icon": "bi-box-seam",
        "fields": [
            _f("type_of_material", "Type of material", col=4, required=True, placeholder="Copper rod, PVC compound…"),
            _f("material_code", "Material code", col=4),
            _f("supplier_name", "Supplier", lookup="vendors", col=4),
            _f("purchase_requisition_no", "Purchase requisition no.", col=3),
            _f("invoice_number", "Invoice number", col=3),
            _f("invoice_date", "Invoice date", type="date", col=3),
            _f("lot_no", "Lot / batch no.", col=3),
            _f("sample_type", "Sample type", col=3, placeholder="Random / composite"),
            _f("quantity", "Quantity", type="number", col=3, step="0.001"),
            _f("unit", "Unit", col=3, placeholder="kg / m / pcs"),
            _f("standard_required", "Standard required", col=3, placeholder="IEC 60228 / ES 3163"),
            _f("disposition", "Disposition", type="select", col=4, options=RM_DISPOSITIONS),
        ],
        "hidden": ("supplier_id",),
        "default_lines": RM_PARAMETERS,
        "signatures": ("prepared_by", "received_by", "inspected_by", "approved_by"),
        "list_columns": (("type_of_material", "Material"), ("supplier_name", "Supplier"),
                         ("lot_no", "Lot"), ("purchase_requisition_no", "PR no."), ("disposition", "Disposition")),
        "lot_field": "lot_no",
    },
    "inprocess": {
        "label": "Cable In-Process Inspection", "prefix": "IPI", "table": "quality_inprocess_inspections",
        "applies_to": "in_process", "icon": "bi-gear-wide-connected",
        "fields": [
            _f("machine_name", "Machine", lookup="machines", col=3),
            *_ORDER_FIELDS,
            _f("process_type", "Process type", col=3, placeholder="Drawing / stranding / sheathing"),
            _f("production_type", "Production type", col=3),
            _f("next_process", "Next process", col=3),
            _f("lot_no", "Lot / drum no.", col=3),
            _f("description", "Description", type="textarea", col=12),
        ],
        "hidden": ("machine_id", "production_order_id"),
        "default_lines": INPROCESS_PARAMETERS,
        "signatures": ("prepared_by", "inspected_by", "checked_by", "approved_by"),
        "list_columns": (("order_number", "Order"), ("product_type_mm2", "Product"),
                         ("machine_name", "Machine"), ("process_type", "Process")),
        "lot_field": "order_number",
    },
    "insulation": {
        "label": "Wire Insulation Inspection", "prefix": "WII", "table": "quality_insulation_inspections",
        "applies_to": "in_process", "icon": "bi-palette",
        "fields": [
            _f("machine_name", "Machine", lookup="machines", col=3),
            *_ORDER_FIELDS,
            _f("colour_code", "Colour code", col=3),
            _f("process_type", "Process type", col=3),
            _f("production_type", "Production type", col=3),
            _f("next_process", "Next process", col=3),
            _f("actual_length_m", "Actual length (m)", type="number", col=3, step="0.01"),
            _f("product_defect", "Product defect", type="select", col=3, options=PRODUCT_DEFECTS),
            _f("description", "Description", type="textarea", col=12),
        ],
        "hidden": ("machine_id", "production_order_id"),
        "default_lines": INSULATION_PARAMETERS,
        "signatures": ("prepared_by", "inspected_by", "checked_by", "approved_by"),
        "list_columns": (("order_number", "Order"), ("product_type_mm2", "Product"),
                         ("colour_code", "Colour"), ("machine_name", "Machine"), ("product_defect", "Defect")),
        "lot_field": "order_number",
    },
    "final": {
        "label": "Final Product Inspection", "prefix": "FPI", "table": "quality_final_inspections",
        "applies_to": "final", "icon": "bi-patch-check",
        "fields": [
            _f("customer_name", "Customer", lookup="customers", col=4),
            _f("cable_type_mm2", "Cable type / size (mm²)", lookup="products", col=4, required=True),
            _f("standard", "IEC / ES standard", col=4, placeholder="IEC 60502-1 / ES 3163"),
            _f("order_number", "Product order number", lookup="orders", col=3),
            _f("rm_code", "RM code", col=3),
            _f("rated_voltage_kv", "Rated voltage (kV)", type="number", col=3, step="0.01"),
            _f("test_voltage_kv", "Test voltage (kV)", type="number", col=3, step="0.01"),
            _f("total_length_m", "Total length (m)", type="number", col=3, step="0.01"),
            _f("drum_number", "Drum number", col=3),
            _f("lot_no", "Lot / batch no.", col=3),
            _f("test_result", "Test result", type="select", col=3, options=("pass", "fail")),
            _f("description", "Description", type="textarea", col=12),
        ],
        "hidden": ("customer_id", "production_order_id", "certificate_number"),
        "default_lines": FINAL_PARAMETERS,
        "signatures": ("prepared_by", "inspected_by", "checked_by", "approved_by"),
        "list_columns": (("order_number", "Order"), ("cable_type_mm2", "Cable type"),
                         ("customer_name", "Customer"), ("drum_number", "Drum"),
                         ("certificate_number", "Certificate")),
        "lot_field": "order_number",
    },
    "packing": {
        "label": "Wire Packing Summary", "prefix": "WPS", "table": "quality_packing_summaries",
        "applies_to": "packaging", "icon": "bi-box2",
        "fields": [
            *_ORDER_FIELDS,
            _f("colour", "Colour", col=3),
            _f("rolls_count", "Number of rolls", type="number", col=3, step="1"),
            _f("input_length_m", "Input length (m)", type="number", col=3, step="0.01"),
            _f("standard_roll_length_m", "Standard roll length (m)", type="number", col=3, step="0.01"),
            _f("under_length_m", "Under-length (m)", type="number", col=3, step="0.01"),
            _f("total_length_m", "Total length (m)", type="number", col=3, step="0.01"),
            _f("scrap_kg", "Scrap (kg)", type="number", col=3, step="0.001"),
            _f("description", "Description", type="textarea", col=12),
        ],
        "hidden": ("production_order_id",),
        "default_lines": (),
        "signatures": ("prepared_by", "checked_by", "approved_by"),
        "list_columns": (("order_number", "Order"), ("product_type_mm2", "Product"), ("colour", "Colour"),
                         ("input_length_m", "Input (m)"), ("total_length_m", "Output (m)"), ("scrap_kg", "Scrap (kg)")),
        "lot_field": "order_number",
    },
    "conductor": {
        "label": "AAC / ABC Delivery Report", "prefix": "CDR", "table": "quality_conductor_delivery_reports",
        "applies_to": "final", "icon": "bi-lightning-charge",
        "fields": [
            _f("conductor_kind", "Conductor kind", type="select", col=3, options=("AAC", "ABC")),
            *_ORDER_FIELDS,
            _f("customer_name", "Customer", lookup="customers", col=3),
            _f("length_km", "Length (km)", type="number", col=3, step="0.001"),
            _f("length_m", "Length (m)", type="number", col=3, step="0.01"),
            _f("number_of_drums", "Number of drums", type="number", col=3, step="1"),
            _f("description", "Description", type="textarea", col=12),
        ],
        "hidden": ("customer_id", "production_order_id"),
        "default_lines": CONDUCTOR_PARAMETERS,
        "signatures": ("prepared_by", "inspected_by", "checked_by", "approved_by"),
        "list_columns": (("conductor_kind", "Kind"), ("order_number", "Order"), ("product_type_mm2", "Size"),
                         ("customer_name", "Customer"), ("number_of_drums", "Drums")),
        "lot_field": "order_number",
    },
}

KIND_LABELS = {k: v["label"] for k, v in INSPECTION_KINDS.items()}


def kind_fields(kind: str) -> list[dict]:
    return list(INSPECTION_KINDS[kind]["fields"])


def default_lines(kind: str, spec_params: Optional[list[dict]] = None) -> list[dict]:
    """
    Starting parameter lines for a new inspection: the active spec set's
    parameters when one is chosen, otherwise the tender's default pairs.
    """
    if spec_params:
        return [{"parameter": p.get("parameter", ""), "unit": p.get("unit") or "",
                 "spec_kind": p.get("spec_kind") or ("range" if (p.get("min_value") is not None or p.get("max_value") is not None) else "nominal"),
                 "spec_value": p.get("nominal"), "spec_min": p.get("min_value"), "spec_max": p.get("max_value"),
                 "tolerance_pct": p.get("tolerance_pct"), "measured_value": None, "result": "na",
                 "mandatory": p.get("mandatory", True), "remarks": "", "method": p.get("method") or ""}
                for p in spec_params]
    return [{"parameter": name, "unit": unit, "spec_kind": kind_, "spec_value": None, "spec_min": None,
             "spec_max": None, "tolerance_pct": None, "measured_value": None, "result": "na",
             "mandatory": mandatory, "remarks": "", "method": ""}
            for name, unit, kind_, mandatory in INSPECTION_KINDS[kind]["default_lines"]]


def lines_from_form(form: dict) -> list[dict]:
    """
    Parse the repeating ``line_<n>_<field>`` inputs of the inspection form into
    evaluated line dicts (empty parameter names are dropped).
    """
    idx: set[int] = set()
    for k in form:
        if k.startswith("line_"):
            parts = k.split("_", 2)
            if len(parts) == 3 and parts[1].isdigit():
                idx.add(int(parts[1]))
    lines = []
    for i in sorted(idx):
        g = lambda f: form.get(f"line_{i}_{f}")  # noqa: E731
        name = (g("parameter") or "").strip()
        if not name:
            continue
        lines.append({
            "parameter": name[:120], "unit": (g("unit") or "").strip()[:30],
            "spec_kind": (g("spec_kind") or "range") if (g("spec_kind") or "range") in SPEC_KINDS else "range",
            "spec_value": to_num(g("spec_value")), "spec_min": to_num(g("spec_min")),
            "spec_max": to_num(g("spec_max")), "tolerance_pct": to_num(g("tolerance_pct")),
            "measured_value": to_num(g("measured_value")),
            "mandatory": (g("mandatory") or "1") not in ("0", "false", "off", ""),
            "remarks": (g("remarks") or "").strip()[:500], "method": (g("method") or "").strip()[:120],
            "seq": len(lines) + 1,
        })
    return evaluate_lines(lines)


__all__ = [
    "evaluate_line", "evaluate_lines", "overall_result", "effective_limits", "spc_stats", "mean_std",
    "calibration_status", "next_due_date", "capa_effective_status", "days_overdue", "complaint_is_overdue",
    "period_key", "group_stats", "trend_series", "yield_stats", "defective_rate", "format_number",
    "add_months", "to_num", "to_date", "INSPECTION_KINDS", "KIND_LABELS", "default_lines", "lines_from_form",
]
