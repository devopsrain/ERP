"""
Procurement Routes — Vendors, PRs, POs, Three-Way Match, Tenders
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required
from template_engine import templates
from procurement_data_store import procurement_store
from notifications_data_store import notifications_store
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/procurement", tags=["procurement"])


@router.on_event("startup")
async def _startup():
    procurement_store.ensure_schema()


@router.get("/", name="procurement_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx.update(
        stats=procurement_store.get_stats(cid),
        pending_prs=procurement_store.get_prs(cid),
        recent_pos=procurement_store.get_pos(cid)[:10],
        open_tenders=procurement_store.get_tenders(cid),
    )
    return templates.TemplateResponse("procurement/dashboard.html", ctx)


# ── Vendors ──────────────────────────────────────────────────────────────────

@router.get("/vendors", name="procurement_vendors")
async def vendors(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["vendors"] = procurement_store.get_vendors(cid)
    return templates.TemplateResponse("procurement/vendors.html", ctx)


@router.get("/vendors/new", name="procurement_new_vendor_get")
async def new_vendor_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("procurement/vendor_form.html", {**template_context(request), "vendor": {}, "action": "create"})


@router.post("/vendors/new", name="procurement_new_vendor_post")
async def new_vendor_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    v = procurement_store.create_vendor(cid, data)
    if v:
        flash(request, f"Vendor '{v['name']}' added", "success")
    else:
        flash(request, "Failed to add vendor", "error")
    return RedirectResponse("/procurement/vendors", status_code=303)


@router.post("/vendors/{vendor_id}/status", name="procurement_vendor_status")
async def vendor_status(vendor_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    procurement_store.update_vendor_status(vendor_id, form.get("status","active"), cid)
    flash(request, "Vendor status updated", "success")
    return RedirectResponse("/procurement/vendors", status_code=303)


# ── Purchase Requisitions ─────────────────────────────────────────────────────

@router.get("/pr", name="procurement_prs")
async def prs(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["prs"] = procurement_store.get_prs(cid)
    return templates.TemplateResponse("procurement/pr_list.html", ctx)


@router.get("/pr/new", name="procurement_new_pr_get")
async def new_pr_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("procurement/pr_form.html", {**template_context(request), "pr": {}})


@router.post("/pr/new", name="procurement_new_pr_post")
async def new_pr_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["requested_by"] = request.session.get("username", "")
    pr = procurement_store.create_pr(cid, data)
    if pr:
        flash(request, "Purchase Requisition created", "success")
        return RedirectResponse("/procurement/pr", status_code=303)
    flash(request, "Failed to create PR", "error")
    return RedirectResponse("/procurement/pr/new", status_code=303)


@router.post("/pr/{pr_id}/submit", name="procurement_submit_pr")
async def submit_pr(pr_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    procurement_store.submit_pr(pr_id, cid)
    notifications_store.broadcast(
        cid, "PR awaiting approval",
        message=f"Submitted by {request.session.get('username','')}",
        link="/procurement/pr", icon="file-earmark-text", category="info"
    )
    flash(request, "PR submitted for approval", "success")
    return RedirectResponse("/procurement/pr", status_code=303)


@router.post("/pr/{pr_id}/approve", name="procurement_approve_pr")
async def approve_pr(pr_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    approved = form.get("decision") == "approve"
    note = form.get("note", "")
    approver = request.session.get("username", "")
    result = procurement_store.approve_pr(pr_id, cid, approver, approved, note)
    if result["ok"]:
        flash(request, f"PR {result['status']}", "success")
    else:
        flash(request, result.get("error", "Action failed"), "error")
    return RedirectResponse("/procurement/pr", status_code=303)


# ── Purchase Orders ───────────────────────────────────────────────────────────

@router.get("/po", name="procurement_pos")
async def pos(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["pos"] = procurement_store.get_pos(cid)
    return templates.TemplateResponse("procurement/po_list.html", ctx)


@router.get("/po/new", name="procurement_new_po_get")
async def new_po_get(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx.update(vendors=procurement_store.get_vendors(cid), prs=procurement_store.get_prs(cid), po={})
    return templates.TemplateResponse("procurement/po_form.html", ctx)


@router.post("/po/new", name="procurement_new_po_post")
async def new_po_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = request.session.get("username", "")
    # Parse line items: line_desc[], line_qty[], line_unit[], line_price[]
    descs  = form.getlist("line_desc")
    qtys   = form.getlist("line_qty")
    units  = form.getlist("line_unit")
    prices = form.getlist("line_price")
    lines = []
    for i in range(len(descs)):
        if descs[i].strip():
            try:
                qty = float(qtys[i]) if i < len(qtys) else 1
                price = float(prices[i]) if i < len(prices) else 0
                lines.append({"description": descs[i], "quantity": qty, "unit": units[i] if i < len(units) else "unit", "unit_price": price, "total": qty * price})
            except (ValueError, IndexError):
                pass
    po = procurement_store.create_po(cid, data, lines)
    if po:
        flash(request, "Purchase Order created", "success")
        return RedirectResponse(f"/procurement/po/{po['id']}", status_code=303)
    flash(request, "Failed to create PO", "error")
    return RedirectResponse("/procurement/po/new", status_code=303)


@router.get("/po/{po_id}", name="procurement_po_detail")
async def po_detail(po_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    po = procurement_store.get_po(po_id, cid)
    if not po:
        flash(request, "PO not found", "error")
        return RedirectResponse("/procurement/po", status_code=303)
    match_status = procurement_store.get_three_way_status(po_id)
    ctx = template_context(request)
    ctx.update(po=po, match_status=match_status)
    return templates.TemplateResponse("procurement/po_detail.html", ctx)


@router.post("/po/{po_id}/grn", name="procurement_record_grn")
async def record_grn(po_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["received_by"] = request.session.get("username", "")
    procurement_store.record_grn(cid, po_id, data)
    flash(request, "Goods Receipt Note recorded", "success")
    return RedirectResponse(f"/procurement/po/{po_id}", status_code=303)


@router.post("/po/{po_id}/invoice", name="procurement_record_invoice")
async def record_invoice(po_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    procurement_store.record_invoice(cid, po_id, data)
    flash(request, "Invoice recorded", "success")
    return RedirectResponse(f"/procurement/po/{po_id}", status_code=303)


# ── Tenders ───────────────────────────────────────────────────────────────────

@router.get("/tenders", name="procurement_tenders")
async def tenders(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["tenders"] = procurement_store.get_tenders(cid)
    return templates.TemplateResponse("procurement/tender_list.html", ctx)


@router.get("/tenders/new", name="procurement_new_tender_get")
async def new_tender_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("procurement/tender_form.html", {**template_context(request), "tender": {}})


@router.post("/tenders/new", name="procurement_new_tender_post")
async def new_tender_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = request.session.get("username", "")
    t = procurement_store.create_tender(cid, data)
    if t:
        flash(request, "Tender published", "success")
    return RedirectResponse("/procurement/tenders", status_code=303)


@router.get("/tenders/{tender_id}/bids", name="procurement_tender_bids")
async def tender_bids(tender_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    bids = procurement_store.get_bid_comparison(tender_id)
    ctx = template_context(request)
    ctx.update(bids=bids, tender_id=tender_id, vendors=procurement_store.get_vendors(cid))
    return templates.TemplateResponse("procurement/bid_comparison.html", ctx)


@router.post("/tenders/{tender_id}/bid", name="procurement_submit_bid")
async def submit_bid(tender_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {k: v for k, v in form.items()}
    procurement_store.submit_bid(tender_id, data)
    flash(request, "Bid submitted", "success")
    return RedirectResponse(f"/procurement/tenders/{tender_id}/bids", status_code=303)


@router.post("/tenders/{tender_id}/award", name="procurement_award_tender")
async def award_tender(tender_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    procurement_store.award_tender(tender_id, cid, form.get("vendor_id",""))
    flash(request, "Tender awarded", "success")
    return RedirectResponse(f"/procurement/tenders/{tender_id}/bids", status_code=303)
