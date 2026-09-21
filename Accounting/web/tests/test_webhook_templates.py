"""
Template render tests — Webhooks & API keys module.

Same harness as test_vat_templates.py: every template is rendered with a
route-accurate context, both EMPTY and POPULATED, with no database. Also
checks that every url_for('webhook.*') used by a template resolves to a real
route name and that static routes are registered before parametrised ones.
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api_keys import SCOPES  # noqa: E402
from webhook_data_store import MAX_ATTEMPTS, STANDARD_EVENTS  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))
NOW = datetime(2026, 9, 11, 10, 0, 0)


def _base_ctx(path="/webhooks/x"):
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


def _endpoint(**kw):
    d = dict(id="ep-1", company_id="default", url="https://receiver.example/ebms",
             description="ERP sync", secret="whsec_abc123", events=["invoice.*", "bid.won", "custom.thing"],
             is_active=True, created_by="admin", created_at=NOW, updated_at=NOW,
             delivery_count=12, failure_count=2, last_success_at=NOW)
    d.update(kw)
    return d


def _attempt(ok=False):
    return {"at": "2026-09-11T10:00:00Z", "ok": ok, "status_code": 200 if ok else 503,
            "error": None if ok else "HTTP 503", "duration_ms": 120,
            "request_headers": {"X-EBMS-Signature": "t=1,v1=ab", "X-EBMS-Event": "invoice.created",
                                "X-EBMS-Delivery-Id": "d-1"},
            "response": "" if ok else "Service Unavailable"}


def _delivery(**kw):
    d = dict(id="d-1", endpoint_id="ep-1", company_id="default", event="invoice.created",
             payload={"invoice_id": "INV-1", "total": 10.5}, status="failed", attempts=2,
             next_attempt_at=NOW + timedelta(minutes=4), last_status_code=503, last_error="HTTP 503",
             last_response="Service Unavailable", attempt_log=[_attempt(), _attempt()],
             created_at=NOW, delivered_at=None, endpoint_url="https://receiver.example/ebms",
             endpoint_description="ERP sync")
    d.update(kw)
    return d


def _key(**kw):
    d = dict(id="k-1", company_id="default", name="Power BI", prefix="ebms_abcdefg", scopes=["read", "invoices:*"],
             created_by="admin", created_at=NOW, last_used_at=None, expires_at=None, revoked_at=None)
    d.update(kw)
    return d


def _stats(**kw):
    s = {"endpoints": 0, "endpoints_active": 0, "api_keys_active": 0, "pending": 0, "failed": 0,
         "success": 0, "dead": 0, "total": 0, "last24h_success": 0, "last24h_failed": 0, "inbound_24h": 0}
    s.update(kw)
    return s


def _inbound():
    return {"id": "i-1", "company_id": "default", "source": "telebirr", "verified": True,
            "received_at": NOW, "body_preview": '{"txn":"123"}'}


CASES = [
    ("webhooks/dashboard.html", lambda: dict(stats=_stats(), failures=[], endpoints=[], inbound=[],
                                             inbound_secrets=[], max_attempts=MAX_ATTEMPTS)),
    ("webhooks/dashboard.html", lambda: dict(
        stats=_stats(endpoints=2, endpoints_active=1, success=40, failed=2, dead=1, pending=3, api_keys_active=2),
        failures=[_delivery(), _delivery(id="d-2", status="dead", attempts=6)],
        endpoints=[_endpoint(), _endpoint(id="ep-2", is_active=False, description="")],
        inbound=[_inbound(), {**_inbound(), "verified": False, "id": "i-2"}],
        inbound_secrets=[{"id": "s-1", "company_id": "default", "source": "telebirr", "secret": "whsec_xyz", "created_at": NOW}],
        max_attempts=MAX_ATTEMPTS)),
    ("webhooks/endpoints.html", lambda: dict(endpoints=[])),
    ("webhooks/endpoints.html", lambda: dict(endpoints=[_endpoint(), _endpoint(id="ep-2", is_active=False, events=[],
                                                                                failure_count=0, last_success_at=None)])),
    ("webhooks/endpoint_form.html", lambda: dict(endpoint={}, standard_events=STANDARD_EVENTS, errors=[], is_edit=False)),
    ("webhooks/endpoint_form.html", lambda: dict(endpoint={"url": "x", "events": ["*"]}, standard_events=STANDARD_EVENTS,
                                                 errors=["URL must start with http:// or https://"], is_edit=False)),
    ("webhooks/endpoint_form.html", lambda: dict(endpoint=_endpoint(), standard_events=STANDARD_EVENTS, errors=[],
                                                 is_edit=True, recent=[_delivery(), _delivery(id="d-3", status="success",
                                                                                              last_status_code=200)])),
    ("webhooks/endpoint_form.html", lambda: dict(endpoint=_endpoint(), standard_events=STANDARD_EVENTS, errors=[],
                                                 is_edit=True, recent=[])),
    ("webhooks/deliveries.html", lambda: dict(deliveries=[], endpoints=[], events_seen=[],
                                              statuses=("pending", "failed", "success", "dead"),
                                              status_filter="", endpoint_filter="", event_filter="")),
    ("webhooks/deliveries.html", lambda: dict(
        deliveries=[_delivery(), _delivery(id="d-2", status="success", delivered_at=NOW, last_error=None,
                                           last_status_code=200),
                    _delivery(id="d-3", status="pending", attempts=0, last_status_code=None, last_error=None),
                    _delivery(id="d-4", status="dead", attempts=6)],
        endpoints=[_endpoint()], events_seen=["invoice.created", "bid.won"],
        statuses=("pending", "failed", "success", "dead"),
        status_filter="failed", endpoint_filter="ep-1", event_filter="invoice.created")),
    ("webhooks/delivery_detail.html", lambda: dict(delivery=_delivery(status="pending", attempts=0, attempt_log=[],
                                                                      last_status_code=None, last_error=None),
                                                   payload_json="{}", attempts=[], max_attempts=MAX_ATTEMPTS)),
    ("webhooks/delivery_detail.html", lambda: dict(delivery=_delivery(), payload_json='{\n  "invoice_id": "INV-1"\n}',
                                                   attempts=[_attempt(), _attempt(ok=True)], max_attempts=MAX_ATTEMPTS)),
    ("webhooks/api_keys.html", lambda: dict(keys=[], scopes=SCOPES, now=NOW)),
    ("webhooks/api_keys.html", lambda: dict(keys=[_key(), _key(id="k-2", revoked_at=NOW),
                                                  _key(id="k-3", expires_at=NOW - timedelta(days=1)),
                                                  _key(id="k-4", expires_at=NOW + timedelta(days=30), last_used_at=NOW)],
                                            scopes=SCOPES, now=NOW)),
    ("webhooks/api_key_created.html", lambda: dict(key=_key(), plaintext="ebms_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                                                   base_url="https://ebms.example")),
    ("webhooks/api_key_created.html", lambda: dict(key={}, plaintext="", base_url="")),
    ("webhooks/docs.html", lambda: dict(standard_events=STANDARD_EVENTS, scopes=SCOPES, base_url="https://ebms.example",
                                        max_attempts=MAX_ATTEMPTS)),
    ("webhooks/docs.html", lambda: dict(standard_events=[], scopes=[], base_url="", max_attempts=MAX_ATTEMPTS)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_webhook_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000
    assert "nav-link active" in html   # sub-nav highlights the current page (include sees block-scoped `active`)


def test_docs_page_lists_catalogue_and_both_snippets():
    html = env.get_template("webhooks/docs.html").render(
        **_base_ctx(), standard_events=STANDARD_EVENTS, scopes=SCOPES, base_url="", max_attempts=MAX_ATTEMPTS)
    for name, _ in STANDARD_EVENTS:
        assert name in html
    assert "hmac.compare_digest" in html          # python snippet
    assert "crypto.timingSafeEqual" in html       # node snippet
    assert "X-EBMS-Signature" in html and "X-API-Key" in html


def test_populated_pages_show_data():
    html = env.get_template("webhooks/deliveries.html").render(
        **_base_ctx(), deliveries=[_delivery()], endpoints=[_endpoint()], events_seen=["invoice.created"],
        statuses=("pending", "failed", "success", "dead"), status_filter="", endpoint_filter="", event_filter="")
    assert "invoice.created" in html and "HTTP 503" in html
    html = env.get_template("webhooks/api_key_created.html").render(
        **_base_ctx(), key=_key(), plaintext="ebms_PLAINTEXT", base_url="")
    assert "ebms_PLAINTEXT" in html
    html = env.get_template("webhooks/endpoint_form.html").render(
        **_base_ctx(), endpoint=_endpoint(), standard_events=STANDARD_EVENTS, errors=[], is_edit=True, recent=[])
    assert 'value="bid.won" id="ev_b1" checked' in html      # standard event ticked
    assert 'value="invoice.*, custom.thing"' in html          # non-standard patterns land in the free-text box
    assert "whsec_abc123" in html                             # secret visible on the edit page


# ── Route wiring guards ───────────────────────────────────────────────────────

def _route_names_and_paths():
    from webhook_routes import router
    return [(getattr(r, "name", None), getattr(r, "path", "")) for r in router.routes]


def test_every_template_url_for_has_a_matching_route():
    names = {n for n, _ in _route_names_and_paths()}
    used = set()
    for tpl in (_WEB_DIR / "templates" / "webhooks").glob("*.html"):
        used |= set(re.findall(r"url_for\('webhook\.([a-z_]+)'", tpl.read_text(encoding="utf-8")))
    assert used, "templates should link to webhook routes"
    missing = {"webhook_" + u for u in used} - names
    assert not missing, f"templates reference unknown routes: {sorted(missing)}"


def test_static_routes_precede_parametrised_ones():
    paths = [p for _, p in _route_names_and_paths()]
    assert paths.index("/webhooks/endpoints/new") < paths.index("/webhooks/endpoints/{endpoint_id}/edit")
    assert paths.index("/webhooks/deliveries") < paths.index("/webhooks/deliveries/{delivery_id}")
    assert paths.index("/webhooks/api-keys/new") < paths.index("/webhooks/api-keys/{key_id}/revoke")
    assert paths.index("/webhooks/docs") < paths.index("/webhooks/inbound/{source}")
    assert "/webhooks/inbound-secrets" in paths and "/webhooks/inbound-secrets/{secret_id}/delete" in paths


def test_management_routes_require_admin_and_inbound_is_open():
    from webhook_routes import router
    from deps import admin_required
    for r in router.routes:
        dep_fns = [d.call for d in getattr(r, "dependant", SimpleNamespace(dependencies=[])).dependencies]
        if r.path.startswith("/webhooks/inbound/"):
            assert admin_required not in dep_fns, "inbound receiver must be public"
            assert {"POST", "PUT"} <= set(r.methods)
        else:
            assert admin_required in dep_fns, f"{r.path} must be admin_required"
