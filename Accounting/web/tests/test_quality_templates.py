"""
Template render tests — Quality Management module.

Renders every quality/ template with route-accurate contexts, both EMPTY and
POPULATED, so undefined variables / wrong field names fail the build instead
of 500ing in production. No database required.
"""
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:  # `_()`, `|et_date` — installed into jinja2 defaults (conftest does this too)
    import i18n as _i18n
    _i18n.install()
except Exception:  # pragma: no cover
    pass

import quality_forms as qf  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))
TODAY = date(2026, 9, 24)


def _base_ctx(path="/quality/x", query=""):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=query),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(),
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
        active_page="dashboard", kinds=qf.INSPECTION_KINDS, kind_labels=qf.KIND_LABELS,
        inspection_statuses=qf.INSPECTION_STATUSES, overall_results=qf.OVERALL_RESULTS, spec_kinds=qf.SPEC_KINDS,
        spec_applies_to=qf.SPEC_APPLIES_TO, spec_statuses=qf.SPEC_STATUSES, rm_dispositions=qf.RM_DISPOSITIONS,
        product_defects=qf.PRODUCT_DEFECTS, equipment_statuses=qf.EQUIPMENT_STATUSES,
        complaint_types=qf.COMPLAINT_TYPES, complaint_statuses=qf.COMPLAINT_STATUSES,
        capa_statuses=qf.CAPA_STATUSES, capa_sources=qf.CAPA_SOURCES, ncr_types=qf.NCR_TYPES,
        ncr_item_kinds=qf.NCR_ITEM_KINDS, ncr_dispositions=qf.NCR_DISPOSITIONS, ncr_statuses=qf.NCR_STATUSES,
        audit_statuses=qf.AUDIT_STATUSES, audit_results=qf.AUDIT_RESULTS, granularities=qf.GRANULARITIES,
        today=TODAY,
    )


# ── fixtures ──────────────────────────────────────────────────────

_LOOKUPS = {"products": [{"id": "p1", "label": "NYY 4x16 mm² black", "code": "NYY-4x16"}],
            "machines": [{"id": "m1", "label": "Extruder 2"}],
            "orders": [{"id": "o1", "label": "PO-2026-0042", "customer": "EEU"}],
            "customers": [{"id": "c1", "label": "Ethiopian Electric Utility"}],
            "vendors": [{"id": "v1", "label": "Copper Rod Supplier PLC"}]}
_EMPTY_LOOKUPS = {k: [] for k in _LOOKUPS}


def _doc(**kw):
    d = dict(id="doc-1", filename="photo.jpg", size=20480, uploaded_at=datetime(2026, 9, 1, 10, 0), uploaded_by="qc",
             tags=["photo"], version=1, backend="local", content_type="image/jpeg")
    d.update(kw)
    return d


def _line(i, **kw):
    ln = dict(id=f"l{i}", seq=i, parameter="Conductor resistance", unit="Ω/km", spec_kind="max",
              spec_value=Decimal("1.15"), spec_min=None, spec_max=None, tolerance_pct=None,
              measured_value=Decimal("1.12"), result="pass", mandatory=True, method="IEC 60228", remarks="")
    ln.update(kw)
    return ln


def _insp(kind, **kw):
    cfg = qf.INSPECTION_KINDS[kind]
    d = dict(id=f"{kind}-1", company_id="default", ref_no=f"{cfg['prefix']}-2026-000001", inspection_date=TODAY,
             spec_set_id="spec-1", status="submitted", overall_result="pass", remarks="Looks fine",
             prepared_by="A. Bekele", received_by="", inspected_by="M. Tesfaye", checked_by="", approved_by="",
             submitted_at=datetime(2026, 9, 24, 9), approved_at=None, created_by="qc",
             created_at=datetime(2026, 9, 24, 8), updated_at=datetime(2026, 9, 24, 8), kind=kind)
    for f in cfg["fields"]:
        d[f["name"]] = ({"number": Decimal("12.5"), "date": TODAY, "select": f["options"][0] if f["options"] else ""}
                        .get(f["type"], f"{f['label']} value"))
    for h in cfg.get("hidden", ()):
        d[h] = None
    d["lines"] = [_line(1), _line(2, parameter="Insulation thickness", unit="mm", spec_kind="min", spec_value=Decimal("0.8"),
                                  measured_value=Decimal("0.75"), result="fail", remarks="thin spot"),
                  _line(3, parameter="Overall diameter", spec_kind="nominal", spec_value=Decimal("10"), tolerance_pct=Decimal("2"),
                        measured_value=None, result="na", mandatory=False)] if cfg["default_lines"] else []
    d.update(kw)
    return d


