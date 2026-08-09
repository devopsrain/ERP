from datetime import date

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse

from deps import flash, login_required, admin_required, template_context, current_company
from finance_management_data_store import finance_store
from services.forecast_service import forecast_finance
from template_engine import templates

# NOTE: app.py registers exactly one router per module. The JSON API keeps
# its historical /finance-mgmt prefix (spelled per-route below) while the
# HTML reports/registers live under /finance — hence no router-level prefix.
router = APIRouter(tags=["finance-management"])


def _company(request: Request) -> str:
    # request.state.company_id (tenant middleware) wins; otherwise the unified
    # session resolution from deps. The legacy session["company_id"] key was
    # never written anywhere, so that dead lookup was dropped.
    return getattr(request.state, "company_id", None) or current_company(request)


def _user(request: Request) -> str:
    return request.session.get("username", "system")


@router.get("/finance-mgmt/dashboard", name="finance_mgmt_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    return JSONResponse({
        "status": "ok",
        "company_id": _company(request),
        "data": finance_store.finance_dashboard(_company(request)),
        "links": {
            "html_dashboard": "/finance/dashboard",
            "balance_sheet": "/finance/reports/balance-sheet",
            "cash_flow": "/finance/reports/cash-flow",
            "profit_loss": "/finance/reports/profit-loss",
            "cost_centers": "/finance/cost-centers",
            "receivables": "/finance/receivables",
            "payables": "/finance/payables",
        },
    })


