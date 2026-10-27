"""
Template render tests — Security Operations module.

Renders every secops/ template with route-accurate contexts, both EMPTY and
POPULATED, so undefined variables / wrong field names fail the build instead
of 500ing in production. No database required.
"""
import json
import re
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

import secops_logic as L  # noqa: E402
from secops_routes import REPORTS  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))

TODAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 7, 12)


def _base_ctx(path="/secops/x"):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(),
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
        active_page="dashboard", grades=L.GRADES, site_types=L.SITE_TYPES, site_statuses=L.SITE_STATUSES,
        coverages=L.COVERAGES, guard_statuses=L.GUARD_STATUSES, roster_statuses=L.ROSTER_STATUSES,
        attendance_statuses=L.ATTENDANCE_STATUSES, kit_kinds=L.KIT_KINDS, kit_statuses=L.KIT_STATUSES,
        incident_categories=L.INCIDENT_CATEGORIES, severities=L.SEVERITIES, incident_statuses=L.INCIDENT_STATUSES,
        training_levels=L.TRAINING_LEVELS, today=TODAY,
    )


# ── fixtures ──────────────────────────────────────────────────────
def _client(**kw):
    c = dict(id="cl-1", company_id="default", name="Awash Bank", contact="Ato Kebede", phone="0911", email="ops@awash.et",
             address="Bole", contract_id="ct-1", customer_id=None, billing_rate_per_guard_hour=Decimal("85"), is_active=True,
             notes="", active_sites=2, created_at=NOW, updated_at=NOW)
    c.update(kw); return c


def _site(**kw):
    s = dict(id="site-1", company_id="default", client_id="cl-1", client_name="Awash Bank", code="S-001", name="Bole Branch",
             address="Bole Rd", region="Addis Ababa", gps_lat=Decimal("8.99"), gps_lng=Decimal("38.78"), site_type="bank",
             supervisor_employee_id="EMP002", supervisor_name="W/ro Sara", start_date=date(2026, 1, 1), end_date=None,
             status="active", instructions="Check IDs at the gate.", required_guard_grade="guard", post_count=2,
             guards_required=3, billing_rate_per_guard_hour=Decimal("85"), created_at=NOW, updated_at=NOW)
    s.update(kw); return s


def _post(**kw):
    p = dict(id="post-1", company_id="default", site_id="site-1", site_name="Bole Branch", name="Main gate", coverage="24h",
             hours_per_day=Decimal("24"), guards_required=2, armed=True, grade_required="guard", is_active=True, created_at=NOW)
    p.update(kw); return p


def _shift(**kw):
    s = dict(id="sh-day", company_id="default", name="Day 06-18", start_time=time(6, 0), end_time=time(18, 0), is_night=False,
             break_minutes=0, is_active=True, sort_order=1, hours=Decimal("12"))
    s.update(kw); return s


def _night():
    return _shift(id="sh-night", name="Night 18-06", start_time=time(18, 0), end_time=time(6, 0), is_night=True, sort_order=2)


def _guard(**kw):
    g = dict(id="g-1", company_id="default", employee_id="EMP001", guard_no="G-0001", grade="guard", licence_no="LIC-77",
             licence_expiry=date(2026, 11, 10), firearm_permit_no="FP-1", firearm_permit_expiry=date(2027, 1, 1),
             medical_fitness_expiry=None, training_level="basic", home_site_id="site-1", home_site_name="Bole Branch",
             status="active", notes="", name="Abebe Kebede", email="abebe@x.et", phone_number="0911", department="Ops",
             position="Guard", basic_salary=8000.0, work_days_per_month=26, work_hours_per_day=8, hire_date=date(2025, 3, 1),
             employee_active=True, created_at=NOW, updated_at=NOW)
    g.update(kw)
    g["expiring"] = L.expiring_documents(g, TODAY)
    g["document_status"] = L.worst_document_status(g, TODAY)
    return g