def _spec(populated=True):
    s = dict(id="spec-1", company_id="default", name="NYY 4x16 final test plan", applies_to="final", product_code="NYY-4x16",
             product_type="NYY 4x16 mm²", standard="IEC 60502-1 / ES 3163", version="2", status="active", notes="1 sample / drum",
             created_by="qc", created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 9, 1), param_count=2)
    s["params"] = [dict(id="p1", spec_set_id="spec-1", parameter="Conductor resistance", unit="Ω/km", spec_kind="max",
                        nominal=Decimal("1.15"), min_value=None, max_value=None, tolerance_pct=None, method="IEC 60228",
                        sample_size="1/drum", mandatory=True, seq=1),
                   dict(id="p2", spec_set_id="spec-1", parameter="Insulation thickness", unit="mm", spec_kind="range",
                        nominal=None, min_value=Decimal("0.8"), max_value=Decimal("1.2"), tolerance_pct=None, method="",
                        sample_size="", mandatory=False, seq=2)] if populated else []
    return s


def _equipment(populated=True, **kw):
    e = dict(id="eq-1", company_id="default", equipment_name="Digital micrometer", equipment_tag="QC-MM-001",
             location="QC lab", calibration_frequency_months=12, last_calibration_date=date(2025, 10, 1),
             next_due_date=date(2026, 10, 1), calibration_standard="ISO/IEC 17025", performed_by="external",
             provider="NMIE", certificate_number="CAL-991", certificate_doc_id="doc-1" if populated else None,
             status="due_soon", days_to_due=7, notes="", created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    e["records"] = [dict(id="r1", equipment_id="eq-1", calibration_date=date(2025, 10, 1), next_due_date=date(2026, 10, 1),
                         performed_by="external", provider="NMIE", certificate_number="CAL-991", certificate_doc_id="doc-1",
                         result="pass", remarks="as found ok", recorded_by="qc", created_at=datetime(2025, 10, 1))] if populated else []
    e.update(kw)
    return e


def _complaint(**kw):
    c = dict(id="cmp-1", company_id="default", complaint_no="CMP-2026-000001", customer_name="EEU", customer_id=None,
             product_details="NYY 4x16 drum 12", batch_number="LOT-0917", complaint_type="electrical",
             description="Insulation breakdown at 3 kV", date_received=date(2026, 9, 10), status="investigating",
             root_cause_analysis="", responsible_department="Production", supporting_data="", linked_capa_id=None,
             resolution="", closed_at=None, closed_by="", created_by="sales", created_at=datetime(2026, 9, 10),
             updated_at=datetime(2026, 9, 10))
    c.update(kw)
    return c


def _capa(**kw):
    c = dict(id="capa-1", company_id="default", capa_no="CAPA-2026-000001", initiated_date=date(2026, 9, 1),
             problem_description="Insulation thickness below minimum on line 2", nc_kind="actual",
             possible_cause="Worn extruder die", proposed_action="Replace die, verify 10 drums", action_type="corrective",
             conditions_for_closing="3 consecutive conforming lots", responsible_person="Line 2 supervisor",
             target_date=date(2026, 9, 15), completion_date=None, status="in_progress", approved_by="", approved_at=None,
             requested_by="qc", source="ncr", source_id="ncr-1", effectiveness_check="", closed_by="", closed_at=None,
             created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1), effective_status="overdue", days_overdue=9)
    c.update(kw)
    return c


def _ncr(**kw):
    n = dict(id="ncr-1", company_id="default", ncr_no="NCR-2026-000001", date_of_inspection=TODAY, ncr_type="inspection",
             product_or_material="NYY 4x16 mm²", item_kind="finished_good", location="Extruder 2",
             measurement="Insulation thickness: measured 0.75 mm (spec 0.8 min)", description="Thin insulation on drum 12",
             objective_evidence="Inspection IPI-2026-000001", inspector_name="M. Tesfaye",
             inspector_signed_at=datetime(2026, 9, 24, 9), disposition=None, disposition_by="", disposition_at=None,
             disposition_note="", status="open", linked_capa_id=None, source_inspection_kind="inprocess",
             source_inspection_id="inprocess-1", lot_no="PO-2026-0042", quantity=Decimal("500"), unit="m", closed_by="",
             closed_at=None, created_by="qc", created_at=datetime(2026, 9, 24), updated_at=datetime(2026, 9, 24))
    n.update(kw)
    return n


