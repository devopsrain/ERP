"""
Template render tests — Approval Engine module.

Renders every approvals template with route-accurate contexts, both EMPTY and
POPULATED, so undefined variables / wrong field names fail the build instead
of 500ing in production. No database required (same harness as the VAT tests).
"""
import re
import sys
from datetime import datetime, date, timedelta
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

from approval_data_store import (  # noqa: E402
    ACTIONS, APPROVER_TYPES, ENTITY_TYPES, ROLE_CHOICES, STATUSES, decorate_request,
)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))

MAX_STEPS = 6


def _base_ctx(path="/approvals/", is_admin=True):
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
        # added by approval_routes._ctx
        active_page="dashboard", is_admin=is_admin, current_username="alice",
        escalation_days=3,
    )


# ── fixtures ──────────────────────────────────────────────────────

def _users():
    return [
        {"username": "alice", "full_name": "Alice A", "email": "a@x.et", "privilege_level": "manager"},
        {"username": "bob", "full_name": "", "email": "", "privilege_level": "admin"},
    ]


def _step(order, a_type="role", a_value="manager", require_all=False, sla=None, name=""):
    return {"id": f"s{order}", "workflow_id": "wf1", "step_order": order, "name": name,
            "approver_type": a_type, "approver_value": a_value, "require_all": require_all,
            "sla_hours": sla}


def _workflow(active=True, steps=None, lo=Decimal("0"), hi=Decimal("50000")):
    return {"id": "wf1", "company_id": "default", "name": "Purchases up to 50k",
            "entity_type": "purchase_requisition", "description": "Small purchases",
            "min_amount": lo, "max_amount": hi, "currency": "ETB", "is_active": active,
            "created_by": "bob", "created_at": datetime(2026, 9, 1, 8, 0),
            "steps": steps if steps is not None else [
                _step(1, "manager_of_requester", "", name="Line manager", sla=24),
                _step(2, "role", "admin", require_all=True), _step(3, "user", "bob")]}


def _action(action="approve", actor="alice", step=1, comment="ok", on_behalf_of="", delegate_to=""):
    return {"id": "a1", "request_id": "r1", "step_order": step, "actor": actor,
            "on_behalf_of": on_behalf_of, "action": action, "comment": comment,
            "delegate_to": delegate_to, "created_at": datetime(2026, 9, 2, 9, 30),
            "created_display": "2026-09-02 09:30"}


def _request(status="pending", actions=None, payload=None, amount=Decimal("12500.5"),
             created=None, acting_as=None):
    req = {"id": "r1", "company_id": "default", "workflow_id": "wf1", "workflow_name": "Purchases up to 50k",
           "entity_type": "purchase_requisition", "entity_id": "PR-001", "title": "Laptops for finance",
           "amount": amount, "currency": "ETB", "requested_by": "carol", "status": status,
           "current_step": 2, "payload": payload if payload is not None else {"vendor": "Acme", "items": 3},
           "created_at": created or (datetime.now() - timedelta(days=5)),
           "updated_at": datetime.now(), "decided_at": datetime.now() if status != "pending" else None,
           "steps": _workflow()["steps"],
           "actions": actions if actions is not None else [
               _action(), _action("comment", "dave", 1, "please hurry"),
               _action("delegate", "bob", 2, "", delegate_to="erin"),
               _action("approve", "erin", 2, "", on_behalf_of="bob")]}
    decorate_request(req)
    if acting_as:
        req["acting_as"] = acting_as
    return req


def _stats(**kw):
    s = {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "cancelled": 0,
         "overdue": 0, "my_pending": 0, "my_requests_pending": 0, "workflows_active": 0}
    s.update(kw); return s


def _delegation(current=True):
    return {"id": "d1", "company_id": "default", "from_user": "alice", "to_user": "bob",
            "starts_on": date.today(), "ends_on": None if current else date.today() - timedelta(days=1),
            "note": "leave", "is_current": current}


