"""
Pure-logic tests — Quality Management module (no database, no FastAPI).

Covers spec vs actual evaluation, overall roll-up, SPC statistics, calibration
status, CAPA overdue logic, periodic grouping, yield / defect maths, the
form-line parser and the module wiring contract (ensure_schema /
register_jobs / public integration API).
"""
import importlib
import inspect
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import quality_forms as qf  # noqa: E402


# ── spec vs actual ────────────────────────────────────────────────

@pytest.mark.parametrize("measured,kw,expected", [
    (None, {}, "na"),
    ("", {"spec_min": 1}, "na"),
    ("abc", {"spec_min": 1}, "na"),
    (5, {}, "na"),                                              # nothing to compare against
    (5, {"spec_min": 1, "spec_max": 10}, "pass"),
    (0.5, {"spec_min": 1, "spec_max": 10}, "fail"),
    (11, {"spec_min": 1, "spec_max": 10}, "fail"),
    (10, {"spec_min": 1, "spec_max": 10}, "pass"),              # inclusive bounds
    (1.15, {"spec_value": 1.15, "spec_kind": "max"}, "pass"),   # IEC 60228 max resistance
    (1.16, {"spec_value": 1.15, "spec_kind": "max"}, "fail"),
    (0.8, {"spec_value": 0.8, "spec_kind": "min"}, "pass"),     # min insulation thickness
    (0.79, {"spec_value": 0.8, "spec_kind": "min"}, "fail"),
    (2.05, {"spec_value": 2.0, "tolerance_pct": 5, "spec_kind": "nominal"}, "pass"),
    (2.11, {"spec_value": 2.0, "tolerance_pct": 5, "spec_kind": "nominal"}, "fail"),
    (1.89, {"spec_value": 2.0, "tolerance_pct": 5, "spec_kind": "nominal"}, "fail"),
    (2.0, {"spec_value": 2.0, "spec_kind": "nominal"}, "pass"),  # nominal without tolerance = exact
    (2.001, {"spec_value": 2.0, "spec_kind": "nominal"}, "fail"),
    (Decimal("7.5"), {"spec_min": Decimal("7"), "spec_max": Decimal("8")}, "pass"),
    ("1,250", {"spec_min": 1000}, "pass"),                      # thousands separator tolerated
    (5, {"spec_min": 1, "spec_max": 10, "spec_value": 99, "spec_kind": "max"}, "pass"),  # explicit bounds win
])
def test_evaluate_line(measured, kw, expected):
    assert qf.evaluate_line(measured, **kw) == expected


def test_effective_limits_variants():
    assert qf.effective_limits(spec_value=10, spec_kind="min") == (10.0, None)
    assert qf.effective_limits(spec_value=10, spec_kind="max") == (None, 10.0)
    assert qf.effective_limits(spec_value=10, tolerance_pct=10) == (9.0, 11.0)
    assert qf.effective_limits(spec_min=1) == (1.0, None)
    assert qf.effective_limits() == (None, None)


def test_overall_result_rollup():
    assert qf.overall_result([]) == "na"
    assert qf.overall_result([{"result": "na"}]) == "na"
    assert qf.overall_result([{"result": "pass"}, {"result": "na"}]) == "pass"
    assert qf.overall_result([{"result": "pass"}, {"result": "fail"}]) == "fail"
    assert qf.overall_result([{"result": "pass"}, {"result": "fail", "mandatory": False}]) == "conditional"
    assert qf.overall_result([{"result": "fail", "mandatory": False}, {"result": "fail", "mandatory": True}]) == "fail"
    assert qf.overall_result([{"result": "fail", "mandatory": None}]) == "fail"   # None → mandatory


def test_evaluate_lines_fills_result():
    out = qf.evaluate_lines([{"parameter": "R", "spec_value": 1.15, "spec_kind": "max", "measured_value": 1.2},
                             {"parameter": "t", "spec_value": 0.8, "spec_kind": "min", "measured_value": 0.85}])
    assert [ln["result"] for ln in out] == ["fail", "pass"]
    assert qf.overall_result(out) == "fail"