def _audit(populated=True, **kw):
    a = dict(id="aud-1", company_id="default", audit_no="AUD-2026-000001", scope="Production & QC processes",
             standard="ISO 9001:2015", planned_date=date(2026, 9, 1), actual_date=date(2026, 9, 2) if populated else None,
             auditor="Lead auditor", auditee_department="Production", status="done" if populated else "planned",
             summary="Two minor findings", created_by="qa", created_at=datetime(2026, 8, 1), updated_at=datetime(2026, 9, 2),
             item_count=2 if populated else 0, nc_count=1 if populated else 0)
    a["checklist"] = [dict(id="ci1", audit_id="aud-1", seq=1, item="Calibration records available?", requirement_ref="§7.1.5",
                           result="conforming", evidence="Register reviewed", capa_id=None, capa_no=None, capa_status=None),
                      dict(id="ci2", audit_id="aud-1", seq=2, item="Work instructions at extruder?", requirement_ref="§8.5.1",
                           result="minor_nc", evidence="Missing at line 2", capa_id="capa-1", capa_no="CAPA-2026-000001",
                           capa_status="in_progress")] if populated else []
    a.update(kw)
    return a


def _dashboard(populated):
    issues = {"open_ncrs": 2, "open_capas": 3, "overdue_capas": 1, "open_complaints": 1, "equipment_due_soon": 1,
              "equipment_expired": 0, "failed_inspections_30d": 2, "pending_approval": 1, "planned_audits": 1} if populated \
        else {k: 0 for k in ("open_ncrs", "open_capas", "overdue_capas", "open_complaints", "equipment_due_soon",
                             "equipment_expired", "failed_inspections_30d", "pending_approval", "planned_audits")}
    by_kind = [{"kind": k, "label": c["label"], "icon": c["icon"], "count": 3 if populated else 0,
                "passed": 2 if populated else 0, "failed": 1 if populated else 0} for k, c in qf.INSPECTION_KINDS.items()]
    stats = {"issues": issues, "by_kind": by_kind,
             "month": {"inspected": 18 if populated else 0, "passed": 12 if populated else 0, "failed": 6 if populated else 0},
             "recent_failures": [dict(_insp("inprocess", overall_result="fail"), product="NYY 4x16")] if populated else [],
             "equipment_attention": [_equipment()] if populated else [],
             "overdue_capa": [_capa()] if populated else [],
             "open_complaints": [_complaint()] if populated else []}
    chart = {"kind_labels": [k["label"] for k in by_kind], "kind_passed": [k["passed"] for k in by_kind],
             "kind_failed": [k["failed"] for k in by_kind], "kind_other": [0 for _ in by_kind]}
    return dict(stats=stats, chart_json=json.dumps(chart))


def _list_ctx(kind, rows):
    return dict(kind=kind, cfg=qf.INSPECTION_KINDS[kind], inspections=rows, status_filter="", result_filter="",
                search="", date_from="", date_to="")


def _form_ctx(kind, is_edit, spec=None):
    insp = _insp(kind) if is_edit else {"inspection_date": TODAY.isoformat(), "spec_set_id": "", "prepared_by": "qc"}
    return dict(kind=kind, cfg=qf.INSPECTION_KINDS[kind], insp=insp, is_edit=is_edit,
                lines=insp["lines"] if is_edit else qf.default_lines(kind, spec["params"] if spec else None),
                specs=[_spec()] if is_edit else [], lookups=_LOOKUPS if is_edit else _EMPTY_LOOKUPS)


def _detail_ctx(kind, **kw):
    insp = _insp(kind, **kw)
    return dict(kind=kind, cfg=qf.INSPECTION_KINDS[kind], insp=insp, documents=[_doc()], linked_ncrs=[_ncr()] if kw.get("overall_result") == "fail" else [],
                yield_info=qf.yield_stats(insp.get("input_length_m"), insp.get("total_length_m"), insp.get("scrap_kg"), insp.get("under_length_m")) if kind == "packing" else None)


_FILTERS = dict(date_from=date(2026, 6, 26), date_to=TODAY, product="", supplier="", machine="", parameter="", granularity="monthly", order_number="")
_OPTIONS = {"products": ["NYY 4x16 mm²"], "parameters": ["Conductor resistance"], "machines": ["Extruder 2"], "suppliers": ["Copper Rod Supplier PLC"]}


def _report(key, title, columns, rows, filter_fields, **extra):
    return dict(report_key=key, report_title=title, columns=columns, rows=rows, filters=_FILTERS, filter_fields=filter_fields,
                options=_OPTIONS, chart_json=json.dumps(extra.pop("chart", {})) if rows else "", **extra)


