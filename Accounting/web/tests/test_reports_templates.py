"""
Template render tests — Report Builder module.

Renders every reports/* template with route-accurate contexts, both EMPTY and
POPULATED, so undefined variables / wrong field names fail the build instead
of 500ing in production. No database required.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from reports_catalog import DATE_PRESETS, SOURCES, OutCol  # noqa: E402
from reports_engine import ReportResult, format_cell  # noqa: E402
from reports_jobs import FORMATS, FREQUENCIES, describe_schedule  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))


def _base_ctx(path="/reports/x"):
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
    )


NOW = datetime(2026, 9, 12, 10, 0)


def _report(**kw):
    r = dict(id="r1", company_id="default", name="Income by month", description="Monthly income",
             source_key="vat_income", columns=[], filters=[], group_by=["income_date:month"],
             aggregates=[{"column": "gross_amount", "fn": "sum"}], sort=[], date_column="income_date",
             date_preset="fiscal_year", limit=5000, chart={"type": "bar", "x": "income_date__month", "y": "sum__gross_amount"},
             is_shared=True, is_template=True, created_by="system", created_at=NOW, updated_at=NOW)
    r.update(kw)
    return r


def _schedule(**kw):
    s = dict(id="s1", company_id="default", report_id="r1", report_name="Income by month", frequency="weekly",
             hour=7, minute=30, weekday=0, day_of_month=None, format="pdf", recipients="a@x.com, b@y.com",
             subject="", is_active=True, last_run_at=None, next_run_at=NOW, created_by="admin")
    s.update(kw)
    return s


def _run(**kw):
    r = dict(id="run1", company_id="default", report_id="r1", report_name="Income by month", schedule_id="s1",
             started_at=NOW, finished_at=NOW, status="ok", row_count=12, error=None,
             output_path="/tmp/ebms_reports/x.pdf", output_format="pdf", triggered_by="scheduler")
    r.update(kw)
    return r


def _result(empty=False):
    cols = [OutCol("income_date__month", "Month of Income date", "text"),
            OutCol("sum__gross_amount", "Sum of Gross amount", "number", "gross_amount", "sum")]
    if empty:
        return ReportResult(columns=cols, rows=[], row_count=0)
    rows = [{"income_date__month": "2026-07", "sum__gross_amount": 1000.0},
            {"income_date__month": "2026-08", "sum__gross_amount": 2500.5}]
    return ReportResult(columns=cols, rows=rows, totals={"sum__gross_amount": 3500.5}, row_count=2)


META = {"report_name": "Income by month", "description": "Monthly income", "company_name": "Test Co",
        "source_label": "VAT Income", "generated_at": "2026-09-12 10:00", "filters_summary": "Income date: FY",
        "date_preset": "Ethiopian fiscal year", "row_count": 2, "truncated": False}
STATS = {"reports": 3, "templates": 2, "schedules": 1, "active_schedules": 1, "runs_ok": 4, "runs_error": 1}
EMPTY_STATS = {k: 0 for k in STATS}
CHART = {"type": "bar", "labels": ["2026-07", "2026-08"], "values": [1000.0, 2500.5], "label": "Sum of Gross amount"}


def _view_ctx(empty=False, chart=True, error=None):
    res = _result(empty)
    return dict(report=_report(), result=res, rows=res.rows, meta=META, error=error,
                chart=CHART if chart else None, format_cell=format_cell, NUMBER="number",
                schedules=[] if empty else [_schedule()], runs=[] if empty else [_run()],
                describe_schedule=describe_schedule)


def _builder_ctx(report=None):
    defn = {k: (report or {}).get(k) for k in ("id", "name", "description", "source_key", "columns", "filters",
                                                 "group_by", "aggregates", "sort", "date_column", "date_preset",
                                                 "limit", "chart", "is_shared")} if report else \
        {"source_key": "", "columns": [], "filters": [], "group_by": [], "aggregates": [], "sort": [],
         "date_column": None, "date_preset": "all", "limit": 5000, "chart": None, "name": "", "description": ""}
    return dict(report=report, definition_json=json.dumps(defn, default=str), date_presets=DATE_PRESETS)


CASES = [
    ("reports/dashboard.html", lambda: dict(stats=STATS, reports=[_report(), _report(id="r2", is_template=False, chart=None)],
                                            schedules=[_schedule()], runs=[_run(), _run(status="error", error="boom")],
                                            sources=SOURCES, describe_schedule=describe_schedule)),
    ("reports/dashboard.html", lambda: dict(stats=EMPTY_STATS, reports=[], schedules=[], runs=[], sources=SOURCES,
                                            describe_schedule=describe_schedule)),
    ("reports/list.html", lambda: dict(reports=[_report(), _report(id="r2", source_key="bid_records", group_by=[])],
                                       sources=SOURCES, source_filter="vat_income")),
    ("reports/list.html", lambda: dict(reports=[], sources=SOURCES, source_filter="")),
    ("reports/builder.html", lambda: _builder_ctx(None)),
    ("reports/builder.html", lambda: _builder_ctx(_report(name='Quote " <script>'))),
    ("reports/view.html", lambda: _view_ctx()),
    ("reports/view.html", lambda: _view_ctx(empty=True, chart=False)),
    ("reports/view.html", lambda: _view_ctx(empty=True, chart=False, error="source table missing")),
    ("reports/schedules.html", lambda: dict(schedules=[_schedule(), _schedule(id="s2", is_active=False, frequency="monthly",
                                                                             day_of_month=31, subject="Monthly pack")],
                                            describe_schedule=describe_schedule)),
    ("reports/schedules.html", lambda: dict(schedules=[], describe_schedule=describe_schedule)),
    ("reports/schedule_form.html", lambda: dict(schedule=_schedule(), reports=[_report()], frequencies=FREQUENCIES, formats=FORMATS)),
    ("reports/schedule_form.html", lambda: dict(schedule={"report_id": "", "frequency": "daily", "hour": 7, "minute": 0,
                                                          "format": "pdf", "recipients": "", "subject": "", "is_active": True},
                                                reports=[], frequencies=FREQUENCIES, formats=FORMATS)),
    ("reports/runs.html", lambda: dict(runs=[_run(), _run(id="run2", status="error", error="x", output_path=None)],
                                       report_id="r1", os_path_exists=lambda p: True)),
    ("reports/runs.html", lambda: dict(runs=[], report_id="", os_path_exists=lambda p: False)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_reports_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000


def test_pdf_report_template_standalone():
    res = _result()
    html = env.get_template("reports/pdf_report.html").render(meta=META, result=res, rows=res.rows,
                                                              format_cell=format_cell, chart=CHART, NUMBER="number")
    assert "<!DOCTYPE html>" in html and "Test Co" in html and "3,500.50" in html
    assert "cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1" in html
    empty = _result(empty=True)
    html2 = env.get_template("reports/pdf_report.html").render(meta=META, result=empty, rows=[], format_cell=format_cell,
                                                               chart=None, NUMBER="number")
    assert "No data" in html2 and "Chart.js" not in html2


def test_builder_embeds_definition_safely_and_uses_csrf_header():
    html = env.get_template("reports/builder.html").render(**_base_ctx(), **_builder_ctx(_report(name='</script><b>x')))
    assert "</script><b>x" not in html                   # tojson escapes < and >
    assert "\\u003c/script\\u003e" in html
    for needle in ("/reports/api/catalog", "/reports/api/preview", "/reports/api/save", "X-CSRFToken",
                   "cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js", "DOMContentLoaded"):
        assert needle in html, needle
    source = (_WEB_DIR / "templates" / "reports" / "builder.html").read_text(encoding="utf-8")
    assert "$(document).ready" not in source


def test_view_renders_totals_and_chart_json():
    html = env.get_template("reports/view.html").render(**_base_ctx(), **_view_ctx())
    assert "3,500.50" in html and "2,500.50" in html
    assert '"labels": ["2026-07", "2026-08"]' in html
    assert "reportChart" in html


def test_forms_carry_csrf_token():
    for tpl, ctx in (("reports/schedules.html", dict(schedules=[_schedule()], describe_schedule=describe_schedule)),
                     ("reports/list.html", dict(reports=[_report()], sources=SOURCES, source_filter="")),
                     ("reports/schedule_form.html", dict(schedule=_schedule(), reports=[_report()],
                                                         frequencies=FREQUENCIES, formats=FORMATS))):
        html = env.get_template(tpl).render(**_base_ctx(), **ctx)
        assert 'name="csrf_token"' in html, tpl
