"""
Stakeholder Management Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from deps import flash, template_context, login_required, current_company
from template_engine import templates
from stakeholder_data_store import stakeholder_store
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stakeholder", tags=["stakeholder"])

VALID_TXN_TYPES = ("purchase", "transfer_in", "transfer_out", "sale")
VALID_DIVIDEND_STATUSES = ("declared", "approved", "paid")


@router.on_event("startup")
async def _startup():
    stakeholder_store.ensure_schema()


@router.get("/", name="stakeholder_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(stats=stakeholder_store.get_stats(cid),
               equity=stakeholder_store.get_equity_structure(cid),
               dividends=stakeholder_store.get_dividends(cid)[:5])
    return templates.TemplateResponse("stakeholder/dashboard.html", ctx)


# ── Shareholders ──────────────────────────────────────────────────
# Static paths MUST be registered before /shareholders/{shareholder_id}

@router.get("/shareholders", name="stakeholder_shareholders")
async def shareholder_list(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    shareholders = stakeholder_store.get_shareholders(cid)
    ctx = template_context(request)
    ctx.update(shareholders=shareholders,
               total_shares=float(stakeholder_store.get_total_shares(cid)))
    return templates.TemplateResponse("stakeholder/shareholders.html", ctx)


@router.get("/shareholders/new", name="stakeholder_shareholder_new_get")
async def new_shareholder_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("stakeholder/shareholder_form.html",
                                      {**template_context(request), "shareholder": {}})


@router.post("/shareholders/new", name="stakeholder_shareholder_new_post")
async def new_shareholder_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    s = stakeholder_store.create_shareholder(cid, data)
    if s:
        flash(request, "Shareholder created", "success")
        return RedirectResponse(f"/stakeholder/shareholders/{s['id']}", status_code=303)
    flash(request, "Failed to create shareholder", "error")
    return RedirectResponse("/stakeholder/shareholders/new", status_code=303)


@router.get("/shareholders/{shareholder_id}", name="stakeholder_shareholder_detail")
async def shareholder_detail(shareholder_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    s = stakeholder_store.get_shareholder(shareholder_id, cid)
    if not s:
        flash(request, "Shareholder not found", "error")
        return RedirectResponse("/stakeholder/shareholders", status_code=303)
    net_shares = float(stakeholder_store.get_net_shares(shareholder_id, cid))
    total_shares = float(stakeholder_store.get_total_shares(cid))
    ctx = template_context(request)
    ctx.update(shareholder=s,
               transactions=stakeholder_store.get_transactions(shareholder_id, cid),
               dividend_history=stakeholder_store.get_shareholder_dividends(shareholder_id),
               net_shares=net_shares, total_shares=total_shares,
               ownership_pct=(net_shares / total_shares * 100) if total_shares else 0.0)
    return templates.TemplateResponse("stakeholder/shareholder_detail.html", ctx)


@router.post("/shareholders/{shareholder_id}/edit", name="stakeholder_shareholder_edit")
async def shareholder_edit(shareholder_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if stakeholder_store.update_shareholder(shareholder_id, cid, data):
        flash(request, "Shareholder updated", "success")
    else:
        flash(request, "Failed to update shareholder", "error")
    return RedirectResponse(f"/stakeholder/shareholders/{shareholder_id}", status_code=303)


# ── Share transactions ────────────────────────────────────────────

@router.post("/transactions/new", name="stakeholder_transaction_new")
async def new_transaction_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    shareholder_id = data.get("shareholder_id", "")
    if not stakeholder_store.get_shareholder(shareholder_id, cid):
        flash(request, "Shareholder not found", "error")
        return RedirectResponse("/stakeholder/shareholders", status_code=303)
    if data.get("txn_type") not in VALID_TXN_TYPES:
        flash(request, "Invalid transaction type", "error")
        return RedirectResponse(f"/stakeholder/shareholders/{shareholder_id}", status_code=303)
    if stakeholder_store.create_transaction(cid, shareholder_id, data):
        flash(request, "Share transaction recorded", "success")
    else:
        flash(request, "Failed to record share transaction", "error")
    return RedirectResponse(f"/stakeholder/shareholders/{shareholder_id}", status_code=303)


# ── Dividends ─────────────────────────────────────────────────────
# Static paths MUST be registered before /dividends/{dividend_id}

@router.get("/dividends", name="stakeholder_dividends")
async def dividend_list(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(dividends=stakeholder_store.get_dividends(cid))
    return templates.TemplateResponse("stakeholder/dividends.html", ctx)


@router.get("/dividends/new", name="stakeholder_dividend_new_get")
async def new_dividend_get(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(dividend={}, total_shares=float(stakeholder_store.get_total_shares(cid)))
    return templates.TemplateResponse("stakeholder/dividend_form.html", ctx)


@router.post("/dividends/new", name="stakeholder_dividend_new_post")
async def new_dividend_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    d = stakeholder_store.create_dividend(cid, data)
    if d:
        flash(request, "Dividend declared — payment rows generated pro-rata", "success")
        return RedirectResponse(f"/stakeholder/dividends/{d['id']}", status_code=303)
    flash(request, "Failed to declare dividend", "error")
    return RedirectResponse("/stakeholder/dividends/new", status_code=303)


@router.get("/dividends/{dividend_id}", name="stakeholder_dividend_detail")
async def dividend_detail(dividend_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    d = stakeholder_store.get_dividend(dividend_id, cid)
    if not d:
        flash(request, "Dividend not found", "error")
        return RedirectResponse("/stakeholder/dividends", status_code=303)
    payments = stakeholder_store.get_dividend_payments(dividend_id)
    ctx = template_context(request)
    ctx.update(dividend=d, payments=payments,
               paid_count=sum(1 for p in payments if p["paid"]),
               paid_amount=sum(float(p["amount"]) for p in payments if p["paid"]),
               payments_total=sum(float(p["amount"]) for p in payments))
    return templates.TemplateResponse("stakeholder/dividend_detail.html", ctx)


@router.post("/dividends/{dividend_id}/pay/{payment_id}", name="stakeholder_dividend_pay")
async def dividend_pay(dividend_id: str, payment_id: str, request: Request,
                       user=Depends(login_required)):
    cid = current_company(request)
    if not stakeholder_store.get_dividend(dividend_id, cid):
        flash(request, "Dividend not found", "error")
        return RedirectResponse("/stakeholder/dividends", status_code=303)
    if stakeholder_store.mark_payment_paid(dividend_id, payment_id):
        flash(request, "Payment marked as paid", "success")
    else:
        flash(request, "Failed to mark payment as paid", "error")
    return RedirectResponse(f"/stakeholder/dividends/{dividend_id}", status_code=303)


@router.post("/dividends/{dividend_id}/status", name="stakeholder_dividend_status")
async def dividend_status(dividend_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    new_status = form.get("status", "")
    if new_status not in VALID_DIVIDEND_STATUSES:
        flash(request, "Invalid status", "error")
        return RedirectResponse(f"/stakeholder/dividends/{dividend_id}", status_code=303)
    if stakeholder_store.set_dividend_status(dividend_id, cid, new_status):
        flash(request, f"Dividend marked {new_status}", "success")
    else:
        flash(request, "Failed to update status", "error")
    return RedirectResponse(f"/stakeholder/dividends/{dividend_id}", status_code=303)