_STAT_COLS = [("period", "Period"), ("product", "Product"), ("parameter", "Parameter"), ("unit", "Unit"), ("n", "n"),
              ("mean", "Mean"), ("stddev", "Std. deviation"), ("min", "Min"), ("max", "Max")]
_STAT_ROWS = [{"period": "2026-09", "product": "NYY 4x16", "parameter": "Conductor resistance", "unit": "Ω/km", "n": 12,
               "mean": 1.121, "stddev": 0.012, "min": 1.10, "max": 1.14}]
_SUP_COLS = [("supplier_name", "Supplier"), ("inspections", "Inspections"), ("passed", "Passed"), ("failed", "Failed"),
             ("conditional", "Conditional"), ("pass_rate", "Pass rate %"), ("accepted", "Accepted"), ("rejections", "Rejections"),
             ("returns", "Returned"), ("replacements", "Replacements requested"), ("rating", "Rating"), ("last_inspection", "Last inspection")]
_SUP_ROWS = [{"supplier_name": "Copper Rod Supplier PLC", "inspections": 10, "passed": 9, "failed": 1, "conditional": 0, "pass_rate": 90.0,
              "accepted": 9, "rejections": 1, "returns": 0, "replacements": 0, "rating": "B", "last_inspection": TODAY}]
_YIELD_COLS = [("inspection_date", "Date"), ("ref_no", "Ref"), ("order_number", "Order"), ("product", "Product"), ("colour", "Colour"),
               ("rolls", "Rolls"), ("input_m", "Input (m)"), ("output_m", "Output (m)"), ("loss_m", "Loss (m)"), ("yield_pct", "Yield %"),
               ("under_length_m", "Under-length (m)"), ("scrap_kg", "Scrap (kg)")]
_YIELD_ROWS = [{"inspection_date": TODAY, "ref_no": "WPS-2026-000001", "order_number": "PO-1", "product": "H07V-U 2.5", "colour": "blue",
                "rolls": 40, **qf.yield_stats(4000, 3900, 8, 30)}]
_YIELD_TOTALS = {"input_m": 4000.0, "output_m": 3900.0, "loss_m": 100.0, "scrap_kg": 8.0, "under_length_m": 30.0, "rolls": 40, "yield_pct": 97.5, "loss_pct": 2.5}
_DEF_COLS = [("period", "Period"), ("product", "Product"), ("inspected", "Volume inspected"), ("passed", "Passed"),
             ("conditional", "Conditional"), ("failed", "Defective"), ("defective_pct", "% defective")]
_DEF_ROWS = [{"period": "2026-09", "product": "NYY 4x16", "inspected": 20, "passed": 18, "conditional": 0, "failed": 2, "defective_pct": 10.0}]
_STAB_COLS = [("product", "Product"), ("parameter", "Parameter"), ("unit", "Unit"), ("n", "n"), ("first", "First"), ("last", "Last"),
              ("mean", "Mean"), ("stddev", "Std. deviation"), ("drift_pct", "Drift %"), ("cv_pct", "CV %")]
_STAB_ROWS = [{"product": "NYY 4x16", "parameter": "Conductor resistance", "unit": "Ω/km", "n": 5, "first": 1.10, "last": 1.19,
               "mean": 1.14, "stddev": 0.03, "drift_pct": 8.2, "cv_pct": 2.6}]
_SPC_VALUES = [1.10, 1.12, 1.11, 1.13, 1.30]
_SPC_STATS = qf.spc_stats(_SPC_VALUES, 1.0, 1.2)
_SPC = {"labels": [f"2026-09-{i + 1:02d} IPI-{i}" for i in range(5)], "values": _SPC_VALUES,
        "refs": [{"id": f"i{i}", "kind": "inprocess", "ref": f"IPI-{i}"} for i in range(5)], "unit": "Ω/km", "stats": _SPC_STATS,
        "mean": _SPC_STATS["mean"], "ucl": _SPC_STATS["ucl"], "lcl": _SPC_STATS["lcl"], "lsl": 1.0, "usl": 1.2}
_SPC_ROWS = [{"point": i + 1, "label": lbl, "value": v, "flag": "out of spec" if i == 4 else ""} for i, (lbl, v) in enumerate(zip(_SPC["labels"], _SPC_VALUES))]
_NC_SUMMARY = {"by_item_kind": {"finished_good": 2, "raw_material": 1}, "by_disposition": {"rework": 1, "pending": 2},
               "by_status": {"open": 2, "dispositioned": 1}, "total_ncrs": 3,
               "failed_by_kind": {"inprocess": {"label": "Cable In-Process Inspection", "inspected": 20, "failed": 2, "conditional": 1}}}
