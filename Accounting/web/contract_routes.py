"""
Contract Management Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from deps import flash, template_context, login_required, current_company
from template_engine import templates
from contract_data_store import contract_store
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contract", tags=["contract"])

VALID_STATUSES = ("draft", "active", "expired", "terminated", "renewed")
STATUS_EVENTS = {"active": "activated", "terminated": "terminated", "expired": "expired"}


@router.on_event("startup")
async def _startup():
    contract_store.ensure_schema()


@router.get("/", name="contract_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(stats=contract_store.get_stats(cid),
               expiring=contract_store.get_expiring(cid, 60),
               contracts=contract_store.get_contracts(cid)[:10])
    return templates.TemplateResponse("contracts/dashboard.html", ctx)


# Static paths MUST be registered before /{contract_id}

@router.get("/list", name="contract_list")
async def contract_list(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    status = request.query_params.get("status") or None
    party_type = request.query_params.get("party_type") or None
    ctx = template_context(request)
    ctx.update(contracts=contract_store.get_contracts(cid, status, party_type),
               status_filter=status or "", party_type_filter=party_type or "",
               list_title="Contracts")
    return templates.TemplateResponse("contracts/list.html", ctx)


@router.get("/new", name="contract_new_get")
async def new_contract_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("contracts/form.html", {**template_context(request), "contract": {}})


@router.post("/new", name="contract_new_post")
async def new_contract_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = request.session.get("username", "")
    c = contract_store.create_contract(cid, data)
    if c:
        contract_store.add_event(c["id"], "created", "Contract created",
                                 request.session.get("username", ""))
        flash(request, "Contract created", "success")
        return RedirectResponse(f"/contract/{c['id']}", status_code=303)
    flash(request, "Failed to create contract", "error")
    return RedirectResponse("/contract/new", status_code=303)


@router.get("/expiring", name="contract_expiring")
async def contract_expiring(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(contracts=contract_store.get_expiring(cid, 60),
               status_filter="", party_type_filter="",
               list_title="Contracts expiring within 60 days")
    return templates.TemplateResponse("contracts/list.html", ctx)


@router.get("/{contract_id}", name="contract_detail")
async def contract_detail(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    c = contract_store.get_contract(contract_id, cid)
    if not c:
        flash(request, "Contract not found", "error")
        return RedirectResponse("/contract/", status_code=303)
    ctx = template_context(request)
    ctx.update(contract=c, events=contract_store.get_events(contract_id))
    return templates.TemplateResponse("contracts/detail.html", ctx)


@router.post("/{contract_id}/edit", name="contract_edit")
async def contract_edit(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if contract_store.update_contract(contract_id, cid, data):
        contract_store.add_event(contract_id, "amended", "Contract details amended",
                                 request.session.get("username", ""))
        flash(request, "Contract updated", "success")
    else:
        flash(request, "Failed to update contract", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)


@router.post("/{contract_id}/status", name="contract_status")
async def contract_status(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    new_status = form.get("status", "")
    if new_status not in VALID_STATUSES:
        flash(request, "Invalid status", "error")
        return RedirectResponse(f"/contract/{contract_id}", status_code=303)
    if contract_store.set_status(contract_id, cid, new_status):
        contract_store.add_event(contract_id, STATUS_EVENTS.get(new_status, "note"),
                                 f"Status changed to {new_status}",
                                 request.session.get("username", ""))
        flash(request, f"Contract marked {new_status}", "success")
    else:
        flash(request, "Failed to update status", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)


@router.post("/{contract_id}/renew", name="contract_renew")
async def contract_renew(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    actor = request.session.get("username", "")
    new = contract_store.renew_contract(contract_id, cid, actor)
    if new:
        flash(request, "Contract renewed — review the new draft", "success")
        return RedirectResponse(f"/contract/{new['id']}", status_code=303)
    flash(request, "Failed to renew contract", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)


@router.post("/{contract_id}/note", name="contract_note")
async def contract_note(contract_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    note = (form.get("note") or "").strip()
    if note:
        contract_store.add_event(contract_id, "note", note,
                                 request.session.get("username", ""))
        flash(request, "Note added", "success")
    else:
        flash(request, "Note cannot be empty", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)