def test_lines_from_form_parses_repeating_inputs():
    form = {
        "line_0_parameter": "Wire diameter", "line_0_unit": "mm", "line_0_spec_kind": "nominal",
        "line_0_spec_value": "2.25", "line_0_tolerance_pct": "1", "line_0_measured_value": "2.26",
        "line_3_parameter": "Resistance", "line_3_unit": "Ω/km", "line_3_spec_kind": "max",
        "line_3_spec_value": "7.41", "line_3_measured_value": "7.6", "line_3_mandatory": "0",
        "line_7_parameter": "", "line_7_measured_value": "5",      # blank parameter → dropped
        "line_9_parameter": "Bad kind", "line_9_spec_kind": "weird", "line_9_measured_value": "",
        "unrelated": "x",
    }
    lines = qf.lines_from_form(form)
    assert [ln["parameter"] for ln in lines] == ["Wire diameter", "Resistance", "Bad kind"]
    assert lines[0]["result"] == "pass" and lines[0]["mandatory"] is True
    assert lines[1]["result"] == "fail" and lines[1]["mandatory"] is False
    assert lines[2]["spec_kind"] == "range" and lines[2]["result"] == "na"
    assert [ln["seq"] for ln in lines] == [1, 2, 3]
    assert qf.overall_result(lines) == "conditional"


def test_default_lines_from_kind_and_spec():
    d = qf.default_lines("inprocess")
    assert len(d) == len(qf.INPROCESS_PARAMETERS)
    assert d[2]["parameter"] == "Conductor resistance" and d[2]["spec_kind"] == "max"
    assert qf.default_lines("packing") == []
    spec = [{"parameter": "Resistance", "unit": "Ω/km", "spec_kind": "max", "nominal": Decimal("1.15"),
             "min_value": None, "max_value": None, "tolerance_pct": None, "mandatory": True, "method": "IEC 60228"}]
    s = qf.default_lines("final", spec)
    assert s[0]["spec_value"] == Decimal("1.15") and s[0]["method"] == "IEC 60228" and s[0]["measured_value"] is None
    ranged = qf.default_lines("rm", [{"parameter": "Purity", "min_value": 99.9, "max_value": None}])
    assert ranged[0]["spec_kind"] == "range"


def test_inspection_kind_config_is_consistent():
    for kind, cfg in qf.INSPECTION_KINDS.items():
        names = [f["name"] for f in cfg["fields"]]
        assert len(names) == len(set(names)), kind
        assert not set(names) & set(cfg.get("hidden", ())), kind
        assert cfg["prefix"] and cfg["table"].startswith("quality_"), kind
        assert all(c in names or c in cfg.get("hidden", ()) for c, _ in cfg["list_columns"]), kind
        assert cfg["lot_field"] in names, kind
        for f in cfg["fields"]:
            assert f["type"] in ("text", "number", "date", "select", "textarea"), (kind, f["name"])
            if f["type"] == "select":
                assert f["options"], (kind, f["name"])


# ── SPC ───────────────────────────────────────────────────────────

def test_spc_stats_basic():
    vals = [10, 12, 11, 13, 9, 10, 12, 11]
    st = qf.spc_stats(vals)
    assert st["n"] == 8
    assert st["mean"] == pytest.approx(11.0)
    assert st["stddev"] == pytest.approx(1.3093, abs=1e-3)      # sample σ (n-1)
    assert st["ucl"] == pytest.approx(11 + 3 * st["stddev"])
    assert st["lcl"] == pytest.approx(11 - 3 * st["stddev"])
    assert st["min"] == 9 and st["max"] == 13
    assert st["out_of_control"] == [] and st["cp"] is None and st["cpk"] is None


