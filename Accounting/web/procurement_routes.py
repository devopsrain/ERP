"""
Procurement Routes — Vendors, PRs, POs, Three-Way Match, Tenders
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required, current_company
from template_engine import templates
from procurement_data_store import procurement_store
from notifications_data_store import notifications_store
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/procurement", tags=["procurement"])


@router.on_event("startup")
async def _startup():
    procurement_store.ensure_schema()


@router.get("/", name="procurement_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(
        stats=procurement_store.get_stats(cid),
        pending_prs=procurement_store.get_prs(cid),
        recent_pos=procurement_store.get_pos(cid)[:10],
        open_tenders=procurement_store.get_tenders(cid),
    )
    return templates.TemplateResponse("procurement/dashboard.html", ctx)


# ── Procurement Plans ────────────────────────────────────────────────────────

def _plan_form_data(form) -> dict:
    """Normalize plan form values (empty strings → safe defaults, ints clamped)."""
    try:
        fiscal_year = int(form.get("fiscal_year") or datetime.now().year)
    except (TypeError, ValueError):
        fiscal_year = datetime.now().year
    try:
        quarter = min(4, max(1, int(form.get("planned_quarter") or 1)))
    except (TypeError, ValueError):
        quarter = 1
    try:
        amount = float(form.get("estimated_amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return {
        "fiscal_year": fiscal_year,
        "department": (form.get("department") or "").strip() or "Unassigned",
        "title": (form.get("title") or "").strip(),
        "description": form.get("description") or "",
        "estimated_amount": amount,
        "planned_quarter": quarter,
    }


@router.get("/plans", name="procurement_plans")
async def plans(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    filters = procurement_store.get_plan_filter_options(cid)
    try:
        fiscal_year = int(request.query_params.get("fiscal_year") or 0)
    except (TypeError, ValueError):
        fiscal_year = 0
    if not fiscal_year:
        fiscal_year = filters["years"][0] if filters["years"] else datetime.now().year
    department = request.query_params.get("department") or None
    plan_rows = procurement_store.get_plans(cid, fiscal_year=fiscal_year, department=department)
    # Totals per department (of the filtered plan list)
    dept_totals = {}
    for p in plan_rows:
        d = dept_totals.setdefault(p["department"], {"count": 0, "total": 0.0})
        d["count"] += 1
        d["total"] += float(p["estimated_amount"] or 0)
    ctx = template_context(request)
    ctx.update(
        plans=plan_rows,
        fiscal_year=fiscal_year,
        department=department,
        filter_years=filters["years"],
        filter_departments=filters["departments"],
        dept_totals=dept_totals,
        budget_vs_actual=procurement_store.get_budget_vs_actual(cid, fiscal_year),
    )
    return templates.TemplateResponse("procurement/plans.html", ctx)


@router.get("/plans/new", name="procurement_new_plan_get")
async def new_plan_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(plan={}, action="create", current_year=datetime.now().year)
    return templates.TemplateResponse("procurement/plan_form.html", ctx)


@router.post("/plans/new", name="procurement_new_plan_post")
async def new_plan_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = _plan_form_data(form)
    if not data["title"]:
        flash(request, "Title is required", "error")
        return RedirectResponse("/procurement/plans/new", status_code=303)
    plan = procurement_store.create_plan(cid, data)
    if plan:
        flash(request, "Procurement plan created", "success")
        return RedirectResponse(f"/procurement/plans?fiscal_year={data['fiscal_year']}", status_code=303)
    flash(request, "Failed to create plan", "error")
    return RedirectResponse("/procurement/plans/new", status_code=303)


@router.get("/plans/{plan_id}/edit", name="procurement_edit_plan_get")
async def edit_plan_get(plan_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    plan = procurement_store.get_plan(plan_id, cid)
    if not plan:
        flash(request, "Plan not found", "error")
        return RedirectResponse("/procurement/plans", status_code=303)
    if plan["status"] not in ("draft", "rejected"):
        flash(request, f"Plan is {plan['status']} and can no longer be edited", "error")
        return RedirectResponse("/procurement/plans", status_code=303)
    ctx = template_context(request)
    ctx.update(plan=plan, action="edit", current_year=datetime.now().year)
    return templates.TemplateResponse("procurement/plan_form.html", ctx)


@router.post("/plans/{plan_id}/edit", name="procurement_edit_plan_post")
async def edit_plan_post(plan_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = _plan_form_data(form)
    if not data["title"]:
        flash(request, "Title is required", "error")
        return RedirectResponse(f"/procurement/plans/{plan_id}/edit", status_code=303)
    if procurement_store.update_plan(plan_id, cid, data):
        flash(request, "Plan updated", "success")
    else:
        flash(request, "Plan could not be updated (only draft/rejected plans are editable)", "error")
    return RedirectResponse(f"/procurement/plans?fiscal_year={data['fiscal_year']}", status_code=303)


@router.post("/plans/{plan_id}/submit", name="procurement_submit_plan")
async def submit_plan(plan_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    if procurement_store.submit_plan(plan_id, cid, request.session.get("username", "")):
        flash(request, "Plan submitted for approval", "success")
    else:
        flash(request, "Plan could not be submitted", "error")
    return RedirectResponse("/procurement/plans", status_code=303)


@router.post("/plans/{plan_id}/approve", name="procurement_approve_plan")
async def approve_plan(plan_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    approved = form.get("decision") == "approve"
    if procurement_store.approve_plan(plan_id, cid, request.session.get("username", ""), approved):
        flash(request, f"Plan {'approved' if approved else 'rejected'}", "success")
    else:
        flash(request, "Action failed (plan must be in submitted status)", "error")
    return RedirectResponse("/procurement/plans", status_code=303)


# ── Reports ──────────────────────────────────────────────────────────────────

@router.get("/reports/spending", name="procurement_report_spending")
async def report_spending(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    try:
        fiscal_year = int(request.query_params.get("fiscal_year") or datetime.now().year)
    except (TypeError, ValueError):
        fiscal_year = datetime.now().year
    report = procurement_store.get_spending_report(cid, fiscal_year)
    ctx = template_context(request)
    ctx.update(
        report=report,
        fiscal_year=fiscal_year,
        year_options=list(range(datetime.now().year, datetime.now().year - 6, -1)),
        budget_vs_actual=procurement_store.get_budget_vs_actual(cid, fiscal_year),
        month_names=["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    )
    return templates.TemplateResponse("procurement/report_spending.html", ctx)


@router.get("/reports/vendors", name="procurement_report_vendors")
async def report_vendors(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx["vendors"] = procurement_store.get_vendor_performance(cid)
    return templates.TemplateResponse("procurement/report_vendors.html", ctx)


# ── Vendors ──────────────────────────────────────────────────────────────────

@router.get("/vendors", name="procurement_vendors")
async def vendors(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx["vendors"] = procurement_store.get_vendors(cid)
    return templates.TemplateResponse("procurement/vendors.html", ctx)


@router.get("/vendors/new", name="procurement_new_vendor_get")
async def new_vendor_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("procurement/vendor_form.html", {**template_context(request), "vendor": {}, "action": "create"})


@router.post("/vendors/new", name="procurement_new_vendor_post")
async def new_vendor_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    name = (data.get("name") or "").strip()
    tin  = (data.get("tin_number") or "").strip()
    dup = procurement_store.find_duplicate_vendor(cid, name, tin)
    if dup:
        reason = "same TIN" if tin and (dup.get("tin_number") or "") == tin else "same name"
        flash(request, f"Duplicate vendor: '{dup['name']}' already exists ({reason}). Not saved.", "error")
        return RedirectResponse("/procurement/vendors/new", status_code=303)
    v = procurement_store.create_vendor(cid, data)
    if v:
        flash(request, f"Vendor '{v['name']}' added", "success")
    else:
        flash(request, "Failed to add vendor", "error")
    return RedirectResponse("/procurement/vendors", status_code=303)


@router.post("/vendors/{vendor_id}/status", name="procurement_vendor_status")
async def vendor_status(vendor_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    procurement_store.update_vendor_status(vendor_id, form.get("status","active"), cid)
    flash(request, "Vendor status updated", "success")
    return RedirectResponse("/procurement/vendors", status_code=303)


@router.post("/vendors/{vendor_id}/rate", name="procurement_rate_vendor")
async def rate_vendor(vendor_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    try:
        rating = float(form.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    if not 1 <= rating <= 5:
        flash(request, "Rating must be between 1 and 5", "error")
    elif procurement_store.rate_vendor(vendor_id, cid, rating):
        flash(request, "Vendor rating updated", "success")
    else:
        flash(request, "Failed to update rating", "error")
    return RedirectResponse("/procurement/reports/vendors", status_code=303)


# ── Purchase Requisitions ─────────────────────────────────────────────────────

def _parse_amount(raw, default: float = 0.0) -> float:
    """Form value → float for NUMERIC columns. '' / None / junk → default."""
    if raw is None:
        return default
    s = str(raw).strip().replace(",", "")
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _pr_form_data(form, requested_by: str = "") -> dict:
    """Map the PR form fields onto the proc_purchase_requisitions columns.

    The form posts item_description / quantity / unit / estimated_cost /
    required_by_date / budget_line / justification, while the table stores
    department / title / description / total_amount — inserting the raw form
    dict fails (missing NOT NULL department/title, '' into NUMERIC).
    API callers that already post title/department/total_amount still work.
    """
    item_desc = (form.get("item_description") or "").strip()
    title = (form.get("title") or "").strip() or item_desc[:200]
    quantity = _parse_amount(form.get("quantity"), 1.0)
    est_cost = _parse_amount(form.get("estimated_cost"), 0.0)
    total = _parse_amount(form.get("total_amount"), quantity * est_cost)
    desc_parts = []
    if item_desc:
        desc_parts.append(item_desc)
    if form.get("quantity"):
        desc_parts.append(f"Quantity: {form.get('quantity')} {(form.get('unit') or '').strip()}".strip())
    if form.get("required_by_date"):
        desc_parts.append(f"Required by: {form.get('required_by_date')}")
    if form.get("budget_line"):
        desc_parts.append(f"Budget line: {(form.get('budget_line') or '').strip()}")
    if form.get("justification"):
        desc_parts.append(f"Justification: {(form.get('justification') or '').strip()}")
    return {
        "title": title,
        "department": ((form.get("department") or form.get("budget_line") or "").strip()
                       or "General"),
        "description": (form.get("description") or "").strip() or "\n".join(desc_parts),
        "total_amount": total,
        "requested_by": requested_by,
    }


@router.get("/pr", name="procurement_prs")
async def prs(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx["prs"] = procurement_store.get_prs(cid)
    return templates.TemplateResponse("procurement/pr_list.html", ctx)


@router.get("/pr/new", name="procurement_new_pr_get")
async def new_pr_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("procurement/pr_form.html", {**template_context(request), "pr": {}})


@router.post("/pr/new", name="procurement_new_pr_post")
async def new_pr_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = _pr_form_data(form, requested_by=request.session.get("username", ""))
    if not data["title"]:
        flash(request, "Item description is required", "error")
        return RedirectResponse("/procurement/pr/new", status_code=303)
    pr = procurement_store.create_pr(cid, data)
    if pr:
        flash(request, "Purchase Requisition created", "success")
        return RedirectResponse("/procurement/pr", status_code=303)
    db_err = (getattr(procurement_store, "last_error", "") or "database write returned an error")
    flash(request, f"Failed to create PR: {db_err[:120]}", "error")
    return RedirectResponse("/procurement/pr/new", status_code=303)


@router.post("/pr/{pr_id}/submit", name="procurement_submit_pr")
async def submit_pr(pr_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
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
    cid = current_company(request)
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
    cid = current_company(request)
    ctx = template_context(request)
    ctx["pos"] = procurement_store.get_pos(cid)
    return templates.TemplateResponse("procurement/po_list.html", ctx)


@router.get("/po/new", name="procurement_new_po_get")
async def new_po_get(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(vendors=procurement_store.get_vendors(cid), prs=procurement_store.get_prs(cid), po={})
    return templates.TemplateResponse("procurement/po_form.html", ctx)


@router.post("/po/new", name="procurement_new_po_post")
async def new_po_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
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
    cid = current_company(request)
    po = procurement_store.get_po(po_id, cid)
    if not po:
        flash(request, "PO not found", "error")
        return RedirectResponse("/procurement/po", status_code=303)
    match_status = procurement_store.get_three_way_status(po_id)
    invoices = procurement_store.get_invoices(cid, po_id)
    ctx = template_context(request)
    ctx.update(po=po, match_status=match_status, invoices=invoices,
               all_paid=bool(invoices) and all(i["status"] == "paid" for i in invoices))
    return templates.TemplateResponse("procurement/po_detail.html", ctx)


@router.post("/po/{po_id}/pay", name="procurement_mark_paid")
async def mark_paid(po_id: str, request: Request, user=Depends(login_required)):
    """Payment processing: mark the PO's invoices as paid (requires 3-way match)."""
    cid = current_company(request)
    status = procurement_store.get_three_way_status(po_id)
    if not (status.get("po_exists") and status.get("grn_received") and status.get("invoice_matched")):
        flash(request, "Payment blocked — complete the three-way match (PO + GRN + invoice) first.", "error")
        return RedirectResponse(f"/procurement/po/{po_id}", status_code=303)
    n = procurement_store.mark_invoices_paid(cid, po_id)
    flash(request, f"{n} invoice(s) marked as paid." if n else "No unpaid invoices found.", "success" if n else "warning")
    return RedirectResponse(f"/procurement/po/{po_id}", status_code=303)


