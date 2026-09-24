"""
Template render tests — Manufacturing module.

Renders every manufacturing/ template with route-accurate contexts, EMPTY and
POPULATED, using the same stub harness as test_fixed_assets_templates.py.
No database required.
"""
import sys
from datetime import date, datetime, time
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

import i18n  # noqa: E402
i18n.install()  # `_()` and `|et_date` on jinja2 defaults

import manufacturing_data_store as m  # noqa: E402
from manufacturing_routes import REPORTS  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))
D = Decimal
TODAY = date(2026, 9, 24)


def _base_ctx(path="/manufacturing/x"):
    request = SimpleNamespace(url=SimpleNamespace(path=path, query=""),
                              query_params=SimpleNamespace(get=lambda k, d=None: d),
                              session={}, form=SimpleNamespace())
    return dict(request=request, session={"username": "tester"}, url_for=lambda *a, **k: "#",
                csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
                static_url=lambda p: p, static_cdn_url="", app_version="1.0",
                current_company_id="default", current_tenant=None,
                active_page="dashboard", order_statuses=m.ORDER_STATUSES, order_types=m.ORDER_TYPES,
                wc_types=m.WC_TYPES, capacity_units=m.CAPACITY_UNITS, product_units=m.PRODUCT_UNITS,
                product_types=m.PRODUCT_TYPES, machine_statuses=m.MACHINE_STATUSES, calendar_kinds=m.CALENDAR_KINDS,
                plan_periods=m.PLAN_PERIODS, plan_basis=m.PLAN_BASIS, issue_kinds=m.ISSUE_KINDS,
                tds_fields=m.TDS_FIELDS, process_fields=m.PROCESS_FIELDS, params_text=m.params_text, today=TODAY)


# ── fixtures ────────────────────────────────────────────────────────
PLANT = dict(id="pl1", company_id="default", code="P1", name="Addis plant", location="Addis Ababa",
             product_group="Power cables", is_active=True, created_at=datetime(2026, 1, 1))
WC = dict(id="wc1", company_id="default", plant_id="pl1", plant_name="Addis plant", code="EXT1", name="Extrusion line 1",
          type="insulation", std_capacity_per_hour=D("250"), capacity_unit="kg", cost_rate_per_hour=D("450"), is_active=True)
MACHINE = dict(id="m1", company_id="default", work_center_id="wc1", work_center_name="Extrusion line 1", work_center_code="EXT1",
               code="EXT-01", name="Extruder 90mm", manufacturer="Maillefer", model="MX90", install_date=date(2020, 5, 1),
               std_capacity_per_hour=D("240"), status="active")
PRODUCT = dict(id="pr1", company_id="default", code="NYY-4x16", name="NYY 4×16 mm² black", description="", product_type="finished",
               size_mm2=D("16"), color="black", unit="m", std_length_per_roll=D("500"), sku="NYY416", inventory_item_id=None,
               is_active=True, active_boms=1, approved_tds=1, active_routings=1, created_at=datetime(2026, 1, 1))
SEMI = dict(PRODUCT, id="pr2", code="CU-STR-16", name="Stranded conductor 16", product_type="semi_finished", active_boms=0,
            approved_tds=0, active_routings=0)
TDS = dict(id="t1", company_id="default", product_id="pr1", version=2, status="approved",
           parameters={"conductor_diameter_mm": "4.8", "sheath_thickness_mm": "1.8", "standard": "IEC 60502"},
           notes="", approved_by="qa", approved_at=datetime(2026, 2, 1, 10), file_doc_id=None, created_by="eng", created_at=datetime(2026, 1, 20))
BOM_LINE = dict(id="bl1", bom_id="b1", component_product_id=None, material_name="PVC compound", material_code="PVC-ST5",
                qty_per_output=D("20"), unit="kg", scrap_pct=D("5"), is_semi_finished=False, work_center_id="wc1",
                work_center_name="Extrusion line 1", component_code=None, operation_seq=30)