def _roster(**kw):
    r = dict(id="r-1", company_id="default", site_id="site-1", site_name="Bole Branch", client_name="Awash Bank",
             period_start=date(2026, 10, 26), period_end=date(2026, 11, 1), status="draft", created_by="admin",
             published_at=None, assignment_count=3, created_at=NOW, updated_at=NOW)
    r.update(kw); return r


def _assignment(**kw):
    a = dict(id="a-1", company_id="default", roster_id="r-1", post_id="post-1", guard_id="g-1", shift_template_id="sh-day",
             duty_date=date(2026, 10, 26), start_at=datetime(2026, 10, 26, 6), end_at=datetime(2026, 10, 26, 18), is_relief=False,
             status="scheduled", notes="", created_at=NOW, post_name="Main gate", site_id="site-1", site_name="Bole Branch",
             guard_name="Abebe Kebede", guard_no="G-0001", grade="guard", shift_name="Day 06-18", start_time=time(6, 0),
             end_time=time(18, 0), is_night=False, break_minutes=0, roster_status="published")
    a.update(kw); return a


def _attendance(**kw):
    a = dict(id="att-1", company_id="default", assignment_id="a-1", guard_id="g-1", site_id="site-1", post_id="post-1",
             duty_date=date(2026, 10, 26), check_in_at=datetime(2026, 10, 26, 6, 5), check_out_at=datetime(2026, 10, 26, 18, 10),
             status="present", hours_worked=Decimal("12.08"), night_hours=Decimal("0"), overtime_hours=Decimal("4.08"),
             late_minutes=0, source="self", gps_lat=Decimal("8.99"), gps_lng=Decimal("38.78"), note="", recorded_by="abebe",
             created_at=NOW, updated_at=NOW, guard_name="Abebe Kebede", guard_no="G-0001", site_name="Bole Branch",
             post_name="Main gate", shift_name="Day 06-18", start_time=time(6, 0), end_time=time(18, 0))
    a.update(kw); return a


def _kit_item(**kw):
    k = dict(id="k-1", company_id="default", name="Uniform shirt", kind="uniform", inventory_item_id="inv-1", tracks_serial=False,
             sizes="S,M,L", is_active=True, created_at=NOW, outstanding=2)
    k.update(kw); return k


def _kit_issue(**kw):
    i = dict(id="ki-1", company_id="default", kit_item_id="k-1", guard_id="g-1", site_id=None, serial_no="", size="M",
             qty=Decimal("2"), issued_at=NOW, issued_by="stores", expected_return=date(2026, 12, 1), returned_at=None,
             condition_out="good", condition_in="", status="issued", client_owned=False, notes="", item_name="Uniform shirt",
             kind="uniform", inventory_item_id="inv-1", guard_name="Abebe Kebede", guard_no="G-0001", site_name=None)
    i.update(kw); return i


def _incident(**kw):
    i = dict(id="inc-1", company_id="default", incident_no="INC-2026-000001", site_id="site-1", post_id="post-1", guard_id="g-1",
             occurred_at=datetime(2026, 10, 25, 23, 40), category="intrusion", severity="high", description="Fence breached",
             actions_taken="Police called", reported_by="abebe", reported_to_police=True, police_ref="PR-9",
             client_notified_at=None, client_notification_note="", status="open", closed_at=None, created_at=NOW, updated_at=NOW,
             site_name="Bole Branch", client_name="Awash Bank", client_email="ops@awash.et", post_name="Main gate",
             guard_name="Abebe Kebede", guard_no="G-0001")
    i.update(kw); return i


def _feed_row(**kw):
    r = dict(id="f-1", company_id="default", period="2026-09", guard_id="g-1", employee_id="EMP001", days_present=24,
             days_absent=1, hours_worked=Decimal("288"), night_hours=Decimal("96"), overtime_hours=Decimal("80"),
             night_allowance=Decimal("960"), overtime_pay=Decimal("4615.38"), hourly_rate=Decimal("38.46"), generated_at=NOW,
             status="draft", name="Abebe Kebede", guard_no="G-0001", basic_salary=8000.0)
    r.update(kw); return r


