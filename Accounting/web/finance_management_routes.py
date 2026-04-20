from datetime import date

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse

from deps import login_required, admin_required
from finance_management_data_store import finance_store

router = APIRouter(prefix="/finance-mgmt", tags=["finance-management"])


def _company(request: Request) -> str:
    return (
        getattr(request.state, "company_id", None)
        or request.session.get("current_company_id")
        or request.session.get("company_id")
        or "default"
    )


def _user(request: Request) -> str:
    return request.session.get("username", "system")


@router.get("/dashboard", name="finance_mgmt_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    return JSONResponse({
        "status": "ok",
        "company_id": _company(request),
        "data": finance_store.finance_dashboard(_company(request)),
    })


@router.post("/gl/entries", name="finance_mgmt_post_gl_entry")
async def post_gl_entry(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    payload["created_by"] = _user(request)
    entry_id = finance_store.post_gl_entry(payload)
    if not entry_id:
        return JSONResponse({"status": "error", "error": "Failed to post GL entry"}, status_code=500)
    return JSONResponse({"status": "ok", "entry_id": entry_id})


@router.post("/ar-ap", name="finance_mgmt_create_ar_ap")
async def create_ar_ap(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    txn_id = finance_store.create_ar_ap(payload)
    if not txn_id:
        return JSONResponse({"status": "error", "error": "Failed to create AR/AP record"}, status_code=500)
    return JSONResponse({"status": "ok", "txn_id": txn_id})


@router.post("/assets", name="finance_mgmt_create_asset")
async def create_asset(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    asset_id = finance_store.create_asset(payload)
    if not asset_id:
        return JSONResponse({"status": "error", "error": "Failed to create asset"}, status_code=500)
    return JSONResponse({"status": "ok", "asset_id": asset_id})


@router.post("/budgets", name="finance_mgmt_create_budget")
async def create_budget(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    budget_id = finance_store.create_budget(payload)
    if not budget_id:
        return JSONResponse({"status": "error", "error": "Failed to create budget"}, status_code=500)
    return JSONResponse({"status": "ok", "budget_id": budget_id})


@router.get("/reports/budget-vs-actual", name="finance_mgmt_budget_vs_actual")
async def budget_vs_actual(
    request: Request,
    fiscal_year: int = Query(date.today().year),
    user=Depends(login_required),
):
    rows = finance_store.budget_vs_actual(fiscal_year=fiscal_year, company_id=_company(request))
    return JSONResponse({"status": "ok", "fiscal_year": fiscal_year, "rows": rows})


@router.post("/shareholders", name="finance_mgmt_create_shareholder")
async def create_shareholder(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    shareholder_id = finance_store.create_shareholder(payload)
    if not shareholder_id:
        return JSONResponse({"status": "error", "error": "Failed to create shareholder"}, status_code=500)
    return JSONResponse({"status": "ok", "shareholder_id": shareholder_id})


@router.post("/dividends", name="finance_mgmt_declare_dividend")
async def declare_dividend(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    dividend_id = finance_store.declare_dividend(payload)
    if not dividend_id:
        return JSONResponse({"status": "error", "error": "Failed to declare dividend"}, status_code=500)
    return JSONResponse({"status": "ok", "dividend_id": dividend_id})


@router.get("/exports/financial-pack", name="finance_mgmt_export_pack")
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