_NC_EMPTY = {"by_item_kind": {}, "by_disposition": {}, "by_status": {}, "total_ncrs": 0, "failed_by_kind": {}}
_AUDIT_SUMMARY = {"schedule": [_audit(False, planned_date=date(2026, 8, 1))], "overdue_schedule": [_audit(False, planned_date=date(2026, 8, 1))],
                  "history": [_audit()], "checklist_results": {"conforming": 5, "minor_nc": 1, "major_nc": 0, "observation": 2, "na": 0},
                  "corrective_actions": [_capa(source="audit")], "total": 2}
_AUDIT_EMPTY = {"schedule": [], "overdue_schedule": [], "history": [], "checklist_results": {k: 0 for k in qf.AUDIT_RESULTS},
                "corrective_actions": [], "total": 0}
_CAPA_REPORT = {"past_due": [_capa()], "pending": [_capa(id="capa-2", capa_no="CAPA-2026-000002", effective_status="open", days_overdue=0, target_date=date(2026, 12, 1))],
                "by_requestor": [{"name": "qc", "open": 2, "overdue": 1, "items": []}], "by_assignee": [{"name": "Line 2 supervisor", "open": 2, "overdue": 1, "items": []}],
                "closed_30d": [], "total_open": 2, "today": TODAY}
_CAPA_EMPTY = {"past_due": [], "pending": [], "by_requestor": [], "by_assignee": [], "closed_30d": [], "total_open": 0, "today": TODAY}