def test_spc_stats_cp_cpk_and_outliers():
    vals = [10.0] * 9 + [10.2, 9.8, 30.0]
    st = qf.spc_stats(vals, lsl=9.0, usl=11.0)
    assert 11 in st["out_of_control"]
    assert 11 in st["out_of_spec"]
    assert st["cp"] == pytest.approx((11 - 9) / (6 * st["stddev"]))
    assert st["cpk"] <= st["cp"]
    one_sided = qf.spc_stats([1.0, 1.1, 0.9, 1.05], usl=1.5)
    assert one_sided["cp"] is None and one_sided["cpk"] is not None and one_sided["cpk"] > 0


def test_spc_stats_edge_cases():
    assert qf.spc_stats([])["mean"] is None
    single = qf.spc_stats([5])
    assert single["mean"] == 5 and single["stddev"] == 0 and single["ucl"] == 5 and single["lcl"] == 5
    with_junk = qf.spc_stats(["3", None, "", "x", Decimal("5")])
    assert with_junk["n"] == 2 and with_junk["mean"] == 4


def test_mean_std():
    assert qf.mean_std([]) == (None, None, 0)
    assert qf.mean_std([2, 4]) == (3.0, pytest.approx(1.4142, abs=1e-3), 2)


# ── calibration ───────────────────────────────────────────────────

def test_calibration_status():
    today = date(2026, 9, 24)
    assert qf.calibration_status(None, today) == "unknown"
    assert qf.calibration_status(date(2026, 9, 23), today) == "expired"
    assert qf.calibration_status(date(2026, 9, 24), today) == "due_soon"       # due today = still valid but due
    assert qf.calibration_status(date(2026, 10, 24), today) == "due_soon"      # exactly 30 days
    assert qf.calibration_status(date(2026, 10, 25), today) == "valid"
    assert qf.calibration_status("2027-01-01", today) == "valid"
    assert qf.calibration_status("garbage", today) == "unknown"