BOM = dict(id="b1", company_id="default", product_id="pr1", tds_id="t1", version=1, status="active", output_qty=D("1000"),
           output_unit="m", notes="", line_count=1, created_at=datetime(2026, 1, 1), lines=[BOM_LINE])
ROUTING_OP = dict(id="ro1", routing_id="r1", seq=10, work_center_id="wc1", work_center_name="Extrusion line 1",
                  operation_name="Insulation", std_setup_minutes=D("30"), std_run_minutes_per_unit=D("0.06"),
                  process_params={"die": "8.2", "zone_temperature": "180"}, cost_rate_per_hour=D("450"))
ROUTING = dict(id="r1", company_id="default", product_id="pr1", version=1, status="active", notes="", op_count=1,
               created_at=datetime(2026, 1, 1), ops=[ROUTING_OP])
SHIFT = dict(id="s1", company_id="default", work_center_id=None, work_center_name=None, name="Morning", start_time=time(6, 0),
             end_time=time(14, 0), shift_leader="A. Bekele", line_supervisor="T. Alemu", is_active=True)
CAL = dict(id="c1", company_id="default", plant_id=None, plant_name=None, date=date(2026, 9, 11), kind="holiday", description="Enkutatash")
CAT = dict(id="dc1", company_id="default", name="Mechanical", is_active=True, reasons=[dict(id="dr1", category_id="dc1", name="Bearing failure")])
SCRAP_T = dict(id="st1", company_id="default", name="Copper")
COST = dict(company_id="default", material_code="PVC-ST5", material_name="PVC compound", unit="kg", std_cost=D("85.5"), currency="ETB")
PLAN_LINE = dict(id="pll1", plan_id="pp1", product_id="pr1", product_code="NYY-4x16", product_name="NYY 4×16", size_mm2=D(16),
                 work_center_id="wc1", work_center_name="Extrusion line 1", planned_qty=D("20000"), unit="m", planned_hours=D("120"),
                 basis="demand", actual_qty=D("15000"), variance=D("-5000"), variance_pct=D("-25.00"))
PLAN = dict(id="pp1", company_id="default", plant_id="pl1", plant_name="Addis plant", period_type="monthly", period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30), status="draft", notes="Sept plan", created_by="planner", created_at=datetime(2026, 8, 20),
            line_count=1, total_qty=D("20000"), lines=[PLAN_LINE])
CAP_ROW = dict(work_center_id="wc1", work_center="Extrusion line 1", code="EXT1", type="insulation", available_hours=D("176"),
               planned_hours=D("120"), utilisation_pct=D("68.18"), std_capacity_per_hour=D("250"), capacity_unit="kg", capacity_qty=D("44000"))
CAP_SAVED = dict(id="cp1", work_center_id="wc1", work_center_code="EXT1", work_center_name="Extrusion line 1", period_start=date(2026, 9, 1),
                 period_end=date(2026, 9, 30), available_hours=D("176"), planned_hours=D("120"), utilisation_pct=D("68.18"), notes="")
RM_LINE = dict(id="rml1", plan_id="rm1", material_code="PVC-ST5", material_name="PVC compound", unit="kg", required_qty=D("2100"),
               on_hand_qty=D("500"), to_procure_qty=D("1600"))
RM_PLAN = dict(id="rm1", company_id="default", year=2026, production_plan_id="pp1", status="draft", prepared_by="planner", notes="",
               store_req_ids="", purchase_req_id=None, approval_request_id=None, submitted_at=None, approved_at=None, approved_by=None,
               created_at=datetime(2026, 1, 5), line_count=1, total_to_procure=D("1600"), lines=[RM_LINE])
