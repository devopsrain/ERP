"""
Template render tests — ERCA tax outputs module.

Renders every erca/* template with route-accurate contexts, EMPTY and
POPULATED, using the same stub harness as test_vat_templates.py. No DB.
"""
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

import erca_forms as forms  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))
D = Decimal


def _base_ctx(path="/erca/x"):
    request = SimpleNamespace(url=SimpleNamespace(path=path, query=""),
                              query_params=SimpleNamespace(get=lambda k, d=None: d),
                              session={}, form=SimpleNamespace())
    return dict(request=request, session={}, url_for=lambda *a, **k: "#",
                csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
                static_url=lambda p: p, static_cdn_url="", app_version="1.0",
                current_company_id="default", current_tenant=None,
                today=date(2026, 9, 12),
                months=[(i, forms.period_bounds(2000, i)["label"].split()[0]) for i in range(1, 13)])


def _profile(empty=False):
    base = {k: "" for k in ("tin", "vat_reg_no", "taxpayer_name", "taxpayer_name_am", "tax_centre", "region", "city",
                            "sub_city", "woreda", "house_no", "phone", "email")}
    base.update(company_id="default", category="A")
    if not empty:
        base.update(tin="0012345678", taxpayer_name="Abyssinia Networks PLC", taxpayer_name_am="አቢሲኒያ", tax_centre="Bole")
    return base


def _period(y=2026, m=8):
    return forms.period_bounds(y, m)


def _ret(status="draft"):
    r = forms.build_vat_return_lines([{"gross_amount": 1150, "vat_amount": 150, "vat_type": "STANDARD"}], [])
    ret = {"id": "r1", "period_year": 2026, "period_month": 8, "status": status, "lines": r["lines"],
           "totals": {**r["totals"], "income_rows": 1, "expense_rows": 0, "capital_rows": 0},
           "computed_at": datetime(2026, 9, 1, 10, 0), "finalized_by": "admin", "finalized_at": None,
           "filed_on": date(2026, 9, 20) if status == "filed" else None, "erca_reference": "ACK-1" if status == "filed" else None}
    ret["period"] = _period()
    ret["boxes"] = r["boxes"]
    return ret


def _wht(i=1, has_tin=True):
    return {"id": f"w{i}", "receipt_no": f"WHT-00000{i}", "payment_date": date(2026, 9, 3), "payee_name": "Supplier",
            "payee_tin": "0098765432" if has_tin else "", "has_tin": has_tin, "transaction_type": "goods",
            "gross_amount": D("10000.00"), "withheld_rate": D("0.02") if has_tin else D("0.30"),
            "withheld_amount": D("200.00") if has_tin else D("3000.00"), "invoice_ref": "S-1", "expense_id": None}


def _series(i=1, kind="invoice", active=True):
    return {"id": f"s{i}", "series_code": "INV" if kind == "invoice" else kind[:3].upper(), "prefix": "INV-", "next_number": 7,
            "pad": 6, "kind": kind, "is_active": active, "machine_id": "MRC-1" if i == 1 else None, "next_formatted": "INV-000007"}


def _inv(status="issued"):
    items = forms.normalize_items([{"description": "Firewall", "qty": 1, "unit_price": 20000}])
    return {"id": "i1", "number": "INV-000001", "kind": "invoice", "issued_at": datetime(2026, 9, 5, 9, 30),
            "customer_name": "Awash Bank", "customer_tin": "0011111111", "items": items, "status": status,
            "void_reason": "dup" if status == "voided" else None, "voided_at": datetime(2026, 9, 6) if status == "voided" else None,
            "hash": "ab" * 32, "withholding_expected": D("400.00"), "series_code": "INV", "machine_id": "MRC-1",
            "series_id": "s1", "created_by": "admin", "linked_income_id": None, **forms.invoice_totals(items)}


def _gap():
    return {"series_code": "INV", "number": "INV-000003", "reason": "dup", "actor": "admin", "created_at": datetime(2026, 9, 6)}


