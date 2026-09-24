"""
Pure-logic tests — Manufacturing module (no database).
"""
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import manufacturing_data_store as m  # noqa: E402

D = Decimal


def test_format_order_no_is_gapless_style():
    assert m.format_order_no(2026, 123) == "MO-2026-000123"
    assert m.format_order_no("2026", 1) == "MO-2026-000001"


def test_yield_and_utilisation_and_variance():
    assert m.yield_pct(950, 1000) == D("95.00")
    assert m.yield_pct(10, 0) == D(0)
    assert m.utilisation_pct(6, 8) == D("75.00")
    assert m.utilisation_pct(1, 0) == D(0)
    diff, pct = m.variance(100, 120)
    assert diff == D(20) and pct == D("20.00")
    assert m.variance(0, 5) == (D(5), D(0))


def test_params_parse_and_roundtrip():
    p = m.parse_params("die = 8.2\nnipple: 6.5\n# comment\nbad line\nZone Temperature=180")
    assert p == {"die": "8.2", "nipple": "6.5", "zone_temperature": "180"}
    text = m.params_text(p)
    assert m.parse_params(text) == p
    assert m.params_text('{"a": 1}') == "a=1"
    assert m.params_text(None) == ""


def test_params_from_form_merges_fixed_and_extra():
    form = {"p_die": "8.2", "p_nipple": "", "extra_params": "lay_length=120", "unrelated": "x"}
    assert m.params_from_form(form, ("die", "nipple")) == {"die": "8.2", "lay_length": "120"}


def _boms():
    return {
        "fg": {"output_qty": D(1000), "lines": [
            {"material_code": "PVC", "material_name": "PVC compound", "unit": "kg", "qty_per_output": D("20"), "scrap_pct": D("5")},
            {"material_code": "", "material_name": "Cond", "unit": "m", "qty_per_output": D("1000"), "scrap_pct": D(0),
             "is_semi_finished": True, "component_product_id": "sf"},
        ]},
        "sf": {"output_qty": D(1000), "lines": [
            {"material_code": "CU-8", "material_name": "Copper rod", "unit": "kg", "qty_per_output": D("143"), "scrap_pct": D("2")},
        ]},
    }


def test_explode_bom_recurses_into_semi_finished_and_applies_scrap():
    lines = m.explode_bom([{"product_id": "fg", "planned_qty": 5000}], _boms(), {"CU-8": 200})
    by = {l["material_code"]: l for l in lines}
    assert by["PVC"]["required_qty"] == D("105.000")            # 5 × 20 × 1.05
    assert by["CU-8"]["required_qty"] == D("729.300")           # 5 × 143 × 1.02
    assert by["CU-8"]["on_hand_qty"] == D("200.000")
    assert by["CU-8"]["to_procure_qty"] == D("529.300")
    assert by["PVC"]["to_procure_qty"] == D("105.000")
    assert [l["material_code"] for l in lines] == ["CU-8", "PVC"]


def test_explode_bom_ignores_products_without_bom():
    assert m.explode_bom([{"product_id": "nope", "planned_qty": 10}], _boms()) == []


def test_planned_costs():
    bom = _boms()["sf"]
    assert m.planned_material_cost(bom, 2000, {"CU-8": D("900")}) == D("262548.00")   # 2×143×1.02×900
    routing = {"ops": [{"work_center_id": "w1", "std_setup_minutes": 30, "std_run_minutes_per_unit": D("0.06")},
                       {"work_center_id": "w2", "std_setup_minutes": 0, "std_run_minutes_per_unit": D("0.03")}]}
    # w1: (30 + 120)/60 = 2.5h × 200 = 500 ; w2: 60/60 = 1h × 100 = 100
    assert m.planned_conversion_cost(routing, 2000, {"w1": 200, "w2": 100}) == D("600.00")
    assert m.routing_hours(routing, 2000) == D("3.50")
    assert m.planned_material_cost(None, 1, {}) == 0 and m.planned_conversion_cost(None, 1, {}) == 0


def test_shift_hours_including_overnight():
    assert m.shift_hours(time(6, 0), time(14, 0)) == D("8.00")
    assert m.shift_hours("22:00", "06:00") == D("8.00")
    assert m.shift_hours(None, "06:00") == D(0)


def test_available_hours_uses_calendar_and_shifts():
    start, end = date(2026, 3, 2), date(2026, 3, 8)          # 7 days
    shifts = [{"start_time": "06:00", "end_time": "14:00", "work_center_id": None, "is_active": True},
              {"start_time": "14:00", "end_time": "22:00", "work_center_id": "w1", "is_active": True},
              {"start_time": "22:00", "end_time": "06:00", "work_center_id": "w2", "is_active": True}]
    holidays = [date(2026, 3, 2)]
    assert m.available_hours(start, end, holidays, shifts, "w1") == D("96.00")   # 6 days × 16h
    assert m.available_hours(start, end, holidays, shifts, "w9") == D("48.00")   # 6 × 8 (global shift only)
    assert m.available_hours(start, end, [], [], "w1") == D("56.00")             # 7 × default 8h
    assert m.available_hours(end, start, [], [], None) == D(0)