ORDER = dict(id="o1", company_id="default", order_no="MO-2026-000001", product_id="pr1", product_code="NYY-4x16", product_name="NYY 4×16 mm² black",
             size_mm2=D(16), color="black", product_type="finished", std_length_per_roll=D(500), tds_id="t1", bom_id="b1", routing_id="r1",
             plant_id="pl1", plant_name="Addis plant", order_type="make_to_order", source_ref="SO-2026-0007", customer_name="EEU",
             qty_ordered=D("5000"), unit="m", cutting_length=D("500"), packing="wooden drum", delivery_date=date(2026, 10, 15),
             priority="high", status="in_progress", planned_start=date(2026, 9, 20), planned_end=date(2026, 10, 5),
             actual_start=datetime(2026, 9, 21, 7), actual_end=None, qty_produced=D("3200"), qty_scrap=D("40"), planned_cost=D("125000"),
             actual_cost=D("98000"), prepared_by="planner", checked_by="", approved_by="mgr", approval_request_id=None, approval_status="none",
             gl_status="", gl_entry_id=None, notes="Rush order", released_at=datetime(2026, 9, 20, 9), confirmed_at=None, closed_at=None,
             past_due=False, created_at=datetime(2026, 9, 18, 8), updated_at=datetime(2026, 9, 21))
OP = dict(id="op1", order_id="o1", seq=10, work_center_id="wc1", work_center_name="Extrusion line 1", machine_id="m1", machine_name="Extruder 90mm",
          operation_name="Insulation", process_params={"die": "8.2"}, planned_qty=D(5000), status="running", started_at=datetime(2026, 9, 21), finished_at=None)
MAT = dict(id="om1", order_id="o1", material_code="PVC-ST5", material_name="PVC compound", unit="kg", planned_qty=D("105"), issued_qty=D("100"),
           consumed_qty=D("60"), returned_qty=D("0"), lot_no="L-77")
LOG = dict(id="lg1", company_id="default", order_id="o1", order_no="MO-2026-000001", product_code="NYY-4x16", product_name="NYY", size_mm2=D(16),
           operation_id="op1", machine_id="m1", machine_name="Extruder 90mm", shift_id="s1", shift_name="Morning", log_date=date(2026, 9, 22),
           hour_slot=9, input_qty=D("120"), input_unit="kg", output_qty=D("112"), output_unit="kg", output_rolls=3, under_length_rolls=1,
           length_m=D("1500"), weight_kg=D("112"), scrap_qty=D("4"), scrap_unit="kg", scrap_type_id="st1", scrap_type_name="Copper",
           rework_qty=D(0), lot_no="L-77", drum_no="D-12", operator="K. Tesfaye", remarks="", created_at=datetime(2026, 9, 22, 10))
ISSUE = dict(id="is1", company_id="default", order_id="o1", material_code="PVC-ST5", material_name="PVC compound", qty=D("60"), unit="kg",
             kind="consumption", lot_no="L-77", store_ref="SR-1", drum_ref="D-12", unit_cost=D("85.5"), gl_status="posted", gl_entry_id="je1",
             created_by="store", created_at=datetime(2026, 9, 22))
LABOR = dict(id="lb1", company_id="default", order_id="o1", work_center_id="wc1", work_center_name="Extrusion line 1", shift_id="s1",
             date=date(2026, 9, 22), workers=4, hours=D("8"), setup_hours=D("1"), run_hours=D("7"))
DT = dict(id="dt1", company_id="default", machine_id="m1", machine_name="Extruder 90mm", work_center_id="wc1", work_center_name="Extrusion line 1",
          order_id="o1", order_no="MO-2026-000001", shift_id="s1", started_at=datetime(2026, 9, 22, 11), ended_at=datetime(2026, 9, 22, 11, 45),
          minutes=D("45"), category_id="dc1", category_name="Mechanical", reason_id="dr1", reason_name="Bearing failure",
          description="Screw bearing", reported_by="K. Tesfaye")
TRANSFER = dict(id="tr1", company_id="default", order_id="o1", product_id="pr1", qty=D("3200"), rolls=7, under_length_rolls=1, length_m=D("3200"),
                weight_kg=D("2400"), from_store="Property Administration", to_store="Market Finished Goods Store", ref_no="FG-MO-2026-000001",
                transferred_by="store", transferred_at=datetime(2026, 9, 23), inventory_movement_ok=True)