def _dash(populated):
    if not populated:
        return dict(period=_period(), position=None, unfiled=[], withholding=forms.withholding_summary([]),
                    invoice_counts={}, series=[], recent_invoices=[])
    return dict(period=_period(), position=_ret("finalized"), unfiled=forms.unfiled_periods(set(), date(2026, 9, 12), 3),
                withholding=forms.withholding_summary([_wht()]), invoice_counts={"invoice": {"issued": 3, "voided": 1}},
                series=[_series(), _series(2, "receipt")], recent_invoices=[_inv(), _inv("voided")])


def _audit(populated):
    invs = [_inv(), _inv("voided")] if populated else []
    return dict(start=date(2026, 7, 8), end=date(2026, 9, 12), fiscal_year=2019, backend="builtin",
                audit={"invoice_count": len(invs), "invoices": invs, "voided": [_gap()] if populated else [],
                       "chain": {"ok": not populated, "checked": len(invs), "broken": ["INV-000001"] if populated else []},
                       "profile": _profile(not populated)})


YEARS = [2023, 2024, 2025, 2026]
CASES = [
    ("erca/dashboard.html", "/erca/", lambda: dict(profile=_profile(True), **_dash(False))),
    ("erca/dashboard.html", "/erca/", lambda: dict(profile=_profile(), **_dash(True))),
    ("erca/profile.html", "/erca/profile", lambda: dict(profile=_profile(True), tin_ok=True, tin_msg="", categories=forms.TAXPAYER_CATEGORIES)),
    ("erca/profile.html", "/erca/profile", lambda: dict(profile=_profile(), tin_ok=False, tin_msg="bad", categories=forms.TAXPAYER_CATEGORIES)),
    ("erca/vat_return.html", "/erca/vat-returns", lambda: dict(profile=_profile(True), returns=[], unfiled=[], default_year=2026, default_month=8, years=YEARS)),
    ("erca/vat_return.html", "/erca/vat-returns", lambda: dict(profile=_profile(), returns=[dict(_ret(), period=_period()), dict(_ret("filed"), period=_period(2026, 7))],
                                                             unfiled=forms.unfiled_periods(set(), date(2026, 9, 12), 4), default_year=2026, default_month=8, years=YEARS)),
    ("erca/vat_return_detail.html", "/erca/vat-returns/r1", lambda: dict(profile=_profile(True), ret=_ret("draft"))),
    ("erca/vat_return_detail.html", "/erca/vat-returns/r1", lambda: dict(profile=_profile(), ret=_ret("finalized"))),
    ("erca/vat_return_detail.html", "/erca/vat-returns/r1", lambda: dict(profile=_profile(), ret=_ret("filed"))),
    ("erca/withholding.html", "/erca/withholding", lambda: dict(profile=_profile(True), entries=[], summary=forms.withholding_summary([]), year=None, month=None, years=YEARS)),
    ("erca/withholding.html", "/erca/withholding", lambda: dict(profile=_profile(), entries=[_wht(), _wht(2, False)],
                                                              summary=forms.withholding_summary([_wht(), _wht(2, False)]), year=2026, month=9, years=YEARS)),
    ("erca/withholding_form.html", "/erca/withholding/new", lambda: dict(profile=_profile(True), entry={}, transaction_types=forms.TRANSACTION_TYPES,
                                                                       rules={"rate_tin": forms.WHT_RATE_TIN, "rate_no_tin": forms.WHT_RATE_NO_TIN, "rate_import": forms.WHT_RATE_IMPORT,
                                                                              "threshold_goods": forms.WHT_THRESHOLD_GOODS, "threshold_services": forms.WHT_THRESHOLD_SERVICES})),
    ("erca/withholding_form.html", "/erca/withholding/w1/edit", lambda: dict(profile=_profile(), entry=_wht(), transaction_types=forms.TRANSACTION_TYPES,
                                                                           rules={"rate_tin": forms.WHT_RATE_TIN, "rate_no_tin": forms.WHT_RATE_NO_TIN, "rate_import": forms.WHT_RATE_IMPORT,
                                                                                  "threshold_goods": forms.WHT_THRESHOLD_GOODS, "threshold_services": forms.WHT_THRESHOLD_SERVICES})),
    ("erca/withholding_return.html", "/erca/withholding/return", lambda: dict(profile=_profile(True), entries=[], summary=forms.withholding_summary([]), year=2026, month=9, period=_period(2026, 9), years=YEARS)),
    ("erca/withholding_return.html", "/erca/withholding/return", lambda: dict(profile=_profile(), entries=[_wht()], summary=forms.withholding_summary([_wht()]), year=2026, month=9, period=_period(2026, 9), years=YEARS)),
    ("erca/invoice_numbers.html", "/erca/series", lambda: dict(profile=_profile(True), series=[], gaps=[])),
    ("erca/invoice_numbers.html", "/erca/series", lambda: dict(profile=_profile(), series=[_series(), _series(2, "withholding_receipt", False)], gaps=[_gap()])),
    ("erca/invoice_series_form.html", "/erca/series/new", lambda: dict(profile=_profile(True), series={}, kinds=forms.INVOICE_KINDS)),
    ("erca/invoice_series_form.html", "/erca/series/s1/edit", lambda: dict(profile=_profile(), series=_series(), kinds=forms.INVOICE_KINDS)),
    ("erca/invoices.html", "/erca/invoices", lambda: dict(profile=_profile(True), invoices=[], filters={"kind": None, "status": None, "start": None, "end": None},
                                                        kinds=forms.INVOICE_KINDS, series=[], vat_rate=forms.VAT_RATE, mode="list", total=D("0"))),
    ("erca/invoices.html", "/erca/invoices/new", lambda: dict(profile=_profile(), invoices=[_inv(), _inv("voided")], filters={"kind": "invoice", "status": "issued", "start": date(2026, 7, 1), "end": None},
                                                            kinds=forms.INVOICE_KINDS, series=[_series()], vat_rate=forms.VAT_RATE, mode="new", total=D("23000"))),
    ("erca/invoice_detail.html", "/erca/invoices/i1", lambda: dict(profile=_profile(True), invoice=dict(_inv(), items=[], customer_name="", customer_tin=""))),
    ("erca/invoice_detail.html", "/erca/invoices/i1", lambda: dict(profile=_profile(), invoice=_inv())),
    ("erca/invoice_detail.html", "/erca/invoices/i1", lambda: dict(profile=_profile(), invoice=_inv("voided"))),
    ("erca/audit_export.html", "/erca/audit", lambda: dict(profile=_profile(True), **_audit(False))),
    ("erca/audit_export.html", "/erca/audit", lambda: dict(profile=_profile(), **_audit(True))),
]