def _padded(steps):
    rows = [dict(s) for s in steps][:MAX_STEPS]
    while len(rows) < MAX_STEPS:
        rows.append({"step_order": len(rows) + 1, "name": "", "approver_type": "role",
                     "approver_value": "", "require_all": False, "sla_hours": None})
    return rows


def _form_ctx(workflow, is_edit):
    return dict(workflow=workflow, steps=_padded(workflow.get("steps", [])), entity_types=ENTITY_TYPES,
                approver_types=APPROVER_TYPES, roles=ROLE_CHOICES, users=_users(), is_edit=is_edit,
                max_steps=MAX_STEPS)


CASES = [
    ("approvals/dashboard.html", lambda: dict(
        stats=_stats(total=3, pending=2, approved=1, overdue=1, my_pending=1, workflows_active=1),
        my_pending=[_request(acting_as="bob")], my_requests=[_request("approved"), _request("rejected")],
        recent=[_request(), _request("cancelled")], delegations=[_delegation(), _delegation(False)],
        users=_users())),
    ("approvals/dashboard.html", lambda: dict(
        stats=_stats(), my_pending=[], my_requests=[], recent=[], delegations=[], users=[])),
    ("approvals/workflows.html", lambda: dict(
        workflows=[_workflow(), _workflow(active=False, steps=[], lo=None, hi=None)], entity_types=ENTITY_TYPES)),
    ("approvals/workflows.html", lambda: dict(workflows=[], entity_types=ENTITY_TYPES)),
    ("approvals/workflow_form.html", lambda: _form_ctx(_workflow(), True)),
    ("approvals/workflow_form.html", lambda: _form_ctx({}, False)),
    ("approvals/inbox.html", lambda: dict(requests=[_request(acting_as="bob"), _request(payload={}, amount=None)],
                                          users=_users())),
    ("approvals/inbox.html", lambda: dict(requests=[], users=[])),
    ("approvals/request_detail.html", lambda: dict(
        req=_request(), can_act=True, is_requester=False, approvers=["bob", "erin"], users=_users(), actions=ACTIONS)),
    ("approvals/request_detail.html", lambda: dict(
        req=_request("pending", actions=[], payload={}), can_act=False, is_requester=True, approvers=[],
        users=[], actions=ACTIONS)),
    ("approvals/request_detail.html", lambda: dict(
        req=_request("rejected"), can_act=False, is_requester=False, approvers=[], users=[], actions=ACTIONS)),
    ("approvals/history.html", lambda: dict(
        requests=[_request(), _request("approved"), _request("rejected"), _request("cancelled")],
        status_filter="pending", entity_filter="expense", statuses=STATUSES, entity_types=ENTITY_TYPES)),
    ("approvals/history.html", lambda: dict(
        requests=[], status_filter="", entity_filter="", statuses=STATUSES, entity_types=ENTITY_TYPES)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_approval_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000
    assert 'class="module-sidebar"' in html
    assert 'class="content-with-sidebar"' in html


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_approval_template_renders_for_non_admin(template, ctx_fn):
    """Non-admin sessions must render too (workflow links hidden, nothing undefined)."""
    html = env.get_template(template).render(**_base_ctx(is_admin=False), **ctx_fn())
    assert len(html) > 1000


def test_every_template_url_for_name_is_a_real_route():
    """url_for('approval.x') resolves to route name 'approval_x' — catch typos."""
    import approval_routes
    names = {getattr(r, "name", None) for r in approval_routes.router.routes}
    used = set()
    for path in (_WEB_DIR / "templates" / "approvals").glob("*.html"):
        used |= set(re.findall(r"url_for\('approval\.(\w+)'", path.read_text(encoding="utf-8")))
    assert used, "no url_for usages found"
    missing = {f"approval_{u}" for u in used} - names
    assert not missing, f"templates reference unknown routes: {sorted(missing)}"


def test_no_top_level_document_ready():
    for path in (_WEB_DIR / "templates" / "approvals").glob("*.html"):
        assert "$(document).ready" not in path.read_text(encoding="utf-8"), path.name