EVENT = dict(id="ev1", order_id="o1", event_type="released", note="Released to production", actor="mgr", created_at=datetime(2026, 9, 20, 9))
KPI = dict(id="k1", company_id="default", kpi_date=date(2026, 9, 22), work_center_id="wc1", work_center_name="Extrusion line 1", machine_id="m1",
           machine_name="Extruder 90mm", input_qty=D(120), output_qty=D(112), scrap_qty=D(4), planned_qty=D(0), available_hours=D(8),
           run_hours=D(7), downtime_minutes=D(45), yield_pct=D("93.33"), utilisation_pct=D("87.50"), computed_at=datetime(2026, 9, 23, 6))


def _stats(populated):
    s = {"by_status": {x: 0 for x in m.ORDER_STATUSES}, "past_due": 0, "open_orders": [], "missing_bom": [], "products": 0,
         "machines": 0, "machines_down": 0, "today_output": D(0), "today_scrap": D(0), "month_output": D(0), "month_yield": D(0),
         "pending_release": 0, "recent_logs": [], "kpis": []}
    if populated:
        s.update(by_status={**s["by_status"], "planned": 2, "in_progress": 1}, past_due=1, open_orders=[ORDER, dict(ORDER, id="o2", status="planned", past_due=True)],
                 missing_bom=[SEMI], products=2, machines=3, machines_down=1, today_output=D("112"), today_scrap=D("4"),
                 month_output=D("3200"), month_yield=D("93.33"), pending_release=1, recent_logs=[LOG], kpis=[KPI])
    return s


def _filters():
    return dict(filters={"date_from": "2026-09-01", "date_to": "2026-09-30", "plant_id": "", "work_center_id": "", "machine_id": "", "group": ""},
                plants=[PLANT], work_centers=[WC], machines=[MACHINE])


def _report_ctx(name, populated):
    cols = [("order_no", "Order", "text"), ("size_mm2", "Size (mm²)", "num"), ("transferred_at", "Date", "date"),
            ("rolls", "Rolls", "int"), ("ratio_pct", "Output/input %", "pct")]
    rows = [dict(order_no="MO-1", size_mm2=D(16), transferred_at=datetime(2026, 9, 23), rolls=7, ratio_pct=D("93.33")),
            dict(order_no="MO-2", size_mm2=None, transferred_at=None, rolls=0, ratio_pct=None)] if populated else []
    totals = {"size_mm2": D(16), "rolls": D(7)} if populated else {}
    return dict(report_name=name, report_title=REPORTS[name], columns=cols, rows=rows, totals=totals, generated=TODAY, **_filters())


def _order_detail(status="in_progress", populated=True):
    o = dict(ORDER, status=status, approval_status="pending" if status == "planned" else "none")
    det = dict(operations=[OP], materials=[MAT], logs=[LOG], issues=[ISSUE], labor=[LABOR], downtime=[DT], transfers=[TRANSFER], events=[EVENT]) \
        if populated else dict(operations=[], materials=[], logs=[], issues=[], labor=[], downtime=[], transfers=[], events=[])
    return dict(order=o, cost_variance=D("-27000"), cost_variance_pct=D("-21.60"), yield_pct=D("93.33"), machines=[MACHINE] if populated else [],
                shifts=[SHIFT] if populated else [], work_centers=[WC] if populated else [], scrap_types=[SCRAP_T] if populated else [],
                downtime_categories=[CAT] if populated else [], approval={"status": "pending", "current_step_name": "Plant manager"} if status == "planned" else None,
                **det)