def _settings():
    s = dict(L.DEFAULT_SETTINGS)
    s.update(company_id="default", night_allowance_per_hour=Decimal("10"))
    return s


def _lookups(populated=True):
    if not populated:
        return {"contracts": [], "customers": [], "employees": [], "inventory_items": []}
    return {"contracts": [{"id": "ct-1", "title": "Guarding 2026", "party_name": "Awash Bank", "status": "active"}],
            "customers": [{"id": "cc-1", "code": "C001", "name": "Awash Bank", "contact_person": "", "phone": "", "email": "", "address": ""}],
            "employees": [{"employee_id": "EMP002", "name": "W/ro Sara", "department": "Ops", "position": "Supervisor"}],
            "inventory_items": [{"id": "inv-1", "sku": "UNI-01", "name": "Uniform shirt", "current_stock": 40, "unit": "pcs"}]}


def _coverage(populated):
    if not populated:
        return {"day": TODAY, "sites": [], "sites_total": 0, "sites_covered": 0, "posts_uncovered": 0, "guards_required": 0,
                "guards_assigned": 0, "guards_present": 0, "absent": 0, "late": 0, "on_duty_now": 0}
    site = {"site_id": "site-1", "site_name": "Bole Branch", "client_name": "Awash Bank", "required": 4, "assigned": 3, "present": 2,
            "uncovered": 1, "gaps": [], "coverage_pct": Decimal("75"), "roster_id": "r-1", "roster_status": "published", "absent": 1, "late": 0}
    site2 = dict(site, site_id="site-2", site_name="HQ", required=2, assigned=2, present=2, uncovered=0, coverage_pct=Decimal("100"),
                 roster_id=None, roster_status=None, absent=0)
    return {"day": TODAY, "sites": [site, site2], "sites_total": 2, "sites_covered": 1, "posts_uncovered": 1, "guards_required": 6,
            "guards_assigned": 5, "guards_present": 4, "absent": 1, "late": 0, "on_duty_now": 4}


def _dashboard_ctx(populated):
    cov = _coverage(populated)
    stats = {"coverage": cov, "guards_total": 12 if populated else 0, "guards_on_leave": 1 if populated else 0,
             "expiring": [_guard()] if populated else [], "expiring_count": 1 if populated else 0,
             "open_incidents": 2 if populated else 0, "recent_incidents": [_incident()] if populated else [],
             "kit_outstanding": 5 if populated else 0, "draft_rosters": [_roster()] if populated else []}
    chart = {"labels": [s["site_name"] for s in cov["sites"]], "required": [s["required"] for s in cov["sites"]],
             "assigned": [s["assigned"] for s in cov["sites"]], "present": [s["present"] for s in cov["sites"]]}
    return dict(stats=stats, cov=cov, day=TODAY, chart_json=json.dumps(chart))


def _grid(populated):
    posts = [_post(), _post(id="post-2", name="Patrol", coverage="night", guards_required=1, armed=False)] if populated else []
    shifts = [_shift(), _night()] if populated else []
    roster = _roster()
    days = L.date_range(roster["period_start"], roster["period_end"])
    slots = L.expand_roster(posts, shifts, roster["period_start"], roster["period_end"])
    assignments = [_assignment(), _assignment(id="a-2", guard_id="g-2", guard_name="Chala", is_relief=True),
                   _assignment(id="a-3", shift_template_id="sh-night", shift_name="Night 18-06")] if populated else []
    by_slot = {}
    for a in assignments:
        by_slot.setdefault(f"{a['post_id']}|{a['duty_date'].isoformat()}|{a['shift_template_id']}", []).append(a)
    required = sum(s["guards_required"] for s in slots)
    return {"posts": posts, "shifts": shifts, "days": days, "slots": slots, "assignments": assignments, "by_slot": by_slot,
            "gaps": L.coverage_gaps(slots, assignments), "required": required, "assigned": len(assignments),
            "coverage_pct": L.coverage_pct(required, len(assignments)),
            "post_shifts": {p["id"]: [s["id"] for s in L.slots_for_post(p, shifts)] for p in posts}}