CASES = [
    ("quality/dashboard.html", lambda: _dashboard(True)),
    ("quality/dashboard.html", lambda: _dashboard(False)),
    ("quality/spec_list.html", lambda: dict(specs=[_spec(), dict(_spec(), id="spec-2", status="draft", product_type=None, product_code=None, standard="")], applies_filter="", status_filter="")),
    ("quality/spec_list.html", lambda: dict(specs=[], applies_filter="final", status_filter="active")),
    ("quality/spec_form.html", lambda: dict(spec={"applies_to": "final", "version": "1", "status": "draft"}, is_edit=False, lookups=_LOOKUPS)),
    ("quality/spec_form.html", lambda: dict(spec={}, is_edit=False, lookups=_EMPTY_LOOKUPS)),
    ("quality/spec_detail.html", lambda: dict(spec=_spec(), documents=[_doc(filename="ES3163.pdf")], lookups=_LOOKUPS)),
    ("quality/spec_detail.html", lambda: dict(spec=_spec(False), documents=[], lookups=_EMPTY_LOOKUPS)),
    ("quality/certificate.html", lambda: dict(insp=_insp("final", status="approved", approved_by="QA Mgr", approved_at=datetime(2026, 9, 24, 12), certificate_number="COA-2026-000001", test_result="pass"), company={"name": "Belayab Cable"})),
    ("quality/certificate.html", lambda: dict(insp=dict(_insp("final"), lines=[], remarks="", description=""), company={})),
    ("quality/equipment_list.html", lambda: dict(equipment=[_equipment(), _equipment(id="eq-2", equipment_tag="QC-OHM-002", status="expired", days_to_due=-3), _equipment(id="eq-3", next_due_date=None, last_calibration_date=None, status="unknown", days_to_due=None)], status_filter="", search="", counts={"valid": 0, "due_soon": 1, "expired": 1, "unknown": 1})),
    ("quality/equipment_list.html", lambda: dict(equipment=[], status_filter="due_soon", search="", counts={s: 0 for s in qf.EQUIPMENT_STATUSES})),
    ("quality/equipment_form.html", lambda: dict(equipment={"calibration_frequency_months": 12, "performed_by": "external"}, is_edit=False)),
    ("quality/equipment_form.html", lambda: dict(equipment=_equipment(), is_edit=True)),
    ("quality/equipment_detail.html", lambda: dict(equipment=_equipment(), documents=[_doc(filename="cert.pdf", tags=["certificate"])])),
    ("quality/equipment_detail.html", lambda: dict(equipment=_equipment(False, status="expired", notes="Handle with care"), documents=[])),
    ("quality/complaint_list.html", lambda: dict(complaints=[_complaint(), _complaint(id="cmp-2", status="closed", linked_capa_id="capa-1")], status_filter="", search="", date_from="", date_to="")),
    ("quality/complaint_list.html", lambda: dict(complaints=[], status_filter="open", search="x", date_from="2026-01-01", date_to="")),
    ("quality/complaint_form.html", lambda: dict(complaint={"date_received": TODAY.isoformat(), "complaint_type": "other"}, is_edit=False, lookups=_LOOKUPS)),
    ("quality/complaint_form.html", lambda: dict(complaint={}, is_edit=False, lookups=_EMPTY_LOOKUPS)),
    ("quality/complaint_detail.html", lambda: dict(complaint=_complaint(linked_capa_id="capa-1", root_cause_analysis="Die wear"), capa=_capa(), documents=[_doc()], lookups=_LOOKUPS)),
    ("quality/complaint_detail.html", lambda: dict(complaint=_complaint(status="closed", closed_at=datetime(2026, 9, 20), closed_by="mgr", resolution="Replaced drum"), capa=None, documents=[], lookups=_EMPTY_LOOKUPS)),
    ("quality/capa_list.html", lambda: dict(capas=[_capa(), _capa(id="capa-2", effective_status="closed", days_overdue=0, status="closed")], counts={"overdue": 1, "closed": 1}, status_filter="", source_filter="", responsible="", requested_by="", search="", date_from="", date_to="")),
    ("quality/capa_list.html", lambda: dict(capas=[], counts={}, status_filter="overdue", source_filter="ncr", responsible="x", requested_by="", search="", date_from="", date_to="")),
    ("quality/capa_form.html", lambda: dict(capa={"initiated_date": TODAY.isoformat(), "requested_by": "qc", "nc_kind": "actual", "action_type": "corrective", "source": "ncr", "source_id": "ncr-1", "problem_description": "prefilled", "target_date": "2026-10-24"}, is_edit=False)),
    ("quality/capa_form.html", lambda: dict(capa={}, is_edit=False)),
    ("quality/capa_detail.html", lambda: dict(capa=_capa(approved_by="QA Mgr", approved_at=datetime(2026, 9, 2)), approval={"status": "pending", "current_step": "QA manager"}, source=_ncr(), documents=[_doc()])),
    ("quality/capa_detail.html", lambda: dict(capa=_capa(source="complaint", source_id="cmp-1"), approval=None, source=_complaint(), documents=[])),
    ("quality/capa_detail.html", lambda: dict(capa=_capa(status="closed", effective_status="closed", days_overdue=0, closed_at=datetime(2026, 9, 20), closed_by="mgr", effectiveness_check="3 lots ok", completion_date=date(2026, 9, 19), source="other", source_id=None), approval=None, source=None, documents=[])),
    ("quality/capa_reminders.html", lambda: dict(report=_CAPA_REPORT)),
    ("quality/capa_reminders.html", lambda: dict(report=_CAPA_EMPTY)),
    ("quality/ncr_list.html", lambda: dict(ncrs=[_ncr(), _ncr(id="ncr-2", status="closed", disposition="rework", linked_capa_id="capa-1")], status_filter="", item_kind_filter="", search="", date_from="", date_to="")),
    ("quality/ncr_list.html", lambda: dict(ncrs=[], status_filter="open", item_kind_filter="process", search="", date_from="", date_to="")),
    ("quality/ncr_form.html", lambda: dict(ncr={"date_of_inspection": TODAY.isoformat(), "ncr_type": "inspection", "item_kind": "finished_good", "inspector_name": "qc"}, is_edit=False)),
    ("quality/ncr_form.html", lambda: dict(ncr=dict(_ncr(), date_of_inspection=TODAY), is_edit=False)),
    ("quality/ncr_detail.html", lambda: dict(ncr=_ncr(), capa=None, documents=[_doc()])),
    ("quality/ncr_detail.html", lambda: dict(ncr=_ncr(status="closed", disposition="rework", disposition_by="mgr", disposition_at=datetime(2026, 9, 25), disposition_note="re-extrude", closed_at=datetime(2026, 9, 26), closed_by="mgr", linked_capa_id="capa-1", source_inspection_kind=None, source_inspection_id=None, quantity=None), capa=_capa(), documents=[])),
    ("quality/audit_list.html", lambda: dict(audits=[_audit(), _audit(False, id="aud-2", planned_date=date(2026, 8, 1))], status_filter="", date_from="", date_to="")),
    ("quality/audit_list.html", lambda: dict(audits=[], status_filter="planned", date_from="", date_to="")),
    ("quality/audit_form.html", lambda: dict(audit={"standard": "ISO 9001:2015", "auditor": "qa"}, is_edit=False)),
    ("quality/audit_form.html", lambda: dict(audit={}, is_edit=False)),
    ("quality/audit_detail.html", lambda: dict(audit=_audit(), summary={"conforming": 1, "minor_nc": 1, "major_nc": 0, "observation": 0, "na": 0}, documents=[_doc()])),
    ("quality/audit_detail.html", lambda: dict(audit=_audit(False), summary={k: 0 for k in qf.AUDIT_RESULTS}, documents=[])),
    ("quality/audit_detail.html", lambda: dict(audit=_audit(status="closed"), summary={k: 0 for k in qf.AUDIT_RESULTS}, documents=[])),
    ("quality/reports_index.html", lambda: dict()),
    ("quality/report_table.html", lambda: _report("parameter_stats", "Periodic mean & standard deviation per parameter", _STAT_COLS, _STAT_ROWS, ("date", "granularity", "product", "parameter", "machine"), description="desc")),
    ("quality/report_table.html", lambda: _report("parameter_stats", "Periodic mean & standard deviation per parameter", _STAT_COLS, [], ("date", "granularity", "product", "parameter", "machine"), description="desc")),
    ("quality/report_table.html", lambda: _report("supplier", "Supplier compliance & reputability", _SUP_COLS, _SUP_ROWS, ("date", "supplier"), chart={"labels": ["x"], "pass_rate": [90], "rejections": [1]}, chart_kind="supplier", description="desc")),
    ("quality/report_table.html", lambda: _report("yield", "Material yield & analysis", _YIELD_COLS, _YIELD_ROWS, ("date", "product"), chart={"labels": ["x"], "input": [1], "output": [1], "scrap": [0]}, chart_kind="yield", totals=_YIELD_TOTALS, description="desc")),
    ("quality/report_table.html", lambda: _report("yield", "Material yield & analysis", _YIELD_COLS, [], ("date", "product"), chart_kind="yield", totals={}, description="desc")),
    ("quality/report_table.html", lambda: _report("defects", "Volume vs. percentage defective", _DEF_COLS, _DEF_ROWS, ("date", "granularity", "product"), chart={"labels": ["2026-09"], "volume": [20], "pct": [10]}, chart_kind="defects", description="desc")),
    ("quality/report_stability.html", lambda: _report("stability", "Product stability analysis", _STAB_COLS, _STAB_ROWS, ("date", "product", "parameter", "machine"), chart={"series": [{"label": "x", "labels": ["a"], "values": [1], "running_mean": [1], "mean": 1, "ucl": 1, "lcl": 1}]})),
    ("quality/report_stability.html", lambda: _report("stability", "Product stability analysis", _STAB_COLS, [], ("date", "product", "parameter", "machine"))),
    ("quality/report_spc.html", lambda: dict(_report("spc", "SPC control chart", [("point", "#")], _SPC_ROWS, ("date", "parameter", "product", "machine", "order"), chart=_SPC, spc=_SPC, lsl="1.0", usl="1.2"), filters=dict(_FILTERS, parameter="Conductor resistance"))),
    ("quality/report_spc.html", lambda: dict(_report("spc", "SPC control chart", [("point", "#")], [], ("date", "parameter", "product", "machine", "order"), spc=None, lsl="", usl=""), filters=dict(_FILTERS, parameter="Conductor resistance"))),
    ("quality/report_spc.html", lambda: _report("spc", "SPC control chart", [("point", "#")], [], ("date", "parameter", "product", "machine", "order"), spc=None, lsl="", usl="")),
    ("quality/report_lot_history.html", lambda: dict(ref="PO-2026-0042", events=[
        {"date": TODAY, "at": datetime(2026, 9, 24), "kind": "inprocess", "label": "Cable In-Process Inspection", "ref_no": "IPI-1", "id": "i1", "result": "fail", "status": "approved", "product": "NYY", "detail": "Extruder 2"},
        {"date": TODAY, "at": datetime(2026, 9, 24, 1), "kind": "ncr", "label": "NCR", "ref_no": "NCR-1", "id": "n1", "result": "rework", "status": "open", "product": "NYY", "detail": "thin"},
        {"date": TODAY, "at": datetime(2026, 9, 24, 2), "kind": "complaint", "label": "Complaint", "ref_no": "CMP-1", "id": "c1", "result": "electrical", "status": "open", "product": "NYY", "detail": "EEU"}])),
    ("quality/report_lot_history.html", lambda: dict(ref="LOT-X", events=[])),
    ("quality/report_lot_history.html", lambda: dict(ref="", events=[])),
    ("quality/report_nc_summary.html", lambda: dict(summary=_NC_SUMMARY, filters=_FILTERS, chart_json=json.dumps({"labels": ["finished_good"], "counts": [2]}))),
    ("quality/report_nc_summary.html", lambda: dict(summary=_NC_EMPTY, filters=_FILTERS, chart_json="{}")),
    ("quality/report_audit_summary.html", lambda: dict(summary=_AUDIT_SUMMARY, filters=dict(_FILTERS, date_from="", date_to=""), chart_json="{}")),
    ("quality/report_audit_summary.html", lambda: dict(summary=_AUDIT_EMPTY, filters=dict(_FILTERS, date_from="", date_to=""), chart_json="{}")),
]
# generic inspection templates × every kind × empty / populated
for _kind in qf.INSPECTION_KINDS:
    CASES += [
        ("quality/inspection_list.html", (lambda k: lambda: _list_ctx(k, [_insp(k), _insp(k, id=f"{k}-2", overall_result="fail", status="draft")]))(_kind)),
        ("quality/inspection_list.html", (lambda k: lambda: _list_ctx(k, []))(_kind)),
        ("quality/inspection_form.html", (lambda k: lambda: _form_ctx(k, False))(_kind)),
        ("quality/inspection_form.html", (lambda k: lambda: _form_ctx(k, False, _spec()))(_kind)),
        ("quality/inspection_form.html", (lambda k: lambda: _form_ctx(k, True))(_kind)),
        ("quality/inspection_detail.html", (lambda k: lambda: _detail_ctx(k))(_kind)),
        ("quality/inspection_detail.html", (lambda k: lambda: _detail_ctx(k, overall_result="fail", status="draft", order_number="PO-1"))(_kind)),
        ("quality/inspection_detail.html", (lambda k: lambda: dict(_detail_ctx(k, status="approved", approved_by="QA", approved_at=datetime(2026, 9, 24)), documents=[]))(_kind)),
    ]