CASES = [
    ("manufacturing/dashboard.html", lambda: dict(stats=_stats(True), variances=[dict(code="NYY-4x16", name="NYY", orders=2, planned_cost=D(100), actual_cost=D(120), variance=D(20))])),
    ("manufacturing/dashboard.html", lambda: dict(stats=_stats(False), variances=[])),
    ("manufacturing/process_map.html", lambda: dict(ref="SO-2026-0007", steps=m.build_process_status(m.PROCESS_STEPS, {"release": {"status": "done", "detail": "released"}, "production": {"status": "in_progress", "detail": "3200/5000"}}), recent_orders=[ORDER])),
    ("manufacturing/process_map.html", lambda: dict(ref="", steps=m.build_process_status(m.PROCESS_STEPS, {}), recent_orders=[])),
    ("manufacturing/settings.html", lambda: dict(settings=dict(company_id="default", gl_wip_account="1310", gl_raw_material_account="1300", gl_finished_goods_account="1320", gl_scrap_account="5900", default_plant_id="pl1", mto_auto_release=True), plants=[PLANT], gl_accounts=[dict(account_code="1310", account_name="WIP", account_type="Asset")])),
    ("manufacturing/settings.html", lambda: dict(settings={"company_id": "default"}, plants=[], gl_accounts=[])),
    ("manufacturing/plants.html", lambda: dict(plants=[PLANT, dict(PLANT, id="pl2", is_active=False)], item=PLANT)),
    ("manufacturing/plants.html", lambda: dict(plants=[], item={})),
    ("manufacturing/work_centers.html", lambda: dict(work_centers=[WC], plants=[PLANT], item=WC)),
    ("manufacturing/work_centers.html", lambda: dict(work_centers=[], plants=[], item={})),
    ("manufacturing/machines.html", lambda: dict(machines=[MACHINE, dict(MACHINE, id="m2", status="down")], work_centers=[WC], item=MACHINE)),
    ("manufacturing/machines.html", lambda: dict(machines=[], work_centers=[], item={})),
    ("manufacturing/shifts.html", lambda: dict(shifts=[SHIFT, dict(SHIFT, id="s2", name="Night", start_time=time(22), end_time=time(6), work_center_id="wc1", work_center_name="Extrusion line 1")], work_centers=[WC], item=SHIFT, shift_hours=m.shift_hours)),
    ("manufacturing/shifts.html", lambda: dict(shifts=[], work_centers=[], item={}, shift_hours=m.shift_hours)),
    ("manufacturing/calendar.html", lambda: dict(entries=[CAL, dict(CAL, id="c2", kind="maintenance", plant_name="Addis plant")], year=2026, plants=[PLANT])),
    ("manufacturing/calendar.html", lambda: dict(entries=[], year=2026, plants=[])),
    ("manufacturing/downtime_categories.html", lambda: dict(categories=[CAT, dict(CAT, id="dc2", name="Other", reasons=[])], scrap_types=[SCRAP_T])),
    ("manufacturing/downtime_categories.html", lambda: dict(categories=[], scrap_types=[])),
    ("manufacturing/materials.html", lambda: dict(costs=[COST])),
    ("manufacturing/materials.html", lambda: dict(costs=[])),
    ("manufacturing/products.html", lambda: dict(products=[PRODUCT, SEMI], type_filter="", search="")),
    ("manufacturing/products.html", lambda: dict(products=[], type_filter="finished", search="x")),
    ("manufacturing/product_form.html", lambda: dict(product=PRODUCT, is_edit=True)),
    ("manufacturing/product_form.html", lambda: dict(product={}, is_edit=False)),
    ("manufacturing/product_detail.html", lambda: dict(product=PRODUCT, tds_list=[TDS, dict(TDS, id="t0", version=1, status="superseded"), dict(TDS, id="t3", version=3, status="draft", approved_at=None, approved_by=None)], boms=[BOM, dict(BOM, id="b0", version=0, status="obsolete")], routings=[ROUTING], material_costs=[COST])),
    ("manufacturing/product_detail.html", lambda: dict(product=SEMI, tds_list=[], boms=[], routings=[], material_costs=[])),
    ("manufacturing/bom_detail.html", lambda: dict(bom=BOM, product=PRODUCT, work_centers=[WC], products=[SEMI], tds_list=[TDS], unit_cost=D("1.7955"), costs={"PVC-ST5": D("85.5")})),
    ("manufacturing/bom_detail.html", lambda: dict(bom=dict(BOM, status="draft", lines=[]), product=PRODUCT, work_centers=[], products=[], tds_list=[], unit_cost=D(0), costs={})),
    ("manufacturing/routing_detail.html", lambda: dict(routing=ROUTING, product=PRODUCT, work_centers=[WC], hours_per_1000=D("1.50"))),
    ("manufacturing/routing_detail.html", lambda: dict(routing=dict(ROUTING, status="draft", ops=[]), product=PRODUCT, work_centers=[], hours_per_1000=D(0))),
    ("manufacturing/plans.html", lambda: dict(plans=[PLAN, dict(PLAN, id="pp2", status="approved", period_start=None, period_end=None, plant_name=None)], status_filter="")),
    ("manufacturing/plans.html", lambda: dict(plans=[], status_filter="draft")),
    ("manufacturing/plan_form.html", lambda: dict(plants=[PLANT], plan={})),
    ("manufacturing/plan_form.html", lambda: dict(plants=[], plan={})),
    ("manufacturing/plan_detail.html", lambda: dict(plan=PLAN, lines=[PLAN_LINE], products=[PRODUCT], work_centers=[WC])),
    ("manufacturing/plan_detail.html", lambda: dict(plan=dict(PLAN, status="approved", lines=[]), lines=[], products=[], work_centers=[])),
    ("manufacturing/capacity.html", lambda: dict(rows=[CAP_ROW, dict(CAP_ROW, work_center_id="wc2", utilisation_pct=D("120"))], period_start="2026-09-01", period_end="2026-09-30", plant_id="pl1", plants=[PLANT], work_centers=[WC], saved=[CAP_SAVED])),
    ("manufacturing/capacity.html", lambda: dict(rows=[], period_start="2026-09-01", period_end="2026-09-30", plant_id="", plants=[], work_centers=[], saved=[])),
    ("manufacturing/rm_plans.html", lambda: dict(plans=[RM_PLAN, dict(RM_PLAN, id="rm2", status="approved", store_req_ids="a,b", purchase_req_id="pr-1234567890")], production_plans=[PLAN])),
    ("manufacturing/rm_plans.html", lambda: dict(plans=[], production_plans=[])),
    ("manufacturing/rm_plan_detail.html", lambda: dict(plan=RM_PLAN, costs={"PVC-ST5": D("85.5")}, estimated_value=D("136800"))),
    ("manufacturing/rm_plan_detail.html", lambda: dict(plan=dict(RM_PLAN, status="submitted", lines=[], store_req_ids="x,y", purchase_req_id="pr-1234567890", notes="note"), costs={}, estimated_value=D(0))),
    ("manufacturing/rm_plan_detail.html", lambda: dict(plan=dict(RM_PLAN, status="approved", lines=[]), costs={}, estimated_value=D(0))),
    ("manufacturing/orders.html", lambda: dict(orders=[ORDER, dict(ORDER, id="o2", status="planned", approval_status="pending", past_due=True, delivery_date=None)], status_filter="", type_filter="", search="", past_due_filter=False)),
    ("manufacturing/orders.html", lambda: dict(orders=[], status_filter="closed", type_filter="make_to_order", search="q", past_due_filter=True)),
    ("manufacturing/order_form.html", lambda: dict(order=ORDER, is_edit=True, products=[PRODUCT], plants=[PLANT], tds_list=[TDS], boms=[BOM], routings=[ROUTING])),
    ("manufacturing/order_form.html", lambda: dict(order={"order_type": "make_to_stock", "priority": "normal"}, is_edit=False, products=[PRODUCT, SEMI], plants=[], tds_list=[], boms=[], routings=[])),
    ("manufacturing/order_detail.html", lambda: _order_detail("in_progress", True)),
    ("manufacturing/order_detail.html", lambda: _order_detail("planned", False)),
    ("manufacturing/order_detail.html", lambda: _order_detail("released", True)),
    ("manufacturing/order_detail.html", lambda: dict(_order_detail("confirmed", True), order=dict(ORDER, status="confirmed", confirmed_at=datetime(2026, 9, 23), gl_status="posted", past_due=True))),
    ("manufacturing/order_detail.html", lambda: dict(_order_detail("cancelled", False), order=dict(ORDER, status="cancelled", approval_status="rejected"))),
    ("manufacturing/bom_print.html", lambda: dict(order=ORDER, bom=BOM, tds=TDS, operations=[OP], materials=[MAT])),
    ("manufacturing/bom_print.html", lambda: dict(order=dict(ORDER, bom_id=None, tds_id=None), bom=None, tds=None, operations=[], materials=[])),
    ("manufacturing/bom_print.html", lambda: dict(order=ORDER, bom=None, tds=None, operations=[], materials=[MAT])),
    ("manufacturing/logs.html", lambda: dict(logs=[LOG, dict(LOG, id="lg2", input_qty=D(0), hour_slot=None, shift_name=None, machine_name=None)], open_orders=[ORDER], shifts=[SHIFT], scrap_types=[SCRAP_T], **_filters())),
    ("manufacturing/logs.html", lambda: dict(logs=[], open_orders=[], shifts=[], scrap_types=[], **dict(_filters(), plants=[], work_centers=[], machines=[]))),
    ("manufacturing/downtime.html", lambda: dict(downtime=[DT, dict(DT, id="dt2", category_name=None, machine_name=None, started_at=None, ended_at=None)], categories=[CAT], shifts=[SHIFT], open_orders=[ORDER], **_filters())),
    ("manufacturing/downtime.html", lambda: dict(downtime=[], categories=[], shifts=[], open_orders=[], **_filters())),
    ("manufacturing/reports_index.html", lambda: dict(reports=REPORTS)),
] + [("manufacturing/report.html", (lambda n=n, p=p: _report_ctx(n, p))) for n in REPORTS for p in (True, False)]


