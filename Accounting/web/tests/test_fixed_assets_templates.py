"""
Template render tests — Fixed Assets module.

Renders every assets/ template with route-accurate contexts, both EMPTY and
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

from fixed_assets_data_store import (  # noqa: E402
    DISPOSAL_METHODS, METHOD_LABELS, METHODS, STATUSES, FixedAssetDataStore, schedule,
)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))


def _base_ctx(path="/assets/x"):
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
        methods=METHODS, method_labels=METHOD_LABELS, statuses=STATUSES,
        disposal_methods=DISPOSAL_METHODS, active_page="dashboard",
    )


def _category(**kw):
    c = dict(id="cat-1", company_id="default", name="Computers", code="COMP",
             default_method="declining_balance", default_useful_life_months=48,
             default_salvage_pct=Decimal("0"), default_declining_rate=Decimal("25"),
             gl_asset_account="1520", gl_depreciation_expense_account="6520",
             gl_accumulated_depreciation_account="1592", asset_count=1,
             created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    c.update(kw)
    return c


def _asset(**kw):
    a = dict(id="asset-1", company_id="default", asset_tag="FA-00001", name="Dell Laptop",
             category_id="cat-1", category_name="Computers", category_code="COMP",
             description="Finance team laptop", serial_number="SN123", location="Addis HQ",
             custodian="A. Bekele", supplier="Dell ET", purchase_date=date(2026, 1, 10),
             in_service_date=date(2026, 1, 16), cost=Decimal("60000"), salvage_value=Decimal("5000"),
             useful_life_months=48, method="declining_balance", declining_rate=Decimal("25"),
             total_units=None, status="active", currency="ETB", notes="",
             created_at=datetime(2026, 1, 10), updated_at=datetime(2026, 1, 10),
             accumulated=Decimal("2500"), book_value=Decimal("57500"), last_period="2026-02",
             gl_asset_account="1520", gl_depreciation_expense_account="6520",
             gl_accumulated_depreciation_account="1592")
    a.update(kw)
    return a


def _schedule_rows():
    rows = schedule(60000, 5000, 48, "declining_balance", date(2026, 1, 16), declining_rate=25)
    for i, r in enumerate(rows):
        r["recorded"] = i < 2
        r["posted"] = i < 1
        r["journal_ref"] = "je-1" if i < 1 else None
    return rows


def _dashboard_stats(populated: bool):
    s = FixedAssetDataStore.empty_dashboard()
    if populated:
        s.update(count=2, cost=Decimal("100000"), accumulated=Decimal("12000"), book_value=Decimal("88000"),
                 by_status={"active": 1, "fully_depreciated": 1, "disposed": 0, "written_off": 0},
                 by_category=[{"name": "Computers", "count": 2, "cost": Decimal("100000"),
                               "accumulated": Decimal("12000"), "book_value": Decimal("88000")}],
                 monthly=[{"period": "2026-01", "total": Decimal("6000"), "posted": True},
                          {"period": "2026-02", "total": Decimal("6000"), "posted": False}],
                 upcoming=[{"id": "asset-1", "asset_tag": "FA-00001", "name": "Dell Laptop",
                            "cost": Decimal("60000"), "in_service_date": date(2022, 10, 1),
                            "end_date": date(2026, 10, 1)}],
                 recent=[_asset()], maintenance_cost=Decimal("350"), last_period="2026-02",
                 unposted_periods=1)
    return s


def _dashboard_ctx(populated):
    stats = _dashboard_stats(populated)
    chart = {"category_labels": [c["name"] for c in stats["by_category"]],
             "category_cost": [float(c["cost"]) for c in stats["by_category"]],
             "category_accumulated": [float(c["accumulated"]) for c in stats["by_category"]],
             "category_book_value": [float(c["book_value"]) for c in stats["by_category"]],
             "monthly_labels": [m["period"] for m in stats["monthly"]],
             "monthly_totals": [float(m["total"]) for m in stats["monthly"]],
             "status_labels": list(stats["by_status"]), "status_counts": list(stats["by_status"].values())}
    return dict(stats=stats, chart_json=json.dumps(chart), today=date(2026, 9, 12))


def _list_ctx(assets):
    cost = sum((a["cost"] for a in assets), Decimal(0))
    acc = sum((a["accumulated"] for a in assets), Decimal(0))
    return dict(assets=assets, totals={"cost": cost, "accumulated": acc, "book_value": cost - acc},
                categories=[_category()] if assets else [], category_filter="", status_filter="", search="")


def _preview_line(**kw):
    line = dict(asset=_asset(), period="2026-03", amount=Decimal("1198.00"), accumulated=Decimal("3698.00"),
                book_value=Decimal("56302.00"), units_used=None, existing=False, needs_units=False,
                skip_reason="", posted=False)
    line.update(kw)
    return line


def _depr_ctx(preview, summaries):
    total = sum((p["amount"] for p in (preview or []) if not p["existing"] and not p["skip_reason"]), Decimal(0))
    return dict(period="2026-03", preview=preview, preview_total=total, summaries=summaries)


_SUMMARIES = [
    {"period": "2026-02", "asset_count": 2, "total": Decimal("6000"), "all_posted": False, "any_posted": False, "journal_ref": None},
    {"period": "2026-01", "asset_count": 2, "total": Decimal("6000"), "all_posted": True, "any_posted": True, "journal_ref": "je-0001"},
]

_DISPOSAL = dict(id="d1", asset_id="asset-1", disposed_on=date(2026, 9, 1), proceeds=Decimal("50000"),
                 book_value_at_disposal=Decimal("57500"), gain_loss=Decimal("-7500"), method="sale", notes="Sold")


def _register_ctx(rows, as_of=""):
    totals = {k: sum((Decimal(r.get(k) or 0) for r in rows), Decimal(0))
              for k in ("cost", "accumulated", "book_value", "period_charge")}
    return dict(rows=rows, totals=totals, as_of=as_of, generated=date(2026, 9, 12))


CASES = [
    ("assets/dashboard.html", lambda: _dashboard_ctx(True)),
    ("assets/dashboard.html", lambda: _dashboard_ctx(False)),
    ("assets/list.html", lambda: _list_ctx([_asset(), _asset(id="asset-2", asset_tag="FA-00002",
                                                              status="disposed", method="straight_line")])),
    ("assets/list.html", lambda: _list_ctx([])),
    ("assets/form.html", lambda: dict(asset={"asset_tag": "FA-00003", "currency": "ETB", "method": "straight_line",
                                             "useful_life_months": 60}, is_edit=False, categories=[_category()])),
    ("assets/form.html", lambda: dict(asset=_asset(), is_edit=True, categories=[_category()])),
    ("assets/form.html", lambda: dict(asset={}, is_edit=False, categories=[])),
    ("assets/detail.html", lambda: dict(asset=_asset(), schedule=_schedule_rows(),
                                        maintenance=[{"id": "m1", "date": date(2026, 5, 1), "description": "Battery",
                                                      "cost": Decimal("350"), "vendor": "Dell ET"}],
                                        disposal=None, today=date(2026, 9, 12))),
    ("assets/detail.html", lambda: dict(asset=_asset(status="disposed", accumulated=Decimal(0),
                                                     book_value=Decimal("60000"), last_period=None),
                                        schedule=[], maintenance=[], disposal=_DISPOSAL, today=date(2026, 9, 12))),
    ("assets/detail.html", lambda: dict(asset=_asset(method="units_of_production", total_units=Decimal("10000"),
                                                     status="fully_depreciated"),
                                        schedule=[dict(period="2026-02", amount=Decimal("550"), accumulated=Decimal("550"),
                                                       book_value=Decimal("59450"), units_used=Decimal("100"),
                                                       recorded=True, posted=False, journal_ref=None)],
                                        maintenance=[], disposal=None, today=date(2026, 9, 12))),
    ("assets/categories.html", lambda: dict(categories=[_category(), _category(id="cat-2", code="OTHR", name="Other",
                                                                                asset_count=0, default_declining_rate=None)],
                                            gl_accounts=[{"account_code": "1520", "account_name": "Computers", "account_type": "Asset"}])),
    ("assets/categories.html", lambda: dict(categories=[], gl_accounts=[])),
    ("assets/depreciation_run.html", lambda: _depr_ctx([
        _preview_line(),
        _preview_line(existing=True, posted=True, units_used=Decimal("12")),
        _preview_line(asset=_asset(id="asset-3", method="units_of_production"), needs_units=True,
                      amount=Decimal(0), skip_reason="Enter units used for this period"),
        _preview_line(skip_reason="Not in service until 2026-05", amount=Decimal(0)),
    ], _SUMMARIES)),
    ("assets/depreciation_run.html", lambda: _depr_ctx([], _SUMMARIES)),
    ("assets/depreciation_run.html", lambda: _depr_ctx(None, [])),
    ("assets/register_report.html", lambda: _register_ctx([dict(_asset(), period_charge=Decimal("1198"))], "2026-03")),
    ("assets/register_report.html", lambda: _register_ctx([dict(_asset(), period_charge=Decimal(0))])),
    ("assets/register_report.html", lambda: _register_ctx([])),
    ("assets/disposal_form.html", lambda: dict(asset=_asset(), today=date(2026, 9, 12), proceeds=Decimal("50000"),
                                               preview_gain_loss=Decimal("-7500"))),
    ("assets/disposal_form.html", lambda: dict(asset=_asset(accumulated=Decimal(0), book_value=Decimal("60000"),
                                                            last_period=None),
                                               today=date(2026, 9, 12), proceeds=Decimal(0), preview_gain_loss=Decimal("-60000"))),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_fixed_assets_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000  # sanity: a real page came out
    assert "module-sidebar" in html


def test_dashboard_embeds_chart_json():
    html = env.get_template("assets/dashboard.html").render(**_base_ctx(), **_dashboard_ctx(True))
    assert 'id="fa-chart-data"' in html
    assert '"monthly_labels": ["2026-01", "2026-02"]' in html
    assert "faCategoryChart" in html and "faMonthlyChart" in html


def test_templates_do_not_use_top_level_jquery_ready():
    # jQuery loads at the bottom of base.html; a top-level $(document).ready
    # in a content block would run before it exists.
    for tpl in (_WEB_DIR / "templates" / "assets").glob("*.html"):
        assert "$(document).ready" not in tpl.read_text(encoding="utf-8"), tpl.name


def test_disposal_form_shows_loss_class():
    html = env.get_template("assets/disposal_form.html").render(
        **_base_ctx(), asset=_asset(), today=date(2026, 9, 12), proceeds=Decimal("50000"),
        preview_gain_loss=Decimal("-7500"))
    assert 'id="fa-gl" class="fs-5 text-danger"' in html
    assert "-7,500.00" in html