@pytest.mark.parametrize("template,path,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _, _) in enumerate(CASES)])
def test_erca_template_renders(template, path, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(path), **ctx_fn())
    assert len(html) > 1000


def test_every_erca_template_is_covered():
    files = {p.name for p in (_WEB_DIR / "templates" / "erca").glob("*.html") if not p.name.startswith("_")}
    covered = {t.split("/")[1] for t, _, _ in CASES}
    assert files == covered


def test_no_top_level_jquery_ready():
    # jQuery loads at the bottom of base.html — content scripts must not use $(document).ready
    for p in (_WEB_DIR / "templates" / "erca").glob("*.html"):
        assert "$(document).ready" not in p.read_text(encoding="utf-8"), p.name


def test_populated_pages_show_key_figures():
    html = env.get_template("erca/vat_return_detail.html").render(**_base_ctx(), profile=_profile(), ret=_ret())
    assert "150.00" in html and "Box" in html and "Finalize" in html
    html = env.get_template("erca/withholding.html").render(**_base_ctx("/erca/withholding"), profile=_profile(),
                                                            entries=[_wht(2, False)], summary=forms.withholding_summary([_wht(2, False)]),
                                                            year=None, month=None, years=YEARS)
    assert "no TIN" in html and "30%" in html and "3,000.00" in html