@pytest.mark.parametrize("template,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_manufacturing_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000
    assert "module-sidebar" in html


def test_every_template_file_is_covered():
    files = {p.name for p in (_WEB_DIR / "templates" / "manufacturing").glob("*.html") if not p.name.startswith("_")}
    covered = {t.split("/")[1] for t, _ in CASES}
    assert files == covered, files ^ covered


def test_templates_do_not_use_top_level_jquery_ready():
    for tpl in (_WEB_DIR / "templates" / "manufacturing").glob("*.html"):
        assert "$(document).ready" not in tpl.read_text(encoding="utf-8"), tpl.name


def test_process_map_shows_all_16_steps_and_statuses():
    ctx = dict(ref="MO-1", steps=m.build_process_status(m.PROCESS_STEPS, {"release": {"status": "done"}}), recent_orders=[])
    html = env.get_template("manufacturing/process_map.html").render(**_base_ctx(), **ctx)
    assert html.count('<div class="num">') == 16
    assert 'class="done"' in html and "not started / module not installed" in html


def test_report_renders_pivot_columns_and_totals():
    html = env.get_template("manufacturing/report.html").render(**_base_ctx(), **_report_ctx("rm-status", True))
    assert "Output/input %" in html and "93.33" in html and "Total" in html and "16.00" in html


def test_amharic_catalogue_covers_sidebar_labels():
    from i18n_catalogue_manufacturing import AM
    for key in ("Production Orders", "Shop-floor Logs", "Process Map", "Raw Material Plans", "Work Centres", "Machines",
                "Factory Calendar", "Material Costs", "Release", "Confirm", "not started / module not installed"):
        assert key in AM and AM[key], key
    html = env.get_template("manufacturing/dashboard.html").render(**_base_ctx(), current_lang="am", stats=_stats(False), variances=[])
    assert AM["Production Orders"] in html
