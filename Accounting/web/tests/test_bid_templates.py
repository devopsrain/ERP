"""
Template render tests — Bid Tracker module.

Same stub-harness pattern as test_vat_templates.py: render every touched bid
template with route-accurate contexts, both EMPTY and POPULATED (including a
bid with and without contract_date/delivery_days), so undefined variables or
wrong field names fail the build instead of 500ing in production. No DB.
"""
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))


def _base_ctx(path="/bid/x"):
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


def _bid(**kw):
    """A bid dict as the routes hand it to the templates."""
    b = dict(
        id="b1", company_id="default", title="IT Infrastructure Upgrade",
        reference_number="RFP-2026-0001", organization="Ministry of Finance",
        description="Desc", category="IT & Technology", status="Open",
        deadline="2026-08-01T12:00", submission_date="",
        bid_amount=125000.0, currency="ETB",
        case_handler_name="Abebe Kebede", case_handler_email="a@example.com",
        reminder_days_before=3, reminder_sent=False, notes="Some notes",
        created_at="2026-07-01T09:00:00", updated_at="2026-07-02T09:00:00",
        document_count=0,
        # New fields default to empty (mirrors rows created before migration)
        contract_date=None, delivery_days=0,
        # Route-computed delivery countdown
        due_date=None, overdue=False,
    )
    b.update(kw)
    return b


def _bid_with_delivery(overdue=False):
    cd = date.today() - timedelta(days=30 if overdue else 1)
    return _bid(contract_date=cd, delivery_days=10,
                due_date=cd + timedelta(days=10), overdue=overdue)


def _stats(**kw):
    s = dict(total=0, draft=0, open=0, in_progress=0, submitted=0,
             won=0, lost=0, upcoming_deadlines=0, total_value=0.0, by_status={})
    s.update(kw)
    return s


def _doc_groups(docs=()):
    g = {t: [] for t in ["original_bid", "technical", "financial", "supporting", "other"]}
    for d in docs:
        g[d.get("doc_type", "other")].append(d)
    return g


def _doc():
    return dict(id="d1", bid_id="b1", filename="d1.pdf",
                original_filename="proposal.pdf", doc_type="technical",
                description="Tech proposal", uploaded_by="FDE",
                file_size=1024, file_size_display="1.0 KB",
                uploaded_at="2026-07-03T10:00:00")


CASES = [
    # dashboard: empty, and populated with/without new fields (incl. overdue)
    ("bid/dashboard.html", lambda: dict(stats=_stats(), bids=[])),
    ("bid/dashboard.html", lambda: dict(
        stats=_stats(total=3, open=2, won=1),
        bids=[_bid(), _bid_with_delivery(), _bid_with_delivery(overdue=True)])),
    # add form: blank, and re-rendered with (string) form data
    ("bid/add_bid.html", lambda: dict(bid={})),
    ("bid/add_bid.html", lambda: dict(bid=dict(
        _bid(), contract_date="2026-07-15", delivery_days="14"))),
    # edit form: bid without and with the new fields
    ("bid/edit_bid.html", lambda: dict(bid=_bid())),
    ("bid/edit_bid.html", lambda: dict(bid=_bid_with_delivery())),
    # view page: without/with new fields (+overdue), and with documents
    ("bid/view_bid.html", lambda: dict(bid=_bid(), doc_groups=_doc_groups())),
    ("bid/view_bid.html", lambda: dict(bid=_bid_with_delivery(overdue=True),
                                       doc_groups=_doc_groups([_doc()]))),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_bid_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000  # sanity: a real page came out
