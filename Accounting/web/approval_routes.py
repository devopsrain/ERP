"""
Approval Engine Routes — /approvals

Pages: dashboard, workflows (admin), inbox, request detail, history.
JSON:  GET  /approvals/api/status/{entity_type}/{entity_id}
       POST /approvals/api/submit   (form or JSON; login required)

Other modules should call ``approval_store.submit(...)`` / ``status_of(...)``
directly from Python rather than going through the HTTP API.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from deps import admin_required, current_company, flash, login_required, template_context
from template_engine import templates
from approval_data_store import (
    ACTIONS, APPROVER_TYPES, ENTITY_TYPES, ESCALATION_DAYS, ROLE_CHOICES, STATUSES,
    approval_store, sorted_steps,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/approvals", tags=["approvals"])

MAX_STEPS = 6
_ADMIN_LEVELS = ("admin", "super_admin")


# ── helpers ───────────────────────────────────────────────────────

def _username(request: Request) -> str:
    return request.session.get("username", "") or ""


def _roles(request: Request) -> list:
    roles = []
    for key in ("privilege_level", "role"):
        v = request.session.get(key)
        if v and v not in roles:
            roles.append(v)
    return roles


def _is_admin(request: Request) -> bool:
    return request.session.get("privilege_level") in _ADMIN_LEVELS


def _ctx(request: Request, active_page: str) -> dict:
    ctx = template_context(request)
    ctx.update(active_page=active_page, is_admin=_is_admin(request),
               current_username=_username(request), escalation_days=ESCALATION_DAYS)
    return ctx


def _parse_steps(form) -> list:
    """Read step_{i}_* fields; blank rows are dropped by the store."""
    steps = []
    for i in range(1, MAX_STEPS + 1):
        steps.append({
            "step_order": i,
            "name": form.get(f"step_{i}_name", ""),
            "approver_type": form.get(f"step_{i}_type", "role"),
            "approver_value": form.get(f"step_{i}_value", ""),
            "require_all": form.get(f"step_{i}_require_all", ""),
            "sla_hours": form.get(f"step_{i}_sla_hours", ""),
        })
    return steps


def _padded_steps(steps: list) -> list:
    rows = [dict(s) for s in sorted_steps(steps or [])][:MAX_STEPS]
    while len(rows) < MAX_STEPS:
        rows.append({"step_order": len(rows) + 1, "name": "", "approver_type": "role",
                     "approver_value": "", "require_all": False, "sla_hours": None})
    for i, r in enumerate(rows, start=1):
        r["step_order"] = i
    return rows


def _safe_next(request: Request, form, default: str) -> str:
    nxt = (form.get("next") or "").strip()
    return nxt if nxt.startswith("/approvals") else default


# ── Dashboard ─────────────────────────────────────────────────────

@router.get("/", name="approval_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    me = _username(request)
    roles = _roles(request)
    ctx = _ctx(request, "dashboard")
    my_pending = approval_store.pending_for(cid, me, roles)
    ctx.update(
        stats=approval_store.get_stats(cid, me, roles),
        my_pending=my_pending[:8],
        my_requests=approval_store.get_requests(cid, requested_by=me, limit=8),
        recent=approval_store.get_requests(cid, limit=8),
        delegations=approval_store.get_delegations(cid, username=me),
        users=approval_store.get_company_users(cid),
    )
    return templates.TemplateResponse("approvals/dashboard.html", ctx)


# ── Workflows (admin) — static paths before /{param} ─────────────

@router.get("/workflows", name="approval_workflows")
async def workflows(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ctx = _ctx(request, "workflows")
    ctx.update(workflows=approval_store.get_workflows(cid), entity_types=ENTITY_TYPES)
    return templates.TemplateResponse("approvals/workflows.html", ctx)


def _form_ctx(request: Request, workflow: dict, is_edit: bool) -> dict:
    cid = current_company(request)
    ctx = _ctx(request, "workflows")
    ctx.update(workflow=workflow or {}, steps=_padded_steps((workflow or {}).get("steps", [])),
               entity_types=ENTITY_TYPES, approver_types=APPROVER_TYPES, roles=ROLE_CHOICES,
               users=approval_store.get_company_users(cid), is_edit=is_edit, max_steps=MAX_STEPS)
    return ctx


@router.get("/workflows/new", name="approval_workflow_new_get")
async def workflow_new_get(request: Request, user=Depends(admin_required)):
    return templates.TemplateResponse("approvals/workflow_form.html", _form_ctx(request, {}, False))


@router.post("/workflows/new", name="approval_workflow_new_post")
async def workflow_new_post(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = _username(request)
    steps = _parse_steps(form)
    if not [s for s in steps if s["approver_value"] or s["approver_type"] == "manager_of_requester"]:
        flash(request, "Add at least one approval step", "error")
        return RedirectResponse("/approvals/workflows/new", status_code=303)
    wf = approval_store.create_workflow(cid, data, steps)
    if wf:
        flash(request, "Workflow created", "success")
        return RedirectResponse("/approvals/workflows", status_code=303)
    flash(request, "Failed to create workflow", "error")
    return RedirectResponse("/approvals/workflows/new", status_code=303)


@router.get("/workflows/{workflow_id}/edit", name="approval_workflow_edit_get")
async def workflow_edit_get(workflow_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    wf = approval_store.get_workflow(workflow_id, cid)
    if not wf:
        flash(request, "Workflow not found", "error")
        return RedirectResponse("/approvals/workflows", status_code=303)
    return templates.TemplateResponse("approvals/workflow_form.html", _form_ctx(request, wf, True))


@router.post("/workflows/{workflow_id}/edit", name="approval_workflow_edit_post")
async def workflow_edit_post(workflow_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if approval_store.update_workflow(workflow_id, cid, data, _parse_steps(form)):
        flash(request, "Workflow updated", "success")
        return RedirectResponse("/approvals/workflows", status_code=303)
    flash(request, "Failed to update workflow", "error")
    return RedirectResponse(f"/approvals/workflows/{workflow_id}/edit", status_code=303)


@router.post("/workflows/{workflow_id}/toggle", name="approval_workflow_toggle")
async def workflow_toggle(workflow_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    state = approval_store.toggle_workflow(workflow_id, cid)
    if state is None:
        flash(request, "Failed to update workflow", "error")
    else:
        flash(request, "Workflow activated" if state else "Workflow deactivated", "success")
    return RedirectResponse("/approvals/workflows", status_code=303)


# ── Inbox / history ───────────────────────────────────────────────

@router.get("/inbox", name="approval_inbox")
async def inbox(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _ctx(request, "inbox")
    ctx.update(requests=approval_store.pending_for(cid, _username(request), _roles(request)),
               users=approval_store.get_company_users(cid))
    return templates.TemplateResponse("approvals/inbox.html", ctx)


@router.get("/history", name="approval_history")
async def history(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    status = request.query_params.get("status") or None
    entity_type = request.query_params.get("entity_type") or None
    if status not in STATUSES:
        status = None
    if entity_type not in ENTITY_TYPES:
        entity_type = None
    ctx = _ctx(request, "history")
    ctx.update(requests=approval_store.get_requests(cid, status=status, entity_type=entity_type, limit=300),
               status_filter=status or "", entity_filter=entity_type or "",
               statuses=STATUSES, entity_types=ENTITY_TYPES)
    return templates.TemplateResponse("approvals/history.html", ctx)


# ── Delegations (managed from the dashboard) ─────────────────────

@router.post("/delegations", name="approval_delegation_create")
async def delegation_create(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    # Only admins may delegate on behalf of someone else
    if not _is_admin(request) or not data.get("from_user"):
        data["from_user"] = _username(request)
    if approval_store.create_delegation(cid, data):
        flash(request, "Delegation saved", "success")
    else:
        flash(request, "Failed to save delegation (choose a different user)", "error")
    return RedirectResponse("/approvals/", status_code=303)


@router.post("/delegations/{delegation_id}/delete", name="approval_delegation_delete")
async def delegation_delete(delegation_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    if approval_store.delete_delegation(delegation_id, cid, _username(request), _is_admin(request)):
        flash(request, "Delegation removed", "success")
    else:
        flash(request, "Failed to remove delegation", "error")
    return RedirectResponse("/approvals/", status_code=303)


# ── JSON API ──────────────────────────────────────────────────────

@router.get("/api/status/{entity_type}/{entity_id}", name="approval_api_status")
async def api_status(entity_type: str, entity_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    st = approval_store.status_of(entity_type, entity_id, cid)
    if not st:
        return JSONResponse({"entity_type": entity_type, "entity_id": entity_id,
                             "status": None, "request_id": None})
    return JSONResponse(st)


@router.post("/api/submit", name="approval_api_submit")
async def api_submit(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    data: dict = {}
    if "application/json" in request.headers.get("content-type", ""):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    else:
        form = await request.form()
        data = {k: v for k, v in form.items()}
    entity_type = (data.get("entity_type") or "generic").strip()
    entity_id = str(data.get("entity_id") or "").strip()
    title = (data.get("title") or "").strip()
    if not entity_id or not title:
        return JSONResponse({"ok": False, "error": "entity_id and title are required"}, status_code=400)
    payload = data.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload) if payload.strip() else {}
        except Exception:
            payload = {"raw": payload}
    rid = approval_store.submit(cid, entity_type, entity_id, title, data.get("amount"),
                                _username(request), payload=payload,
                                currency=data.get("currency") or "ETB")
    if rid is None:
        return JSONResponse({"ok": True, "request_id": None, "status": "auto_approved"})
    return JSONResponse({"ok": True, "request_id": rid, "status": "pending"}, status_code=201)


# ── Request detail + actions ──────────────────────────────────────

@router.get("/requests/{request_id}", name="approval_request_detail")
async def request_detail(request_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    req = approval_store.get_request(request_id, cid)
    if not req:
        flash(request, "Approval request not found", "error")
        return RedirectResponse("/approvals/", status_code=303)
    me = _username(request)
    ctx = _ctx(request, "detail")
    ctx.update(req=req, can_act=approval_store.can_act(req, me, _roles(request)),
               is_requester=(req.get("requested_by") == me),
               approvers=approval_store.current_approvers(req),
               users=approval_store.get_company_users(cid), actions=ACTIONS)
    return templates.TemplateResponse("approvals/request_detail.html", ctx)


@router.post("/requests/{request_id}/action", name="approval_request_action")
async def request_action(request_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    action = (form.get("action") or "").strip().lower()
    back = _safe_next(request, form, f"/approvals/requests/{request_id}")
    if action not in ACTIONS:
        flash(request, "Invalid action", "error")
        return RedirectResponse(back, status_code=303)
    result = approval_store.decide(request_id, _username(request), action, form.get("comment", ""),
                                   roles=_roles(request), company_id=cid,
                                   delegate_to=form.get("delegate_to", ""))
    if result.get("ok"):
        msg = {"approve": "Approved", "reject": "Rejected", "comment": "Comment added",
               "delegate": "Delegated"}.get(action, "Done")
        if action == "approve" and result.get("status") == "pending":
            msg += " — waiting for the next approver" if result.get("step_complete") else " — waiting for other approvers at this step"
        flash(request, msg, "success")
    else:
        flash(request, result.get("error") or "Action failed", "error")
    return RedirectResponse(back, status_code=303)


@router.post("/requests/{request_id}/cancel", name="approval_request_cancel")
async def request_cancel(request_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    result = approval_store.cancel(request_id, _username(request), cid, _is_admin(request))
    flash(request, "Request cancelled" if result.get("ok") else (result.get("error") or "Failed to cancel"),
          "success" if result.get("ok") else "error")
    return RedirectResponse(f"/approvals/requests/{request_id}", status_code=303)
