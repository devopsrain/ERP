"""
Template render tests — Mobile Money Payments module.

Renders every payments template with route-accurate contexts, both EMPTY
and POPULATED (same stub harness as test_vat_templates.py). No database.
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

from payment_providers import (  # noqa: E402
    ADAPTERS, DIRECTIONS, PROVIDER_LABELS, PROVIDERS, STATUSES, provider_choices,
)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))
MATCH_TYPES = ("income", "expense", "invoice")


def _base_ctx(path="/payments/x", active="dashboard"):
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
        active_page=active, providers=provider_choices(), provider_labels=PROVIDER_LABELS,
        directions=DIRECTIONS, statuses=STATUSES, match_types=MATCH_TYPES,
    )


def _payment(**kw):
    p = dict(id="11111111-2222-3333-4444-555555555555", company_id="default", account_id="acc1",
             provider="telebirr", direction="in", amount=Decimal("1150.00"), currency="ETB", fee=Decimal("0"),
             payer_name="Abebe Kebede", payer_msisdn="+251911223344", payee_name="", payee_msisdn=None,
             provider_txn_id="BJK7H2XYZ1", reference="INV-2026-042", narration="Invoice payment",
             paid_at=datetime(2026, 9, 1, 10, 15), status="completed", source="statement_import",
             raw={"Transaction No": "BJK7H2XYZ1"}, matched_type=None, matched_id=None, matched_at=None,
             matched_by=None, reconciled=False, reconciled_at=None, reconciled_by=None, created_by="admin",
             created_at=datetime(2026, 9, 1, 11, 0), account_name="Telebirr HQ", account_number="600123")
    p.update(kw)
    return p


def _candidate(**kw):
    c = dict(type="income", id="inc-1", amount=1150.0, date=date(2026, 9, 1), description="Consulting invoice",
             counterparty="Abebe Kebede", reference="INV-2026-042", invoice_number="INV-2026-042",
             tender_id="", payment_mode="advance", score=100, reasons=["exact amount", "same day"])
    c.update(kw)
    return c


def _account(**kw):
    a = dict(id="acc1", company_id="default", provider="telebirr", name="Telebirr HQ", account_number="600123",
             currency="ETB", is_active=True, opening_balance=Decimal("1000"), notes="", movement=Decimal("1150"),
             balance=Decimal("2150"), txn_count=1, unreconciled=1, created_at=datetime(2026, 1, 1))
    a.update(kw)
    return a


def _empty_stats():
    return {"by_provider": {p: {"today_in": Decimal(0), "today_out": Decimal(0), "month_in": Decimal(0),
                                "month_out": Decimal(0), "count": 0} for p in PROVIDERS},
            "today_in": Decimal(0), "today_out": Decimal(0), "month_in": Decimal(0), "month_out": Decimal(0),
            "total_count": 0, "unreconciled": 0, "unmatched": 0, "pending": 0, "total_balance": Decimal(0)}


def _stats():
    s = _empty_stats()
    s["by_provider"]["telebirr"].update(today_in=Decimal("1150"), month_in=Decimal("3450"), count=3)
    s.update(today_in=Decimal("1150"), month_in=Decimal("3450"), total_count=3, unreconciled=2, unmatched=1, pending=1)
    return s


def _provider_rows(configured=False):
    rows = []
    for p in PROVIDERS:
        a = ADAPTERS[p]
        rows.append({"provider": p, "label": a.label, "adapter": a,
                     "env": {k: configured for k in (*a.ENV_REQUIRED, *a.ENV_OPTIONAL)},
                     "required": list(a.ENV_REQUIRED), "optional": list(a.ENV_OPTIONAL),
                     "configured": configured and bool(a.ENV_REQUIRED), "missing": [] if configured else list(a.ENV_REQUIRED),
                     "notifies": p in ("telebirr", "cbebirr", "mpesa"),
                     "callback_url": f"https://ebms.example/webhooks/inbound/{p}",
                     "settings": {"short_code": "600123", "merchant_name": "My Co", "callback_secret_ref": "X_SECRET",
                                  "require_signature": "1", "enabled": "1", "_updated_at": datetime(2026, 9, 1),
                                  "_updated_by": "admin"} if configured else {}})
    return rows


def _import_row():
    return dict(id="imp1", company_id="default", account_id="acc1", provider="mpesa", filename="mpesa_sept.xlsx",
                rows_total=10, rows_imported=8, rows_duplicate=2, rows_error=0, errors="", imported_by="admin",
                imported_at=datetime(2026, 9, 2, 9, 0), account_name="M-Pesa till")


CASES = [
    ("payments/dashboard.html", "dashboard", lambda: dict(stats=_empty_stats(), balances=[], recent=[],
                                                          inbound_available=False, inbound_pending=None)),
    ("payments/dashboard.html", "dashboard", lambda: dict(stats=_stats(), balances=[_account(), _account(id="a2", provider="mpesa", is_active=False)],
                                                          recent=[_payment(), _payment(direction="out", status="pending", reconciled=True)],
                                                          inbound_available=True, inbound_pending=3)),
    ("payments/list.html", "list", lambda: dict(payments=[], filters={k: "" for k in ("provider", "direction", "status", "reconciled", "matched", "account_id", "date_from", "date_to", "q")},
                                                accounts=[], total_in=Decimal(0), total_out=Decimal(0), query_string="")),
    ("payments/list.html", "list", lambda: dict(payments=[_payment(), _payment(id="p2", direction="out", matched_type="expense", matched_id="e1", reconciled=True, status="reversed")],
                                                filters={"provider": "telebirr", "direction": "in", "status": "completed", "reconciled": "no", "matched": "", "account_id": "acc1", "date_from": "2026-09-01", "date_to": "2026-09-30", "q": "Abebe"},
                                                accounts=[_account()], total_in=Decimal("1150"), total_out=Decimal("1150"), query_string="provider=telebirr")),
    ("payments/form.html", "new", lambda: dict(payment={}, accounts=[], today="2026-09-01T10:00")),
    ("payments/form.html", "new", lambda: dict(payment={"provider": "mpesa", "direction": "out", "amount": "500", "matched_type": "expense", "matched_id": "e1"},
                                               accounts=[_account()], today="2026-09-01T10:00")),
    ("payments/detail.html", "list", lambda: dict(payment=_payment(raw=None, source="manual", provider_txn_id=None), raw_json="", candidates=[], matched=None)),
    ("payments/detail.html", "list", lambda: dict(payment=_payment(), raw_json='{\n  "Transaction No": "BJK7H2XYZ1"\n}',
                                                  candidates=[_candidate(), _candidate(id="inc-2", score=62, reasons=["amount within 0.5%"])], matched=None)),
    ("payments/detail.html", "list", lambda: dict(payment=_payment(matched_type="income", matched_id="inc-1", matched_by="admin", matched_at=datetime(2026, 9, 2), reconciled=True),
                                                  raw_json="{}", candidates=[],
                                                  matched={"description": "Consulting invoice", "customer_name": "Abebe", "gross_amount": 1150.0, "invoice_number": "INV-2026-042", "tender_id": "BID-1"})),
    ("payments/detail.html", "list", lambda: dict(payment=_payment(status="pending"), raw_json="", candidates=[], matched=None)),
    ("payments/detail.html", "list", lambda: dict(payment=_payment(status="reversed"), raw_json="", candidates=[], matched=None)),
    ("payments/reconcile.html", "reconcile", lambda: dict(queue=[], stats=_empty_stats(), income_available=False, expense_available=False)),
    ("payments/reconcile.html", "reconcile", lambda: dict(queue=[{**_payment(), "candidates": [_candidate(), _candidate(id="inc-3", score=45, reasons=["amount within 0.9%", "6 day(s) apart"])]},
                                                                 {**_payment(id="p3", direction="out", payee_name="Vendor"), "candidates": []}],
                                                          stats=_stats(), income_available=True, expense_available=True)),
    ("payments/import_statement.html", "import", lambda: dict(accounts=[], imports=[], adapters=[(p, ADAPTERS[p]) for p in PROVIDERS], selected_provider="telebirr", report=None)),
    ("payments/import_statement.html", "import", lambda: dict(accounts=[_account()], imports=[_import_row()], adapters=[(p, ADAPTERS[p]) for p in PROVIDERS], selected_provider="mpesa",
                                                              report={"total": 10, "imported": 8, "duplicates": 1, "errors": ["Row 4: no positive amount"], "import_id": "imp1", "provider": "mpesa", "filename": "mpesa_sept.xlsx"})),
    ("payments/accounts.html", "accounts", lambda: dict(accounts=[])),
    ("payments/accounts.html", "accounts", lambda: dict(accounts=[_account(), _account(id="a2", provider="cash", is_active=False, movement=Decimal("-50"), balance=Decimal("950"), notes="Petty cash")])),
    ("payments/settings.html", "settings", lambda: dict(provider_rows=_provider_rows(False), callback_base="", test_result=None, inbound_available=False, inbound_outcomes=[])),
    ("payments/settings.html", "settings", lambda: dict(provider_rows=_provider_rows(True), callback_base="https://ebms.example",
                                                        test_result={"provider": "mpesa", "result": {"status": "not_configured", "missing_env": ["MPESA_PASSKEY"], "message": ""}},
                                                        inbound_available=True,
                                                        inbound_outcomes=[{"processed_at": datetime(2026, 9, 1), "source": "telebirr", "outcome": "created", "detail": "", "payment_id": "p1"},
                                                                          {"processed_at": datetime(2026, 9, 1), "source": "mpesa", "outcome": "rejected", "detail": "signature verification failed", "payment_id": None}])),
    ("payments/settings.html", "settings", lambda: dict(provider_rows=_provider_rows(True), callback_base="https://ebms.example",
                                                        test_result={"provider": "mpesa", "result": {"status": "pending_integration", "message": "ok", "request": {"BusinessShortCode": "600000"}}},
                                                        inbound_available=True, inbound_outcomes=[])),
]


@pytest.mark.parametrize("template,active,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _, _) in enumerate(CASES)])
def test_payments_template_renders(template, active, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(active=active), **ctx_fn())
    assert len(html) > 1000
    assert 'class="module-sidebar"' in html and "content-with-sidebar" in html


def test_settings_shows_callback_urls_and_env_names():
    html = env.get_template("payments/settings.html").render(**_base_ctx(active="settings"), **CASES[-2][2]())
    for needle in ("/webhooks/inbound/telebirr", "/webhooks/inbound/cbebirr", "/webhooks/inbound/mpesa",
                   "TELEBIRR_APP_ID", "MPESA_PASSKEY", "CBEBIRR_MERCHANT_ID", "credentials detected"):
        assert needle in html, needle


def test_templates_do_not_use_top_level_jquery_ready():
    for t in sorted((_WEB_DIR / "templates" / "payments").glob("*.html")):
        assert "$(document).ready" not in t.read_text(encoding="utf-8"), t.name