def test_next_due_date_and_add_months():
    assert qf.next_due_date(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert qf.next_due_date(date(2024, 1, 31), 1) == date(2024, 2, 29)         # leap year
    assert qf.next_due_date(date(2026, 3, 15), 12) == date(2027, 3, 15)
    assert qf.next_due_date(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert qf.next_due_date(None, 12) is None
    assert qf.next_due_date(date(2026, 1, 1), 0) is None
    assert qf.add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)


# ── CAPA / complaints ─────────────────────────────────────────────

def test_capa_effective_status():
    today = date(2026, 9, 24)
    assert qf.capa_effective_status("open", None, today) == "open"
    assert qf.capa_effective_status("open", date(2026, 9, 30), today) == "open"
    assert qf.capa_effective_status("open", date(2026, 9, 23), today) == "overdue"
    assert qf.capa_effective_status("in_progress", "2026-01-01", today) == "overdue"
    assert qf.capa_effective_status("closed", date(2026, 1, 1), today) == "closed"
    assert qf.capa_effective_status("overdue", date(2026, 12, 1), today) == "open"   # target moved out
    assert qf.capa_effective_status("pending_verification", date(2026, 1, 1), today,
                                    completion_date=date(2026, 9, 1)) == "pending_verification"
    assert qf.days_overdue(date(2026, 9, 20), today) == 4
    assert qf.days_overdue(date(2026, 9, 30), today) == 0
    assert qf.days_overdue(None, today) == 0


def test_complaint_is_overdue():
    today = date(2026, 9, 24)
    assert qf.complaint_is_overdue("open", date(2026, 9, 1), today) is True
    assert qf.complaint_is_overdue("open", date(2026, 9, 15), today) is False
    assert qf.complaint_is_overdue("closed", date(2026, 1, 1), today) is False
    assert qf.complaint_is_overdue("open", None, today) is False


# ── periodic grouping / trends ────────────────────────────────────

def test_period_key():
    d = date(2026, 9, 24)
    assert qf.period_key(d, "daily") == "2026-09-24"
    assert qf.period_key(d, "weekly") == "2026-W39"
    assert qf.period_key(d, "monthly") == "2026-09"
    assert qf.period_key(d, "quarterly") == "2026-Q3"
    assert qf.period_key(d, "annual") == "2026"
    assert qf.period_key(None, "monthly") == ""


def test_group_stats():
    rows = [
        {"date": date(2026, 9, 1), "product": "NYY 4x16", "parameter": "R", "value": 1.10, "unit": "Ω/km"},
        {"date": date(2026, 9, 9), "product": "NYY 4x16", "parameter": "R", "value": 1.14},
        {"date": date(2026, 10, 2), "product": "NYY 4x16", "parameter": "R", "value": 1.20},
        {"date": date(2026, 9, 3), "product": "NYY 4x16", "parameter": "t", "value": 0.9},
        {"date": date(2026, 9, 3), "product": "NYY 4x16", "parameter": "t", "value": None},   # ignored
    ]
    out = qf.group_stats(rows, "monthly")
    assert [(o["period"], o["parameter"], o["n"]) for o in out] == [("2026-09", "R", 2), ("2026-09", "t", 1), ("2026-10", "R", 1)]
    assert out[0]["mean"] == pytest.approx(1.12) and out[0]["unit"] == "Ω/km"
    assert out[1]["stddev"] == 0.0
    quarterly = qf.group_stats(rows, "quarterly")
    assert [o["period"] for o in quarterly] == ["2026-Q3", "2026-Q3", "2026-Q4"]


def test_trend_series_running_mean():
    ts = qf.trend_series([{"date": date(2026, 9, 3), "value": 3, "ref": "b"},
                          {"date": date(2026, 9, 1), "value": 1, "ref": "a"},
                          {"date": date(2026, 9, 2), "value": "x"}])
    assert [p["value"] for p in ts] == [1, 3]
    assert [p["running_mean"] for p in ts] == [1.0, 2.0]
    assert ts[0]["ref"] == "a"


# ── yield / defects / numbering ───────────────────────────────────

def test_yield_stats():
    y = qf.yield_stats(1000, 950, scrap_kg="12.5", under_length_m=20)
    assert y["loss_m"] == 50 and y["yield_pct"] == pytest.approx(95.0) and y["loss_pct"] == pytest.approx(5.0)
    assert y["scrap_kg"] == 12.5 and y["under_length_m"] == 20
    empty = qf.yield_stats(None, None)
    assert empty["yield_pct"] is None and empty["loss_m"] == 0
    assert qf.yield_stats(100, 120)["loss_m"] == 0      # never negative


def test_defective_rate_and_numbering():
    assert qf.defective_rate(0, 0) is None
    assert qf.defective_rate(200, 5) == pytest.approx(2.5)
    assert qf.format_number("COA", 2026, 1) == "COA-2026-000001"
    assert qf.format_number("CAPA", 2026, 123456) == "CAPA-2026-123456"


def test_to_num_and_to_date():
    assert qf.to_num("") is None and qf.to_num(None) is None and qf.to_num("1e400") is None
    assert qf.to_num(Decimal("2.5")) == 2.5 and qf.to_num(" 3 ") == 3.0
    assert qf.to_date("2026-09-24T10:00:00") == date(2026, 9, 24)
    assert qf.to_date("") is None and qf.to_date("nope") is None


# ── wiring contract ───────────────────────────────────────────────

def test_modules_import_and_expose_contract():
    store = importlib.import_module("quality_data_store")
    jobs = importlib.import_module("quality_jobs")
    routes = importlib.import_module("quality_routes")
    assert callable(store.ensure_schema)
    for name in ("latest_rm_result", "inspections_for_order", "open_quality_issues", "record_procurement_sample_result"):
        assert callable(getattr(store, name)), name
    assert set(inspect.signature(store.latest_rm_result).parameters) == {"company_id", "material_code", "lot_no"}
    assert set(inspect.signature(store.inspections_for_order).parameters) == {"company_id", "order_number"}
    paths = [getattr(r, "path", "") for r in routes.router.routes]
    assert paths and all(p.startswith("/quality") for p in paths)
    names = [getattr(r, "name", "") for r in routes.router.routes]
    assert all(n.startswith("quality_") for n in names), [n for n in names if not n.startswith("quality_")]
    assert len(names) == len(set(names)), "duplicate route names"

    class _FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger=None, *a, **kw):
            self.jobs.append((func, trigger, kw.get("id")))

    sched = _FakeScheduler()
    jobs.register_jobs(sched)
    assert sched.jobs and callable(sched.jobs[0][0]) and sched.jobs[0][2] == jobs.JOB_ID
    assert str(sched.jobs[0][1]).find("hour='7'") >= 0