def _filters():
    return {"date_from": date(2026, 10, 1), "date_to": TODAY, "site_id": "", "client_id": "", "guard_id": "", "category": "",
            "severity": "", "client_owned": ""}


def _sheet_rows():
    a = _assignment()
    return [{"assignment": a, "attendance": _attendance(), "guard_id": "g-1", "guard_name": "Abebe Kebede", "guard_no": "G-0001",
             "post_name": "Main gate", "post_id": "post-1", "shift_name": "Day 06-18", "shift_template_id": "sh-day",
             "start_time": time(6, 0), "end_time": time(18, 0), "status": "present", "key": "a-1"},
            {"assignment": _assignment(id="a-2", guard_id="g-2", guard_name="Chala", guard_no="G-0002", is_relief=True), "attendance": None,
             "guard_id": "g-2", "guard_name": "Chala", "guard_no": "G-0002", "post_name": "Main gate", "post_id": "post-1",
             "shift_name": "Day 06-18", "shift_template_id": "sh-day", "start_time": time(6, 0), "end_time": time(18, 0), "status": "", "key": "a-2"},
            {"assignment": None, "attendance": _attendance(id="att-3", assignment_id=None, guard_id="g-3", status="relief"), "guard_id": "g-3",
             "guard_name": "Dawit", "guard_no": "G-0003", "post_name": None, "post_id": None, "shift_name": "", "shift_template_id": None,
             "start_time": None, "end_time": None, "status": "relief", "key": "att-3"}]


def _report_ctx(key, populated):
    cfg = REPORTS[key]
    sample = {
        "coverage": {"duty_date": TODAY, "site_name": "Bole Branch", "client_name": "Awash Bank", "required": 4, "assigned": 3, "present": 2, "absent": 1,
                     "uncovered": 1, "coverage_pct": Decimal("75")},
        "attendance": {"site_name": "Bole Branch", "duty_date": TODAY, "guard_no": "G-0001", "guard_name": "Abebe", "post_name": "Main gate",
                       "shift_name": "Day", "status": "late", "check_in_at": NOW, "check_out_at": None, "hours_worked": Decimal("12"),
                       "night_hours": Decimal(0), "overtime_hours": Decimal("4"), "late_minutes": 20, "source": "self"},
        "absenteeism": {"guard_no": "G-0001", "guard_name": "Abebe", "scheduled": 20, "present": 18, "late": 2, "absent": 2, "leave": 0,
                        "late_minutes": 40, "absence_pct": Decimal("10")},
        "hours": {"guard_no": "G-0001", "name": "Abebe", "days_present": 20, "days_absent": 1, "hours_worked": Decimal("240"), "night_hours": Decimal("60"),
                  "overtime_hours": Decimal("40"), "hourly_rate": Decimal("38.46"), "overtime_pay": Decimal("2307.6"), "night_allowance": Decimal("600")},
        "kit_guards": {"holder": "Abebe", "holder_type": "guard", "guard_no": "G-0001", "item_name": "Radio", "kind": "radio", "serial_no": "R-1", "size": "",
                       "qty": Decimal(1), "issued_at": NOW, "expected_return": date(2026, 9, 1), "overdue": True, "condition_out": "good", "client_owned": "no"},
        "kit_sites": {"site_name": "Bole Branch", "item_name": "CCTV monitor", "kind": "other", "serial_no": "M-1", "qty": Decimal(1), "issued_at": NOW,
                      "client_owned": "yes", "condition_out": "good"},
        "licences": {"guard_no": "G-0001", "guard_name": "Abebe", "grade": "senior_guard", "home_site": "Bole", "document": "Guard licence",
                     "expiry": date(2026, 11, 10), "days": 15, "status": "expiring"},
        "incidents": {"site_name": "Bole Branch", "category": "intrusion", "total": 3, "low": 1, "medium": 1, "high": 1, "critical": 0, "open": 2},
        "incident_list": {"incident_no": "INC-2026-000001", "occurred_at": NOW, "site_name": "Bole", "client_name": "Awash", "category": "theft",
                          "severity": "high", "status": "open", "guard_name": "Abebe", "reported_to_police": "yes", "client_notified": "no", "description": "x"},
        "billing": {"client_name": "Awash Bank", "site_name": "Bole", "period": "2026-10", "guard_days": 60, "hours": Decimal("720"),
                    "night_hours": Decimal("240"), "overtime_hours": Decimal("80"), "rate": Decimal("85"), "amount": Decimal("61200")},
    }[key]
    rows = [sample, dict(sample)] if populated else []
    totals = {k: sum((Decimal(str(r.get(k) or 0)) for r in rows), Decimal(0)) for k, _l in cfg["columns"]
              if rows and isinstance(rows[0].get(k), (int, Decimal)) and not isinstance(rows[0].get(k), bool)}
    return dict(active_page=f"report_{key}", report_key=key, report_title=cfg["title"], columns=cfg["columns"], rows=rows, totals=totals,
                filters=_filters(), filter_fields=cfg["filters"], sites=[_site()], clients=[_client()], guards=[_guard()])