@pytest.mark.parametrize("template,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_quality_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000
    if template != "quality/certificate.html":
        assert "module-sidebar" in html
    else:
        assert "Certificate of Analysis" in html


def test_every_quality_template_is_covered():
    covered = {t for t, _ in CASES}
    on_disk = {f"quality/{p.name}" for p in (_WEB_DIR / "templates" / "quality").glob("*.html") if not p.name.startswith("_")}
    assert on_disk == covered, on_disk ^ covered


def test_templates_do_not_use_top_level_jquery_ready():
    for tpl in (_WEB_DIR / "templates" / "quality").glob("*.html"):
        assert "$(document).ready" not in tpl.read_text(encoding="utf-8"), tpl.name


def test_inspection_form_lines_and_evaluation_script():
    html = env.get_template("quality/inspection_form.html").render(**_base_ctx(), **_form_ctx("inprocess", False))
    for name, *_ in qf.INPROCESS_PARAMETERS:
        assert f'value="{name}"' in html
    assert 'name="line_0_measured_value"' in html and 'id="q-line-tpl"' in html
    edit = env.get_template("quality/inspection_form.html").render(**_base_ctx(), **_form_ctx("insulation", True))
    assert "q-result-fail" in edit and 'value="1.12"' in edit


def test_detail_shows_raise_ncr_only_on_failure_and_certificate_for_final():
    ok = env.get_template("quality/inspection_detail.html").render(**_base_ctx(), **_detail_ctx("final"))
    bad = env.get_template("quality/inspection_detail.html").render(**_base_ctx(), **_detail_ctx("final", overall_result="fail"))
    assert "Raise NCR" not in ok and "Raise NCR" in bad
    assert "Certificate of Analysis" in ok
    assert "Certificate of Analysis" not in env.get_template("quality/inspection_detail.html").render(**_base_ctx(), **_detail_ctx("rm"))


def test_certificate_lists_every_line_and_number():
    ctx = dict(insp=_insp("final", certificate_number="COA-2026-000007", status="approved"), company={})
    html = env.get_template("quality/certificate.html").render(**_base_ctx(), **ctx)
    assert "COA-2026-000007" in html and "Accuracy of determination" in html
    assert html.count("<tr>") >= 3 + 5   # meta rows + one per line


def test_dashboard_embeds_chart_json():
    html = env.get_template("quality/dashboard.html").render(**_base_ctx(), **_dashboard(True))
    assert 'id="q-chart-data"' in html and "qKindChart" in html and "Raw Material Inspection" in html


def test_spc_report_uses_cdnjs_fallback_and_json_endpoint():
    src = (_WEB_DIR / "templates" / "quality" / "report_spc.html").read_text(encoding="utf-8")
    assert "cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js" in src
    html = env.get_template("quality/report_spc.html").render(**_base_ctx(), **CASES[[t for t, _ in CASES].index("quality/report_spc.html")][1]())
    assert "qSpcChart" in html and "Cp / Cpk" in html