def test_static_routes_registered_before_dynamic():
    routes = importlib.import_module("quality_routes")
    paths = [getattr(r, "path", "") for r in routes.router.routes]
    for static, dynamic in (("/quality/specs/new", "/quality/specs/{spec_id}"),
                            ("/quality/inspections/{kind}/new", "/quality/inspections/{kind}/{inspection_id}"),
                            ("/quality/inspections/{kind}/export.xlsx", "/quality/inspections/{kind}/{inspection_id}"),
                            ("/quality/calibration/new", "/quality/calibration/{equipment_id}"),
                            ("/quality/capa/reminders", "/quality/capa/{capa_id}"),
                            ("/quality/ncr/new", "/quality/ncr/{ncr_id}"),
                            ("/quality/audits/new", "/quality/audits/{audit_id}")):
        assert paths.index(static) < paths.index(dynamic), (static, dynamic)


def test_schema_covers_every_inspection_kind_and_table():
    store = importlib.import_module("quality_data_store")
    ddl = store._SCHEMA
    for cfg in qf.INSPECTION_KINDS.values():
        assert f"CREATE TABLE IF NOT EXISTS {cfg['table']}" in ddl
        for f in cfg["fields"]:
            assert f["name"] in ddl, f["name"]
    for t in ("quality_sequences", "quality_spec_sets", "quality_spec_params", "quality_inspection_lines",
              "quality_equipment", "quality_calibration_records", "quality_complaints", "quality_capa",
              "quality_ncrs", "quality_audits", "quality_audit_checklist"):
        assert f"CREATE TABLE IF NOT EXISTS {t}" in ddl, t
    assert ddl.count("company_id") >= 17


def test_overall_kind_specific_rules():
    store = importlib.import_module("quality_data_store")
    ov = store.QualityDataStore._overall
    assert ov("final", {"test_result": "fail"}, [{"result": "pass"}]) == "fail"
    assert ov("final", {"test_result": "pass"}, []) == "pass"
    assert ov("insulation", {"product_defect": "reject"}, [{"result": "pass"}]) == "fail"
    assert ov("rm", {"disposition": "reject"}, [{"result": "pass"}]) == "conditional"
    assert ov("rm", {"disposition": "accept"}, [{"result": "pass"}]) == "pass"
    assert ov("packing", {}, []) == "na"


def test_xlsx_export_tolerates_sheet_title_characters(tmp_path):
    """openpyxl rejects / \\ * ? : [ ] in sheet names — 'AAC / ABC Delivery Report' must still export."""
    routes = importlib.import_module("quality_routes")
    from openpyxl import load_workbook
    resp = routes._xlsx("AAC / ABC Delivery Report: [x]?*", ["A", "B"], [[1, Decimal("2.5")], [date(2026, 9, 24), None]],
                        "t.xlsx", subtitle="sub")
    wb = load_workbook(resp.path)
    assert wb.active.title.startswith("AAC - ABC Delivery Report") and len(wb.active.title) <= 31
    assert not set(wb.active.title) & set("/\\*?:[]")
    assert wb.active["A1"].value.startswith("AAC / ABC")
    assert wb.active["B5"].value == 2.5


def test_i18n_catalogue_covers_module_strings():
    cat = importlib.import_module("i18n_catalogue_quality")
    assert isinstance(cat.AM, dict) and len(cat.AM) > 300
    for cfg in qf.INSPECTION_KINDS.values():
        assert cfg["label"] in cat.AM, cfg["label"]
        for f in cfg["fields"]:
            assert f["label"] in cat.AM, f["label"]
    assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in cat.AM.items())
