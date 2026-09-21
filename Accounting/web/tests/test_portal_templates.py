"""
Template render tests — Customer & Supplier Portal.

Renders every portal/ and portal_admin/ template with route-accurate
contexts, EMPTY and POPULATED, so undefined variables or wrong field names
fail here instead of 500ing for an external user. No database required.
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

from portal_routes import fmt_date, fmt_dt, fmt_money, fmt_size  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))

CUSTOMER = {"id": "u-cust", "company_id": "default", "kind": "customer", "email": "c@acme.et",
            "full_name": "Chaltu A.", "org_name": "Acme PLC", "party_key": "Acme PLC", "tin": "0012",
            "phone": "+251 911 000 000", "last_login_at": datetime(2026, 9, 1, 9, 30), "is_active": True}
SUPPLIER = {**CUSTOMER, "id": "u-sup", "kind": "supplier", "email": "s@supply.et",
            "full_name": "Sami B.", "org_name": "Supply Co", "party_key": "Supply Co"}


def _base_ctx(user=None, path="/portal/"):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(), headers={},
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "tok", get_flashed_messages=lambda **k: [("success", "Saved")],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
        portal_user=user, company_name="Acme Trading PLC", current_year=2026,
        fmt_date=fmt_date, fmt_dt=fmt_dt, fmt_money=fmt_money, fmt_size=fmt_size,
    )


def _invoice():
    return {"income_id": "inc-0001-abcd", "contract_date": date(2026, 8, 1), "description": "Consulting",
            "category": "sales_revenue", "invoice_number": "INV-42", "gross_amount": 1150.0,
            "vat_amount": 150.0, "net_amount": 1000.0, "vat_type": "standard", "vat_rate": 0.15,
            "customer_name": "Acme PLC", "customer_tin": "0012"}


def _totals(rows):
    return {"gross_amount": sum(r["gross_amount"] for r in rows), "vat_amount": sum(r["vat_amount"] for r in rows),
            "net_amount": sum(r["net_amount"] for r in rows), "count": len(rows)}


def _ticket(with_msgs=True):
    t = {"id": "t1", "subject": "Invoice question", "body": "Where is INV-42?", "status": "in_progress",
         "priority": "normal", "created_at": datetime(2026, 9, 2, 10), "updated_at": datetime(2026, 9, 3, 11),
         "message_count": 2, "email": "c@acme.et", "full_name": "Chaltu A.", "org_name": "Acme PLC", "kind": "customer"}
    t["messages"] = [
        {"author_kind": "portal", "author": "Chaltu A.", "body": "Where is INV-42?", "created_at": datetime(2026, 9, 2, 10)},
        {"author_kind": "staff", "author": "admin", "body": "Sent today.", "created_at": datetime(2026, 9, 3, 11)},
    ] if with_msgs else []
    return t


def _order():
    return {"id": "po-1234-5678", "title": "Office chairs", "delivery_date": date(2026, 10, 1), "payment_terms": "net30",
            "total_amount": Decimal("12500.00"), "status": "open", "grn_received": False, "invoice_matched": False,
            "created_at": datetime(2026, 9, 1), "pr_id": "pr1", "vendor_name": "Supply Co",
            "lines": [{"description": "Chair", "quantity": Decimal("10"), "unit": "pcs", "unit_price": Decimal("1250"), "total": Decimal("12500")}],
            "grns": [{"received_date": date(2026, 9, 20), "status": "accepted", "notes": "All good"}],
            "invoices": [{"invoice_number": "S-1", "invoice_date": date(2026, 9, 21), "status": "pending", "amount": Decimal("12500")}],
            "requisition": {"title": "Chairs for HQ", "department": "Admin"}}


def _expense():
    return {"expense_id": "e1", "expense_date": date(2026, 8, 5), "description": "Chairs", "category": "office_supplies",
            "receipt_number": "R-9", "gross_amount": 11500.0, "vat_amount": 1500.0, "net_amount": 10000.0, "vat_type": "standard"}


def _doc(status="received"):
    return {"id": "d1", "kind": "invoice", "filename": "inv.pdf", "size": 204800, "related_ref": "PO-1", "status": status,
            "staff_note": "" if status == "received" else "OK", "uploaded_at": datetime(2026, 9, 4, 8),
            "email": "s@supply.et", "org_name": "Supply Co"}


def _rfq(status="open"):
    return {"id": "r1", "title": "Laptops x20", "description": "20 business laptops, 3y warranty", "due_at": datetime(2026, 9, 30, 17),
            "status": status, "created_by": "admin", "created_at": datetime(2026, 9, 1), "invited_count": 3, "response_count": 1,
            "invitation_status": "invited", "my_amount": None, "submitted_at": None}


def _response():
    return {"id": "resp1", "amount": Decimal("450000"), "currency": "ETB", "delivery_days": 14, "notes": "Valid 30 days",
            "attachment_path": "/tmp/x.pdf", "submitted_at": datetime(2026, 9, 5), "email": "s@supply.et", "org_name": "Supply Co"}


def _puser(**kw):
    u = {"id": "pu1", "kind": "customer", "email": "c@acme.et", "full_name": "Chaltu A.", "org_name": "Acme PLC",
         "tin": "0012", "phone": "", "party_key": "Acme PLC", "is_active": True, "has_password": True,
         "is_locked": False, "failed_attempts": 0, "last_login_at": datetime(2026, 9, 1), "created_at": datetime(2026, 8, 1)}
    u.update(kw)
    return u


# (template, user, ctx_fn)
CASES = [
    # public
    ("portal/login.html", None, lambda: dict(next="/portal/", email="")),
    ("portal/login.html", None, lambda: dict(next="/portal/", email="c@acme.et", error="Incorrect e-mail or password.")),
    ("portal/forgot.html", None, lambda: dict(sent=False, email="")),
    ("portal/forgot.html", None, lambda: dict(sent=True, email="c@acme.et")),
    ("portal/reset.html", None, lambda: dict(token="t", valid=False, email="")),
    ("portal/reset.html", None, lambda: dict(token="t", valid=True, email="c@acme.et", error="Too short")),
    ("portal/accept_invite.html", None, lambda: dict(token="t", valid=False, invite={})),
    ("portal/accept_invite.html", None, lambda: dict(token="t", valid=True, invite=_puser())),
    # portal — customer
    ("portal/home.html", CUSTOMER, lambda: dict(invoice_totals=_totals([]), recent_invoices=[], cpo_count=0, open_tickets=0)),
    ("portal/home.html", CUSTOMER, lambda: dict(invoice_totals=_totals([_invoice()]), recent_invoices=[_invoice()], cpo_count=2, open_tickets=1)),
    ("portal/customer_invoices.html", CUSTOMER, lambda: dict(invoices=[], totals=_totals([]))),
    ("portal/customer_invoices.html", CUSTOMER, lambda: dict(invoices=[_invoice(), _invoice()], totals=_totals([_invoice(), _invoice()]))),
    ("portal/customer_invoice_detail.html", CUSTOMER, lambda: dict(invoice=_invoice())),
    ("portal/customer_orders.html", CUSTOMER, lambda: dict(cpos=[], bookings=[])),
    ("portal/customer_orders.html", CUSTOMER, lambda: dict(
        cpos=[{"id": "c1", "name": "Acme PLC", "date": "2026-07-01", "amount": 5000, "bid_name": "Tender 7", "is_returned": "false", "returned_date": ""},
              {"id": "c2", "name": "Acme PLC", "date": "2026-06-01", "amount": 800, "bid_name": "", "is_returned": "true", "returned_date": "2026-08-01"}],
        bookings=[{"id": "b1", "event_name": "Launch", "venue_name": "Hall A", "event_start": datetime(2026, 10, 2, 9), "status": "confirmed", "total_amount": 30000}])),
    ("portal/customer_projects.html", CUSTOMER, lambda: dict(projects=[], contracts=[])),
    ("portal/customer_projects.html", CUSTOMER, lambda: dict(
        projects=[{"id": "p1", "name": "ERP rollout", "classification": "external", "status": "active", "start_date": date(2026, 1, 1), "end_date": None, "total_budget": 1}],
        contracts=[{"id": "k1", "title": "Support SLA", "contract_type": "service", "value": Decimal("120000"), "currency": "ETB", "start_date": date(2026, 1, 1), "end_date": date(2026, 12, 31), "status": "active"}])),
    ("portal/customer_tickets.html", CUSTOMER, lambda: dict(tickets=[], ticket=None)),
    ("portal/customer_tickets.html", CUSTOMER, lambda: dict(tickets=[_ticket()], ticket=_ticket())),
    ("portal/customer_tickets.html", CUSTOMER, lambda: dict(tickets=[_ticket()], ticket=_ticket(with_msgs=False))),
    ("portal/customer_ticket_new.html", CUSTOMER, lambda: dict(priorities=("low", "normal", "high"), form={})),
    ("portal/customer_ticket_new.html", CUSTOMER, lambda: dict(priorities=("low", "normal", "high"), form={"subject": "x", "body": "", "priority": "high"}, error="Subject and message are required.")),
    ("portal/profile.html", CUSTOMER, lambda: dict(account=_puser())),
    # portal — supplier
    ("portal/home.html", SUPPLIER, lambda: dict(open_orders=[], order_count=0, open_rfqs=[], pending_docs=0, open_tickets=0)),
    ("portal/home.html", SUPPLIER, lambda: dict(open_orders=[_order()], order_count=3, open_rfqs=[_rfq()], pending_docs=1, open_tickets=0)),
    ("portal/supplier_orders.html", SUPPLIER, lambda: dict(orders=[], requisitions=[], contracts=[])),
    ("portal/supplier_orders.html", SUPPLIER, lambda: dict(orders=[_order()],
        requisitions=[{"id": "pr1", "title": "Chairs for HQ", "department": "Admin", "status": "approved", "total_amount": Decimal("12500"), "created_at": datetime(2026, 8, 1)}],
        contracts=[{"id": "k2", "title": "Supply framework", "contract_type": "supply", "value": 1, "currency": "ETB", "start_date": None, "end_date": None, "status": "draft"}])),
    ("portal/supplier_order_detail.html", SUPPLIER, lambda: dict(order=_order())),
    ("portal/supplier_order_detail.html", SUPPLIER, lambda: dict(order={**_order(), "lines": [], "grns": [], "invoices": [], "requisition": None})),
    ("portal/supplier_invoices.html", SUPPLIER, lambda: dict(expenses=[], totals=_totals([]), proc_invoices=[])),
    ("portal/supplier_invoices.html", SUPPLIER, lambda: dict(expenses=[_expense()], totals=_totals([_expense()]),
        proc_invoices=[{"id": "i1", "invoice_number": "S-1", "invoice_date": date(2026, 9, 21), "status": "matched", "po_id": "po-1", "po_title": "Office chairs", "amount": 12500}])),
    ("portal/supplier_documents.html", SUPPLIER, lambda: dict(documents=[], kinds=("quote", "invoice", "delivery_note", "other"), max_mb=10)),
    ("portal/supplier_documents.html", SUPPLIER, lambda: dict(documents=[_doc(), _doc("accepted"), _doc("rejected")], kinds=("quote", "invoice", "delivery_note", "other"), max_mb=10)),
    ("portal/supplier_rfqs.html", SUPPLIER, lambda: dict(rfqs=[], now=datetime.now())),
    ("portal/supplier_rfqs.html", SUPPLIER, lambda: dict(rfqs=[_rfq(), {**_rfq("closed"), "invitation_status": "responded", "my_amount": 1000}, {**_rfq(), "invitation_status": "declined"}], now=datetime.now())),
    ("portal/supplier_rfq_respond.html", SUPPLIER, lambda: dict(rfq=_rfq(), response={}, now=datetime.now())),
    ("portal/supplier_rfq_respond.html", SUPPLIER, lambda: dict(rfq={**_rfq(), "invitation_status": "responded", "my_response": _response()}, response=_response(), now=datetime.now())),
    ("portal/supplier_rfq_respond.html", SUPPLIER, lambda: dict(rfq=_rfq("closed"), response={}, now=datetime.now())),
    # staff admin
    ("portal_admin/users.html", None, lambda: dict(users=[], kind_filter="", stats={}, ticket_stats={}, document_stats={}, audit=[])),
    ("portal_admin/users.html", None, lambda: dict(
        users=[_puser(), _puser(id="pu2", kind="supplier", has_password=False, is_locked=True, failed_attempts=10), _puser(id="pu3", is_active=False)],
        kind_filter="customer",
        stats={"customer": {"total": 2, "activated": 1, "locked": 0}, "supplier": {"total": 1, "activated": 0, "locked": 1}},
        ticket_stats={"open": 1, "in_progress": 0, "resolved": 0, "closed": 0},
        document_stats={"received": 2, "accepted": 0, "rejected": 0},
        audit=[{"created_at": datetime(2026, 9, 5), "email": "c@acme.et", "kind": "customer", "action": "login", "ip": "1.2.3.4"}])),
    ("portal_admin/user_form.html", None, lambda: dict(user={"kind": "customer", "is_active": True}, kinds=("customer", "supplier"), is_new=True)),
    ("portal_admin/user_form.html", None, lambda: dict(user={"kind": "supplier", "email": "x"}, kinds=("customer", "supplier"), is_new=True, error="A valid e-mail address is required.")),
    ("portal_admin/user_form.html", None, lambda: dict(user=_puser(has_password=False, failed_attempts=3), kinds=("customer", "supplier"), is_new=False)),
    ("portal_admin/invite_sent.html", None, lambda: dict(user=_puser(), link="https://x/portal/accept-invite?token=abc", email_sent=False, mode="invite", expires_hours=72)),
    ("portal_admin/invite_sent.html", None, lambda: dict(user=_puser(), link="https://x/portal/reset?token=abc", email_sent=True, mode="reset", expires_hours=1)),
    ("portal_admin/tickets.html", None, lambda: dict(tickets=[], status_filter="", statuses=("open", "in_progress", "resolved", "closed"), stats={})),
    ("portal_admin/tickets.html", None, lambda: dict(tickets=[_ticket(), {**_ticket(), "id": "t2", "status": "open", "priority": "high"}], status_filter="open",
                                                      statuses=("open", "in_progress", "resolved", "closed"), stats={"open": 1, "in_progress": 1, "resolved": 0, "closed": 0})),
    ("portal_admin/ticket_detail.html", None, lambda: dict(ticket=_ticket(), statuses=("open", "in_progress", "resolved", "closed"))),
    ("portal_admin/ticket_detail.html", None, lambda: dict(ticket=_ticket(with_msgs=False), statuses=("open", "in_progress", "resolved", "closed"))),
    ("portal_admin/rfqs.html", None, lambda: dict(rfqs=[])),
    ("portal_admin/rfqs.html", None, lambda: dict(rfqs=[_rfq(), _rfq("awarded"), _rfq("closed")])),
    ("portal_admin/rfq_form.html", None, lambda: dict(rfq={}, invitations=[], responses=[], suppliers=[], statuses=("open", "closed", "awarded"))),
    ("portal_admin/rfq_form.html", None, lambda: dict(rfq={"title": "", "description": "x", "due_at": ""}, invitations=[], responses=[], suppliers=[], statuses=("open", "closed", "awarded"), error="Title is required.")),
    ("portal_admin/rfq_form.html", None, lambda: dict(rfq=_rfq(),
        invitations=[{"portal_user_id": "pu2", "status": "invited", "email": "s@supply.et", "org_name": "Supply Co", "full_name": ""},
                     {"portal_user_id": "pu3", "status": "responded", "email": "t@other.et", "org_name": "", "full_name": "Tom"}],
        responses=[_response(), {**_response(), "id": "resp2", "attachment_path": None, "delivery_days": None}],
        suppliers=[_puser(id="pu4", kind="supplier", has_password=False)], statuses=("open", "closed", "awarded"))),
    ("portal_admin/documents.html", None, lambda: dict(documents=[], status_filter="", statuses=("received", "accepted", "rejected"), stats={})),
    ("portal_admin/documents.html", None, lambda: dict(documents=[_doc(), _doc("accepted"), _doc("rejected")], status_filter="received",
                                                        statuses=("received", "accepted", "rejected"), stats={"received": 1, "accepted": 1, "rejected": 1})),
]


@pytest.mark.parametrize("template,user,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _, _) in enumerate(CASES)])
def test_portal_template_renders(template, user, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(user), **ctx_fn())
    assert len(html) > 800
    assert "Saved" in html                      # flash messages are surfaced on every page


def test_portal_base_is_self_contained_and_public():
    src = (_WEB_DIR / "templates" / "portal" / "base.html").read_text(encoding="utf-8")
    assert "multicompany/base.html" not in src and 'extends "base.html"' not in src
    assert 'name="viewport"' in src
    assert "noindex" in src
    assert "<style>" in src and "<link" not in src        # no external CSS / staff assets


def test_every_portal_post_form_carries_csrf_field():
    """Every <form method="post"> in portal templates must include the hidden csrf_token."""
    import re
    for folder in ("portal", "portal_admin"):
        for path in sorted((_WEB_DIR / "templates" / folder).glob("*.html")):
            src = path.read_text(encoding="utf-8")
            forms = re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', src, flags=re.S | re.I)
            for body in forms:
                assert 'name="csrf_token"' in body, f"{path.name}: POST form without csrf_token"


def test_anonymous_pages_do_not_expose_navigation():
    html = env.get_template("portal/login.html").render(**_base_ctx(None), next="/portal/", email="")
    assert "Sign out" not in html and 'class="p-nav"' not in html
    assert "Acme Trading PLC" in html and "EBMS" in html


def test_supplier_and_customer_navs_differ():
    c = env.get_template("portal/profile.html").render(**_base_ctx(CUSTOMER), account=_puser())
    s = env.get_template("portal/profile.html").render(**_base_ctx(SUPPLIER), account=_puser(kind="supplier"))
    assert "Orders &amp; CPOs" in c and "RFQs" not in c
    assert "RFQs" in s and "Orders &amp; CPOs" not in s


def test_formatters():
    assert fmt_date(date(2026, 9, 12)) == "12 Sep 2026"
    assert fmt_date("2026-09-12T10:00:00") == "12 Sep 2026"
    assert fmt_date("") == "" and fmt_date(None) == ""
    assert fmt_dt(datetime(2026, 9, 12, 14, 5)) == "12 Sep 2026 14:05"
    assert fmt_money(1234.5) == "1,234.50"
    assert fmt_money(Decimal("10"), "ETB") == "10.00 ETB"
    assert fmt_money(None) == "0.00"
    assert fmt_size(204800) == "200.0 KB" and fmt_size(0) == "0 B"