CASES = [
    ("secops/dashboard.html", lambda: _dashboard_ctx(True)),
    ("secops/dashboard.html", lambda: _dashboard_ctx(False)),
    ("secops/coverage.html", lambda: dict(gaps=[dict(s, assigned=0, gap=s["guards_required"], site_name="Bole Branch", client_name="Awash")
                                               for s in L.expand_roster([_post()], [_shift()], TODAY, TODAY)],
                                          filters=_filters(), sites=[_site()], total_gap=2)),
    ("secops/coverage.html", lambda: dict(gaps=[], filters=_filters(), sites=[], total_gap=0)),
    ("secops/clients.html", lambda: dict(clients=[_client(), _client(id="cl-2", name="Zemen", is_active=False, contract_id=None)], search="")),
    ("secops/clients.html", lambda: dict(clients=[], search="")),
    ("secops/client_form.html", lambda: dict(client=_client(), is_edit=True, lookups=_lookups(), sites=[_site()])),
    ("secops/client_form.html", lambda: dict(client={"is_active": True}, is_edit=False, lookups=_lookups(False))),
    ("secops/sites.html", lambda: dict(sites=[_site(), _site(id="site-2", name="HQ", status="closed", start_date=None, supervisor_name=None)],
                                       clients=[_client()], status_filter="", client_filter="", search="")),
    ("secops/sites.html", lambda: dict(sites=[], clients=[], status_filter="", client_filter="", search="")),
    ("secops/site_form.html", lambda: dict(site=_site(), is_edit=True, clients=[_client()], lookups=_lookups())),
    ("secops/site_form.html", lambda: dict(site={"status": "active", "site_type": "office", "required_guard_grade": "guard", "client_id": ""},
                                           is_edit=False, clients=[], lookups=_lookups(False))),
    ("secops/site_detail.html", lambda: dict(site=_site(), posts=[_post(), _post(id="post-2", name="Patrol", is_active=False)], shifts=[_shift()],
                                             rosters=[_roster()], guards=[_guard()], kit=[_kit_issue(guard_id=None, site_id="site-1", client_owned=True)],
                                             incidents=[_incident()], documents=[{"id": "d1", "filename": "orders.pdf", "uploaded_by": "admin"}],
                                             coverage=_coverage(True)["sites"][0], week_start=date(2026, 10, 26))),
    ("secops/site_detail.html", lambda: dict(site=_site(instructions="", gps_lat=None, client_name=None, supervisor_name=None), posts=[], shifts=[],
                                             rosters=[], guards=[], kit=[], incidents=[], documents=[], coverage=None, week_start=date(2026, 10, 26))),
    ("secops/shifts.html", lambda: dict(shifts=[_shift(), _night()])),
    ("secops/shifts.html", lambda: dict(shifts=[])),
    ("secops/guards.html", lambda: dict(guards=[_guard(), _guard(id="g-2", guard_no="G-0002", status="exited", licence_expiry=date(2026, 1, 1),
                                                                 firearm_permit_no=None, name=None)],
                                        counts={"active": 1, "exited": 1}, sites=[_site()], status_filter="", site_filter="", grade_filter="",
                                        search="", docs_filter="")),
    ("secops/guards.html", lambda: dict(guards=[], counts={}, sites=[], status_filter="", site_filter="", grade_filter="", search="", docs_filter="")),
    ("secops/guard_form.html", lambda: dict(guard=_guard(), is_edit=True, employees=[], sites=[_site()])),
    ("secops/guard_form.html", lambda: dict(guard={"grade": "guard", "status": "active", "training_level": "basic"}, is_edit=False,
                                            employees=[{"employee_id": "EMP009", "name": "New Guy", "department": "", "position": ""}], sites=[])),
    ("secops/guard_form.html", lambda: dict(guard={}, is_edit=False, employees=[], sites=[])),
    ("secops/guard_detail.html", lambda: dict(guard=_guard(), kit=[_kit_issue(), _kit_issue(id="ki-2", status="returned", returned_at=NOW)],
                                              duties=[_assignment()], attendance=[_attendance(), _attendance(id="att-2", status="absent", check_in_at=None, check_out_at=None)],
                                              incidents=[_incident()], documents=[{"id": "d1", "filename": "licence.jpg", "uploaded_by": "admin"}])),
    ("secops/guard_detail.html", lambda: dict(guard=_guard(licence_expiry=None, firearm_permit_no=None, firearm_permit_expiry=None, home_site_name=None,
                                                           name=None, position="", notes=""),
                                              kit=[], duties=[], attendance=[], incidents=[], documents=[])),
    ("secops/kit_card.html", lambda: dict(guard=_guard(), kit=[_kit_issue(), _kit_issue(id="ki-2", status="lost", returned_at=NOW)], printed_at=NOW)),
    ("secops/kit_card.html", lambda: dict(guard=_guard(name=None, home_site_name=None), kit=[], printed_at=NOW)),
    ("secops/rosters.html", lambda: dict(rosters=[_roster(), _roster(id="r-2", status="published")], sites=[_site()], site_filter="", status_filter="",
                                         week_start=date(2026, 10, 26))),
    ("secops/rosters.html", lambda: dict(rosters=[], sites=[], site_filter="", status_filter="", week_start=date(2026, 10, 26))),
    ("secops/roster_builder.html", lambda: dict(roster=_roster(), grid=_grid(True), guards=[_guard(), _guard(id="g-2", guard_no="G-0002", name="Chala", home_site_id=None)],
                                                home_guard_ids={"g-1"}, prev_start=date(2026, 10, 19), next_start=date(2026, 11, 2))),
    ("secops/roster_builder.html", lambda: dict(roster=_roster(status="published"), grid=_grid(True), guards=[_guard()], home_guard_ids=set(),
                                                prev_start=date(2026, 10, 19), next_start=date(2026, 11, 2))),
    ("secops/roster_builder.html", lambda: dict(roster=_roster(status="closed"), grid=_grid(False), guards=[], home_guard_ids=set(),
                                                prev_start=date(2026, 10, 19), next_start=date(2026, 11, 2))),
    ("secops/attendance.html", lambda: dict(rows=[_attendance(), _attendance(id="att-2", status="absent", check_in_at=None, check_out_at=None, gps_lat=None)],
                                            filters=_filters(), status_filter="", totals={"hours": Decimal("12"), "night": Decimal(0), "overtime": Decimal("4"), "absent": 1, "late": 0},
                                            sites=[_site()], guards=[_guard()])),
    ("secops/attendance.html", lambda: dict(rows=[], filters=_filters(), status_filter="", totals={"hours": Decimal(0), "night": Decimal(0), "overtime": Decimal(0), "absent": 0, "late": 0},
                                            sites=[], guards=[])),
    ("secops/attendance_sheet.html", lambda: dict(rows=_sheet_rows(), sites=[_site()], site=_site(), site_id="site-1", day=TODAY, guards=[_guard()], posts=[_post()],
                                                  prev_day=date(2026, 10, 25), next_day=date(2026, 10, 27))),
    ("secops/attendance_sheet.html", lambda: dict(rows=[], sites=[_site()], site=_site(), site_id="site-1", day=TODAY, guards=[], posts=[],
                                                  prev_day=date(2026, 10, 25), next_day=date(2026, 10, 27))),
    ("secops/attendance_sheet.html", lambda: dict(rows=[], sites=[], site=None, site_id="", day=TODAY, guards=[], posts=[],
                                                  prev_day=date(2026, 10, 25), next_day=date(2026, 10, 27))),
    ("secops/me.html", lambda: dict(guard=_guard(), open_row=_attendance(check_out_at=None), duties=[_assignment()], recent=[_attendance()], site=None,
                                    kit=[_kit_issue()])),
    ("secops/me.html", lambda: dict(guard=_guard(), open_row=None, duties=[], recent=[], site=None, kit=[])),
    ("secops/me.html", lambda: dict(guard=None, open_row=None, duties=[], recent=[], site=None, kit=[])),
    ("secops/kit_items.html", lambda: dict(items=[_kit_item(), _kit_item(id="k-2", name="Radio", kind="radio", tracks_serial=True, inventory_item_id=None)],
                                           lookups=_lookups())),
    ("secops/kit_items.html", lambda: dict(items=[], lookups=_lookups(False))),
    ("secops/kit_issues.html", lambda: dict(issues=[_kit_issue(), _kit_issue(id="ki-2", guard_id=None, site_id="site-1", site_name="Bole Branch", client_owned=True,
                                                                             status="returned", returned_at=NOW, condition_in="good", expected_return=None)],
                                            guards=[_guard()], sites=[_site()], guard_filter="", site_filter="", status_filter="", client_owned_filter="")),
    ("secops/kit_issues.html", lambda: dict(issues=[], guards=[], sites=[], guard_filter="", site_filter="", status_filter="", client_owned_filter="")),
    ("secops/kit_issue_form.html", lambda: dict(items=[_kit_item()], guards=[_guard()], sites=[_site()], preset={"guard_id": "g-1", "site_id": ""})),
    ("secops/kit_issue_form.html", lambda: dict(items=[], guards=[], sites=[], preset={"guard_id": "", "site_id": ""})),
    ("secops/incidents.html", lambda: dict(incidents=[_incident(), _incident(id="inc-2", severity="critical", status="closed", guard_name=None)],
                                           counts={"open": 1, "closed": 1}, sites=[_site()], status_filter="", site_filter="", severity_filter="",
                                           category_filter="", date_from="", date_to="")),
    ("secops/incidents.html", lambda: dict(incidents=[], counts={}, sites=[], status_filter="", site_filter="", severity_filter="", category_filter="",
                                           date_from="", date_to="")),
    ("secops/incident_form.html", lambda: dict(incident={"site_id": "site-1", "guard_id": "", "severity": "low", "category": "other", "occurred_at": "2026-10-26T07:00"},
                                               is_edit=False, sites=[_site()], posts=[_post()], guards=[_guard()])),
    ("secops/incident_form.html", lambda: dict(incident={}, is_edit=False, sites=[], posts=[], guards=[])),
    ("secops/incident_detail.html", lambda: dict(incident=_incident(client_notified_at=NOW, client_notification_note="Called Ato Kebede"),
                                                 documents=[{"id": "d1", "filename": "fence.jpg", "uploaded_by": "abebe"}], sites=[_site()], posts=[_post()], guards=[_guard()])),
    ("secops/incident_detail.html", lambda: dict(incident=_incident(client_email=None, guard_id=None, closed_at=NOW, status="closed"), documents=[], sites=[], posts=[], guards=[])),
    ("secops/payroll_feed.html", lambda: dict(period="2026-09", rows=[_feed_row(), _feed_row(id="f-2", guard_id="g-2", status="exported")],
                                              totals={"hours_worked": Decimal("576"), "night_hours": Decimal("192"), "overtime_hours": Decimal("160"),
                                                      "overtime_pay": Decimal("9230.76"), "night_allowance": Decimal("1920"), "days_present": 48, "days_absent": 2},
                                              periods=[{"period": "2026-09", "guards": 2, "overtime_pay": Decimal("9230.76"), "night_allowance": Decimal("1920"),
                                                        "status": "draft", "generated_at": NOW}],
                                              settings=_settings(), exported=True)),
    ("secops/payroll_feed.html", lambda: dict(period="2026-09", rows=[], totals={k: Decimal(0) for k in ("hours_worked", "night_hours", "overtime_hours", "overtime_pay", "night_allowance")} | {"days_present": 0, "days_absent": 0},
                                              periods=[], settings=_settings(), exported=False)),
    ("secops/settings.html", lambda: dict(settings=_settings(), holidays=[{"id": "h1", "holiday_date": date(2026, 9, 27), "name": "Meskel"}], weekend_days={6})),
    ("secops/settings.html", lambda: dict(settings=_settings(), holidays=[], weekend_days=set())),
    ("secops/reports_index.html", lambda: dict(reports=REPORTS)),
] + [("secops/report_table.html", (lambda k=k, p=p: _report_ctx(k, p))) for k in REPORTS for p in (True, False)]