@router.post("/po/{po_id}/grn", name="procurement_record_grn")
async def record_grn(po_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["received_by"] = request.session.get("username", "")
    procurement_store.record_grn(cid, po_id, data)
    flash(request, "Goods Receipt Note recorded", "success")
    return RedirectResponse(f"/procurement/po/{po_id}", status_code=303)


@router.post("/po/{po_id}/invoice", name="procurement_record_invoice")
async def record_invoice(po_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    procurement_store.record_invoice(cid, po_id, data)
    flash(request, "Invoice recorded", "success")
    return RedirectResponse(f"/procurement/po/{po_id}", status_code=303)


# ── Tenders ───────────────────────────────────────────────────────────────────

@router.get("/tenders", name="procurement_tenders")
async def tenders(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx["tenders"] = procurement_store.get_tenders(cid)
    return templates.TemplateResponse("procurement/tender_list.html", ctx)


@router.get("/tenders/new", name="procurement_new_tender_get")
async def new_tender_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("procurement/tender_form.html", {**template_context(request), "tender": {}})


@router.post("/tenders/new", name="procurement_new_tender_post")
async def new_tender_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = request.session.get("username", "")
    t = procurement_store.create_tender(cid, data)
    if t:
        flash(request, "Tender published", "success")
    return RedirectResponse("/procurement/tenders", status_code=303)


@router.get("/tenders/{tender_id}/bids", name="procurement_tender_bids")
async def tender_bids(tender_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
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
    cid = current_company(request)
    form = await request.form()
    procurement_store.award_tender(tender_id, cid, form.get("vendor_id",""))
    flash(request, "Tender awarded", "success")
    return RedirectResponse(f"/procurement/tenders/{tender_id}/bids", status_code=303)