@router.post("/finance-mgmt/gl/entries", name="finance_mgmt_post_gl_entry")
async def post_gl_entry(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    payload["created_by"] = _user(request)
    entry_id = finance_store.post_gl_entry(payload)
    if not entry_id:
        return JSONResponse({"status": "error", "error": "Failed to post GL entry"}, status_code=500)
    return JSONResponse({"status": "ok", "entry_id": entry_id})


@router.post("/finance-mgmt/ar-ap", name="finance_mgmt_create_ar_ap")
async def create_ar_ap(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    txn_id = finance_store.create_ar_ap(payload)
    if not txn_id:
        return JSONResponse({"status": "error", "error": "Failed to create AR/AP record"}, status_code=500)
    return JSONResponse({"status": "ok", "txn_id": txn_id})


@router.post("/finance-mgmt/assets", name="finance_mgmt_create_asset")
async def create_asset(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    asset_id = finance_store.create_asset(payload)
    if not asset_id:
        return JSONResponse({"status": "error", "error": "Failed to create asset"}, status_code=500)
    return JSONResponse({"status": "ok", "asset_id": asset_id})


@router.post("/finance-mgmt/budgets", name="finance_mgmt_create_budget")
async def create_budget(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    budget_id = finance_store.create_budget(payload)
    if not budget_id:
        return JSONResponse({"status": "error", "error": "Failed to create budget"}, status_code=500)
    return JSONResponse({"status": "ok", "budget_id": budget_id})


@router.get("/finance-mgmt/reports/budget-vs-actual", name="finance_mgmt_budget_vs_actual")
async def budget_vs_actual(
    request: Request,
    fiscal_year: int = Query(date.today().year),
    user=Depends(login_required),
):
    rows = finance_store.budget_vs_actual(fiscal_year=fiscal_year, company_id=_company(request))
    return JSONResponse({"status": "ok", "fiscal_year": fiscal_year, "rows": rows})


@router.post("/finance-mgmt/shareholders", name="finance_mgmt_create_shareholder")
async def create_shareholder(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    shareholder_id = finance_store.create_shareholder(payload)
    if not shareholder_id:
        return JSONResponse({"status": "error", "error": "Failed to create shareholder"}, status_code=500)
    return JSONResponse({"status": "ok", "shareholder_id": shareholder_id})


@router.post("/finance-mgmt/dividends", name="finance_mgmt_declare_dividend")
async def declare_dividend(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    dividend_id = finance_store.declare_dividend(payload)
    if not dividend_id:
        return JSONResponse({"status": "error", "error": "Failed to declare dividend"}, status_code=500)
    return JSONResponse({"status": "ok", "dividend_id": dividend_id})


@router.get("/finance-mgmt/exports/financial-pack", name="finance_mgmt_export_pack")
async def export_financial_pack(request: Request, user=Depends(login_required)):
    """Return data payload for Excel/PDF export pipelines."""
    cid = _company(request)
    fiscal_year = date.today().year
    return JSONResponse({
        "status": "ok",
        "company_id": cid,
        "fiscal_year": fiscal_year,
        "dashboard": finance_store.finance_dashboard(cid),
        "budget_vs_actual": finance_store.budget_vs_actual(fiscal_year, cid),
        "note": "Payload ready for Excel/PDF rendering layer.",
    })


@router.get("/finance-mgmt/forecast", name="finance_mgmt_forecast")
async def forecast_view(
    request: Request,
    year: int = Query(default=date.today().year),
    format: str = Query(default="html"),
    user=Depends(login_required),
):
    """
    End-of-year forecast for revenue, expense and net income.

    Extrapolates monthly GL entries using linear regression (>=3 observed
    months) or monthly averages (sparse data). Returns JSON when
    ?format=json, otherwise renders a chart-based HTML page.
    """
    cid = _company(request)
    data = forecast_finance(cid, year)
    if format.lower() == "json":
        return JSONResponse({"status": "ok", **data})
    ctx = template_context(request)
    ctx.update(
        forecast=data,
        module_label="Finance",
        module_home="/finance-mgmt/dashboard",
        metric_labels={"revenue": "Revenue", "expense": "Expense", "net": "Net Income"},
    )
    return templates.TemplateResponse("forecast/dashboard.html", ctx)


# ══════════════════════════════════════════════════════════════════
# HTML finance module — statements, cost centers, AR/AP registers
# ══════════════════════════════════════════════════════════════════

def _blank_to_none(value):
    """'' → None for DATE/NUMERIC columns posted from HTML forms."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _to_float(value, default=0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


@router.get("/finance/dashboard", name="finance_dashboard")
async def finance_dashboard_page(request: Request, user=Depends(login_required)):
    cid = _company(request)
    ctx = template_context(request)
    ctx.update(
        metrics=finance_store.finance_dashboard(cid),
        ar=finance_store.list_register("receivable", cid),
        ap=finance_store.list_register("payable", cid),
        company_id=cid,
    )
    return templates.TemplateResponse("finance/dashboard.html", ctx)


# ── Financial statements ──────────────────────────────────────────

@router.get("/finance/reports/balance-sheet", name="finance_balance_sheet")
async def balance_sheet_report(request: Request, user=Depends(login_required)):
    cid = _company(request)
    ctx = template_context(request)
    ctx.update(report=finance_store.balance_sheet(cid),
               report_date=date.today(), company_id=cid)
    return templates.TemplateResponse("finance/balance_sheet.html", ctx)


@router.get("/finance/reports/cash-flow", name="finance_cash_flow")
async def cash_flow_report(
    request: Request,
    year: int = Query(default=None),
    user=Depends(login_required),
):
    cid = _company(request)
    year = year or date.today().year
    ctx = template_context(request)
    ctx.update(
        report=finance_store.cash_flow(year, cid),
        year=year,
        year_options=list(range(date.today().year, date.today().year - 6, -1)),
        company_id=cid,
    )
    return templates.TemplateResponse("finance/cash_flow.html", ctx)


@router.get("/finance/reports/profit-loss", name="finance_profit_loss")
async def profit_loss_report(request: Request, user=Depends(login_required)):
    cid = _company(request)
    ctx = template_context(request)
    ctx.update(report=finance_store.profit_loss(cid),
               report_date=date.today(), company_id=cid)
    return templates.TemplateResponse("finance/profit_loss.html", ctx)


# ── Cost centers (static paths registered before /{cc_id}) ───────

@router.get("/finance/cost-centers", name="finance_cost_centers")
async def cost_centers_list(request: Request, user=Depends(login_required)):
    cid = _company(request)
    ctx = template_context(request)
    ctx.update(cost_centers=finance_store.list_cost_centers(cid), company_id=cid)
    return templates.TemplateResponse("finance/cost_centers.html", ctx)


@router.get("/finance/cost-centers/new", name="finance_cost_center_new_get")
async def cost_center_new_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(cost_center=None, form_action="/finance/cost-centers/new")
    return templates.TemplateResponse("finance/cost_center_form.html", ctx)


@router.post("/finance/cost-centers/new", name="finance_cost_center_new")
async def cost_center_new_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {
        "company_id": _company(request),
        "code": (form.get("code") or "").strip(),
        "name": (form.get("name") or "").strip(),
        "budget_amount": _to_float(_blank_to_none(form.get("budget_amount"))),
        "is_active": form.get("is_active") == "on",
    }
    if not data["code"] or not data["name"]:
        flash(request, "Code and name are required", "error")
        return RedirectResponse("/finance/cost-centers/new", status_code=303)
    if finance_store.create_cost_center(data):
        flash(request, f"Cost center {data['code']} created", "success")
    else:
        flash(request, "Failed to create cost center", "error")
    return RedirectResponse("/finance/cost-centers", status_code=303)


@router.get("/finance/cost-centers/edit/{cc_id}", name="finance_cost_center_edit_get")
async def cost_center_edit_get(cc_id: str, request: Request, user=Depends(login_required)):
    cid = _company(request)
    cost_center = finance_store.get_cost_center(cc_id, cid)
    if not cost_center:
        flash(request, "Cost center not found", "error")
        return RedirectResponse("/finance/cost-centers", status_code=303)
    ctx = template_context(request)
    ctx.update(cost_center=cost_center,
               form_action=f"/finance/cost-centers/edit/{cc_id}")
    return templates.TemplateResponse("finance/cost_center_form.html", ctx)


@router.post("/finance/cost-centers/edit/{cc_id}", name="finance_cost_center_edit")
async def cost_center_edit_post(cc_id: str, request: Request, user=Depends(login_required)):
    cid = _company(request)
    form = await request.form()
    data = {
        "code": (form.get("code") or "").strip(),
        "name": (form.get("name") or "").strip(),
        "budget_amount": _to_float(_blank_to_none(form.get("budget_amount"))),
        "is_active": form.get("is_active") == "on",
    }
    if finance_store.update_cost_center(cc_id, data, cid):
        flash(request, f"Cost center {data['code']} updated", "success")
    else:
        flash(request, "Failed to update cost center", "error")
    return RedirectResponse("/finance/cost-centers", status_code=303)


# ── AR / AP registers ─────────────────────────────────────────────

_REGISTER_META = {
    "receivable": {"label": "Accounts Receivable", "party_label": "Customer",
                   "base_url": "/finance/receivables"},
    "payable": {"label": "Accounts Payable", "party_label": "Supplier",
                "base_url": "/finance/payables"},
}


def _register_list(request: Request, kind: str):
    cid = _company(request)
    ctx = template_context(request)
    ctx.update(kind=kind, meta=_REGISTER_META[kind],
               register=finance_store.list_register(kind, cid), company_id=cid)
    return templates.TemplateResponse("finance/ar_ap_list.html", ctx)


def _register_new_get(request: Request, kind: str):
    ctx = template_context(request)
    ctx.update(kind=kind, meta=_REGISTER_META[kind])
    return templates.TemplateResponse("finance/ar_ap_form.html", ctx)


async def _register_new_post(request: Request, kind: str):
    meta = _REGISTER_META[kind]
    form = await request.form()
    data = {
        "company_id": _company(request),
        "party": (form.get("party") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "amount": _to_float(_blank_to_none(form.get("amount"))),
        "due_date": _blank_to_none(form.get("due_date")),
        "status": "open",
    }
    if not data["party"] or data["amount"] <= 0:
        flash(request, "Party and a positive amount are required", "error")
        return RedirectResponse(f"{meta['base_url']}/new", status_code=303)
    if finance_store.create_register_record(kind, data):
        flash(request, f"{meta['label']} record created", "success")
    else:
        flash(request, f"Failed to create {meta['label']} record", "error")
    return RedirectResponse(meta["base_url"], status_code=303)


async def _register_pay_post(request: Request, kind: str, rec_id: str):
    meta = _REGISTER_META[kind]
    form = await request.form()
    payment = _to_float(_blank_to_none(form.get("payment_amount")))
    if payment <= 0:
        flash(request, "Payment amount must be positive", "error")
    elif finance_store.record_register_payment(kind, rec_id, payment, _company(request)):
        flash(request, "Payment of {:,.2f} recorded".format(payment), "success")
    else:
        flash(request, "Failed to record payment", "error")
    return RedirectResponse(meta["base_url"], status_code=303)


@router.get("/finance/receivables", name="finance_receivables")
async def receivables_list(request: Request, user=Depends(login_required)):
    return _register_list(request, "receivable")


@router.get("/finance/receivables/new", name="finance_receivable_new_get")
async def receivable_new_get(request: Request, user=Depends(login_required)):
    return _register_new_get(request, "receivable")


@router.post("/finance/receivables/new", name="finance_receivable_new")
async def receivable_new_post(request: Request, user=Depends(login_required)):
    return await _register_new_post(request, "receivable")


@router.post("/finance/receivables/pay/{rec_id}", name="finance_receivable_pay")
async def receivable_pay(rec_id: str, request: Request, user=Depends(login_required)):
    return await _register_pay_post(request, "receivable", rec_id)


@router.get("/finance/payables", name="finance_payables")
async def payables_list(request: Request, user=Depends(login_required)):
    return _register_list(request, "payable")


@router.get("/finance/payables/new", name="finance_payable_new_get")
async def payable_new_get(request: Request, user=Depends(login_required)):
    return _register_new_get(request, "payable")


@router.post("/finance/payables/new", name="finance_payable_new")
async def payable_new_post(request: Request, user=Depends(login_required)):
    return await _register_new_post(request, "payable")


@router.post("/finance/payables/pay/{rec_id}", name="finance_payable_pay")
async def payable_pay(rec_id: str, request: Request, user=Depends(login_required)):
    return await _register_pay_post(request, "payable", rec_id)