def test_pivot_materials():
    rows = [{"order_no": "MO-1", "material_code": "CU", "issued": 10, "consumed": 8, "returned": 1, "drum_refs": "D1"},
            {"order_no": "MO-1", "material_code": "PVC", "issued": 5, "consumed": 5, "returned": 0, "drum_refs": "D1"},
            {"order_no": "MO-2", "material_code": "CU", "issued": 3, "consumed": 0, "returned": 0, "drum_refs": None}]
    mats, out = m.pivot_materials(rows, ("order_no",))
    assert mats == ["CU", "PVC"]
    r1 = next(r for r in out if r["order_no"] == "MO-1")
    assert r1["CU__issued"] == 10 and r1["PVC__consumed"] == 5 and r1["total_issued"] == 15 and r1["drum_refs"] == "D1"
    r2 = next(r for r in out if r["order_no"] == "MO-2")
    assert r2["total_issued"] == 3 and "PVC__issued" not in r2


def test_kpi_row_prefers_labour_hours_and_caps_at_available():
    k = m.kpi_row(1000, 900, 50, 16, 120, 0)
    assert k["run_hours"] == D("14.00") and k["utilisation_pct"] == D("87.50") and k["yield_pct"] == D("90.00")
    k2 = m.kpi_row(0, 0, 0, 8, 0, 12)
    assert k2["run_hours"] == D("8.00") and k2["utilisation_pct"] == D("100.00")
    k3 = m.kpi_row(0, 0, 0, 0, 30, 0)
    assert k3["run_hours"] == 0 and k3["utilisation_pct"] == 0


def test_order_is_past_due():
    today = date(2026, 9, 24)
    assert m.order_is_past_due({"status": "released", "delivery_date": date(2026, 9, 1)}, today)
    assert not m.order_is_past_due({"status": "closed", "delivery_date": date(2026, 9, 1)}, today)
    assert not m.order_is_past_due({"status": "planned", "delivery_date": None, "planned_end": None}, today)
    assert m.order_is_past_due({"status": "planned", "planned_end": datetime(2026, 9, 2, 8)}, today)


def test_process_steps_and_status_merge():
    assert len(m.PROCESS_STEPS) == 16
    assert [s["seq"] for s in m.PROCESS_STEPS] == list(range(1, 17))
    out = m.build_process_status(m.PROCESS_STEPS, {"release": {"status": "done", "detail": "x", "url": "/u"}})
    rel = next(s for s in out if s["key"] == "release")
    assert rel["status"] == "done" and rel["link"] == "/u"
    assert all(s["status"] == "not_started" for s in out if s["key"] != "release")
    assert all(s["link"].startswith("/") for s in out)


def test_lenient_converters():
    assert m.D("") == 0 and m.D(None) == 0 and m.D("abc") == 0 and m.D("1.5") == D("1.5")
    assert m._opt("") is None and m._opt("x") == "x"
    assert m._int("", 7) == 7 and m._int("12") == 12 and m._int("x") == 0
    assert m._bool("on") and m._bool("1") and not m._bool("") and not m._bool(None)


def test_public_api_surface():
    for name in ("create_order_from_sales_order", "get_order_by_source", "product_by_code", "active_bom_for",
                 "approved_tds_for", "order_status_summary", "ensure_schema", "manufacturing_store"):
        assert hasattr(m, name), name
    import inspect
    sig = inspect.signature(m.manufacturing_store.create_order_from_sales_order)
    assert {"source_ref", "customer_name", "product_code", "qty", "unit", "cutting_length", "packing",
            "delivery_date", "prepared_by"} <= set(sig.parameters)


def test_jobs_register_without_side_effects():
    import manufacturing_jobs as jobs

    class _Sched:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger=None, *a, **kw):
            self.jobs.append((func, trigger, kw.get("id")))

    s = _Sched()
    jobs.register_jobs(s)
    ids = {j[2] for j in s.jobs}
    assert ids == {jobs.KPI_JOB_ID, jobs.PAST_DUE_JOB_ID}
    assert all(callable(j[0]) for j in s.jobs)


def test_routes_are_prefixed_and_static_before_param():
    import manufacturing_routes as r
    paths = [getattr(x, "path", "") for x in r.router.routes]
    assert paths and all(p.startswith("/manufacturing") for p in paths)
    names = [getattr(x, "name", "") for x in r.router.routes]
    assert all(n.startswith("manufacturing_") for n in names)
    assert len(names) == len(set(names)), "duplicate route names"
    assert paths.index("/manufacturing/orders/new") < paths.index("/manufacturing/orders/{order_id}")
    assert paths.index("/manufacturing/products/new") < paths.index("/manufacturing/products/{product_id}")
    assert paths.index("/manufacturing/process-map") < paths.index("/manufacturing/orders/{order_id}")