@pytest.mark.parametrize("template,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_secops_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**{**_base_ctx(), **ctx_fn()})
    assert len(html) > 1000
    assert "module-sidebar" in html


def test_every_secops_template_is_covered():
    covered = {t for t, _ in CASES}
    for tpl in (_WEB_DIR / "templates" / "secops").glob("*.html"):
        if tpl.name.startswith("_"):
            continue
        assert f"secops/{tpl.name}" in covered, tpl.name


def test_templates_do_not_use_top_level_jquery_ready():
    for tpl in (_WEB_DIR / "templates" / "secops").glob("*.html"):
        assert "$(document).ready" not in tpl.read_text(encoding="utf-8"), tpl.name


def test_roster_builder_shows_gaps_and_relief():
    html = env.get_template("secops/roster_builder.html").render(**_base_ctx(), roster=_roster(), grid=_grid(True), guards=[_guard()],
                                                                  home_guard_ids={"g-1"}, prev_start=date(2026, 10, 19), next_start=date(2026, 11, 2))
    assert 'class="slot gap"' in html and 'class="slot full"' in html
    assert "Auto-fill from last week" in html and "Publish" in html


def test_i18n_catalogue_covers_every_template_string():
    """Every literal passed to _() in the secops templates, every report title/column label
    and every option value rendered through _() must have an Amharic entry."""
    import importlib
    cat = importlib.import_module("i18n_catalogue_secops").AM
    assert isinstance(cat, dict) and len(cat) > 400
    assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in cat.items())
    pat = re.compile(r"""_\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*\)""")
    missing = set()
    for tpl in (_WEB_DIR / "templates" / "secops").glob("*.html"):
        for m in pat.finditer(tpl.read_text(encoding="utf-8")):
            s = m.group(2).replace("\\'", "'")
            if s not in cat:
                missing.add(s)
    for cfg in REPORTS.values():
        if cfg["title"] not in cat:
            missing.add(cfg["title"])
        for _k, label in cfg["columns"]:
            if label not in cat:
                missing.add(label)
    for vocab in (L.GRADES, L.SITE_TYPES, L.SITE_STATUSES, L.COVERAGES, L.GUARD_STATUSES, L.ROSTER_STATUSES, L.ATTENDANCE_STATUSES,
                  L.KIT_KINDS, L.KIT_STATUSES, L.INCIDENT_CATEGORIES, L.SEVERITIES, L.INCIDENT_STATUSES, L.TRAINING_LEVELS, L.ATTENDANCE_SOURCES):
        for v in vocab:
            for form in (v, v.replace("_", " "), v.replace("_", " ").capitalize()):
                if form not in cat:
                    missing.add(form)
    assert not missing, sorted(missing)
