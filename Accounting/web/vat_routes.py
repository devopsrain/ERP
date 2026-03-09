from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

from datetime import datetime, date, timedelta
from decimal import Decimal
from models.vat_portal import (
    VATContextManager, VATType, IncomeCategory, ExpenseCategory,
    IncomeRecord, ExpenseRecord, CapitalRecord,
)
from vat_data_store import VATDataStore
from auth_data_store import login_required as _flask_login_required

vat_manager   = VATContextManager()
vat_data_store = VATDataStore()

router = APIRouter(prefix="/vat", tags=["vat"])

def _company(request: Request) -> str:
    return request.session.get("current_company_id", "demo_company")


@router.get("/dashboard", name="vat_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    today = date.today()
    m_start = date(today.year, today.month, 1)
    m_end   = date(today.year, today.month + 1, 1) - timedelta(days=1) if today.month < 12 else date(today.year, 12, 31)
    ctx = template_context(request)
    ctx.update(
        stats=vat_manager.get_company_statistics(company_id),
        recent_income=vat_manager.get_company_income_records(company_id)[-10:],
        recent_expenses=vat_manager.get_company_expense_records(company_id)[-10:],
        monthly_summary=vat_manager.generate_financial_summary(company_id, m_start, m_end),
    )
    return templates.TemplateResponse("vat/dashboard.html", ctx)


@router.get("/income", name="vat_income_list")
async def income_list(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    start_date = request.query_params.get("start_date")
    end_date   = request.query_params.get("end_date")
    category   = request.query_params.get("category")
    s = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    e = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else None
    records = vat_manager.get_company_income_records(company_id, s, e)
    if category:
        records = [r for r in records if r.category.value == category]
    totals = {
        "gross_amount": sum(r.gross_amount for r in records),
        "vat_amount":   sum(r.vat_amount   for r in records),
        "net_amount":   sum(r.net_amount   for r in records),
    }
    ctx = template_context(request)
    ctx.update(income_records=records, income_transactions=records,
               totals=totals, income_categories=IncomeCategory,
               vat_types=VATType, filters={"start_date": start_date, "end_date": end_date, "category": category})
    return templates.TemplateResponse("vat/income_list.html", ctx)


@router.get("/income/add", name="vat_add_income_get")
async def add_income_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(income_categories=IncomeCategory, vat_types=VATType,
               vat_configs=vat_manager.get_vat_configurations())
    return templates.TemplateResponse("vat/add_income.html", ctx)


@router.post("/income/add", name="vat_add_income")
async def add_income_post(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    data = await request.json()
    try:
        income_data = {
            "contract_date":  datetime.strptime(data.get("contract_date"), "%Y-%m-%d").date(),
            "description":    data.get("description"),
            "category":       IncomeCategory(data.get("category")),
            "gross_amount":   Decimal(str(data.get("gross_amount", 0))),
            "vat_type":       VATType(data.get("vat_type")),
            "vat_rate":       Decimal(str(data.get("vat_rate", 0.15))),
            "customer_name":  data.get("customer_name", ""),
            "customer_tin":   data.get("customer_tin", ""),
            "invoice_number": data.get("invoice_number", ""),
            "created_by":     request.session.get("username", ""),
        }
        rec = vat_manager.add_income_record(company_id, income_data)
        return {"success": True, "income_id": rec.income_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/expenses", name="vat_expense_list")
async def expense_list(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    start_date = request.query_params.get("start_date")
    end_date   = request.query_params.get("end_date")
    category   = request.query_params.get("category")
    s = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    e = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else None
    records = vat_manager.get_company_expense_records(company_id, s, e)
    if category:
        records = [r for r in records if r.category.value == category]
    totals = {
        "gross_amount": sum(r.gross_amount for r in records),
        "vat_amount":   sum(r.vat_amount   for r in records),
        "net_amount":   sum(r.net_amount   for r in records),
    }
    ctx = template_context(request)
    ctx.update(expense_records=records, expense_transactions=records,
               totals=totals, expense_categories=ExpenseCategory,
               vat_types=VATType, filters={"start_date": start_date, "end_date": end_date, "category": category})
    return templates.TemplateResponse("vat/expense_list.html", ctx)


@router.get("/expenses/add", name="vat_add_expense_get")
async def add_expense_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(expense_categories=ExpenseCategory, vat_types=VATType,
               vat_configs=vat_manager.get_vat_configurations())
    return templates.TemplateResponse("vat/add_expense.html", ctx)


@router.post("/expenses/add", name="vat_add_expense")
async def add_expense_post(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)
    try:
        expense_data = {
            "expense_date":   datetime.strptime(data.get("expense_date"), "%Y-%m-%d").date(),
            "description":    data.get("description"),
            "category":       ExpenseCategory(data.get("category")),
            "gross_amount":   Decimal(str(data.get("gross_amount", 0))),
            "vat_type":       VATType(data.get("vat_type")),
            "vat_rate":       Decimal(str(data.get("vat_rate", 0.15))),
            "supplier_name":  data.get("supplier_name", ""),
            "supplier_tin":   data.get("supplier_tin", ""),
            "receipt_number": data.get("receipt_number", ""),
            "created_by":     request.session.get("username", ""),
        }
        rec = vat_manager.add_expense_record(company_id, expense_data)
        if "application/json" in ct:
            return {"success": True, "expense_id": rec.expense_id}
        flash(request, "Expense record added!", "success")
        return RedirectResponse("/vat/expenses", status_code=303)
    except Exception as e:
        if "application/json" in ct:
            raise HTTPException(status_code=400, detail=str(e))
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/vat/expenses/add", status_code=303)


@router.get("/capital", name="vat_capital_list")
async def capital_list(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    records    = vat_manager.get_company_capital_records(company_id)
    injections = [r for r in records if getattr(r, "transaction_type", "") == "INJECTION"]
    withdrawals = [r for r in records if getattr(r, "transaction_type", "") != "INJECTION"]
    ctx = template_context(request)
    ctx.update(
        capital_records=records, capital_transactions=records,
        injections_count=len(injections), withdrawals_count=len(withdrawals),
        total_injected=sum(r.amount for r in injections),
        total_withdrawn=sum(r.amount for r in withdrawals),
        total_vat=sum(getattr(r, "vat_amount", 0) for r in records),
        net_capital=sum(r.amount for r in injections) - sum(r.amount for r in withdrawals),
        total_capital=sum(r.amount for r in records),
    )
    return templates.TemplateResponse("vat/capital_list.html", ctx)


@router.get("/capital/add", name="vat_add_capital_get")
async def add_capital_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("vat/add_capital.html", template_context(request))


@router.post("/capital/add", name="vat_add_capital")
async def add_capital_post(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    data = await request.json()
    try:
        from decimal import Decimal as D
        cap_data = {
            "transaction_date": datetime.strptime(data.get("transaction_date"), "%Y-%m-%d").date(),
            "description":      data.get("description"),
            "capital_type":     data.get("capital_type"),
            "amount":           D(str(data.get("amount", 0))),
            "source":           data.get("source", ""),
            "created_by":       request.session.get("username", ""),
        }
        rec = vat_manager.add_capital_record(company_id, cap_data)
        return {"success": True, "capital_id": rec.capital_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/summary", name="vat_financial_summary")
async def financial_summary(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    start_date = request.query_params.get("start_date")
    end_date   = request.query_params.get("end_date")
    s = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else date(date.today().year, 1, 1)
    e = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else date.today()
    summary = vat_manager.generate_financial_summary(company_id, s, e)
    ctx = template_context(request)
    ctx.update(summary=summary, start_date=s, end_date=e)
    return templates.TemplateResponse("vat/financial_summary.html", ctx)
