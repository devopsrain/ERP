"""Batch 2: vat, income_expense, transaction, inventory, multicompany, payroll"""
import os, textwrap

WEB = os.path.join(os.path.dirname(__file__), "web")

def write(filename, content):
    path = os.path.join(WEB, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    print(f"  wrote {filename}")

_HDR = '''\
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)
'''

# =============================================================================
# vat_routes.py
# =============================================================================
write("vat_routes.py", _HDR + '''
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
''')

# =============================================================================
# income_expense_routes.py  (keep functional parts, replace Flask patterns)
# =============================================================================
write("income_expense_routes.py", _HDR + '''
import io
from datetime import datetime, date
from income_expense_data_store import income_expense_store

router = APIRouter(prefix="/income-expense", tags=["income_expense"])


def _get_current_month_salary_data():
    try:
        import pandas as pd, os
        payroll_file = os.path.join("data", "employees.parquet")
        if not os.path.exists(payroll_file):
            return {"total_salary_expense": 0, "employee_count": 0, "base_salary": 0, "employer_costs": 0}
        df = pd.read_parquet(payroll_file)
        if df.empty:
            return {"total_salary_expense": 0, "employee_count": 0, "base_salary": 0, "employer_costs": 0}
        active = df[df.get("active", True) == True] if "active" in df.columns else df
        total  = float(active["basic_salary"].sum()) if "basic_salary" in active.columns else 0
        costs  = total * 0.16
        return {"total_salary_expense": total + costs, "employee_count": len(active),
                "base_salary": total, "employer_costs": costs}
    except Exception:
        return {"total_salary_expense": 0, "employee_count": 0, "base_salary": 0, "employer_costs": 0}


def _auto_create_it_expenses():
    try:
        current_month = date.today().strftime("%Y-%m")
        all_exp = income_expense_store.get_all_expense_records()
        has_it  = any(e.get("category") == "IT Services" and e.get("date", "").startswith(current_month)
                      for e in all_exp)
        if has_it:
            return
        for expense in [
            {"date": f"{current_month}-01", "description": "Monthly Internet Service - Ethio Telecom",
             "category": "IT Services", "gross_amount": 3500.0, "tax_rate": 0.15,
             "tax_amount": 525.0, "net_amount": 2975.0, "payment_method": "Bank Transfer"},
            {"date": f"{current_month}-01", "description": "Monthly Software Licenses",
             "category": "IT Services", "gross_amount": 2800.0, "tax_rate": 0.15,
             "tax_amount": 420.0, "net_amount": 2380.0, "payment_method": "Bank Transfer"},
        ]:
            try:
                income_expense_store.save_expense_record(expense)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Auto-create IT expenses failed: %s", e)


@router.get("/", name="income_expense_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    stats          = income_expense_store.get_summary_statistics()
    recent_income  = sorted(income_expense_store.get_all_income_records()[-10:],
                            key=lambda x: x.get("created_at", ""), reverse=True)[:10]
    recent_expenses = sorted(income_expense_store.get_all_expense_records()[-10:],
                             key=lambda x: x.get("created_at", ""), reverse=True)[:10]
    salary_data    = _get_current_month_salary_data()
    _auto_create_it_expenses()
    date_range     = income_expense_store.get_date_range()
    ctx = template_context(request)
    ctx.update(stats=stats, recent_income=recent_income, recent_expenses=recent_expenses,
               salary_data=salary_data, date_range=date_range)
    return templates.TemplateResponse("income_expense/dashboard.html", ctx)


@router.get("/income", name="income_expense_income_list")
async def income_list(request: Request, user=Depends(login_required)):
    records = sorted(income_expense_store.get_all_income_records(),
                     key=lambda x: x.get("date", ""), reverse=True)
    ctx = template_context(request)
    ctx.update(records=records)
    return templates.TemplateResponse("income_expense/income_list.html", ctx)


@router.get("/expenses", name="income_expense_expense_list")
async def expense_list(request: Request, user=Depends(login_required)):
    records = sorted(income_expense_store.get_all_expense_records(),
                     key=lambda x: x.get("date", ""), reverse=True)
    ctx = template_context(request)
    ctx.update(records=records)
    return templates.TemplateResponse("income_expense/expense_list.html", ctx)


@router.get("/add-income", name="income_expense_add_income_get")
async def add_income_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("income_expense/add_income.html", template_context(request))


@router.post("/add-income", name="income_expense_add_income")
async def add_income_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    record = {
        "date":           form.get("date", ""),
        "description":    form.get("description", "").strip(),
        "category":       form.get("category", "").strip(),
        "amount":         float(form.get("amount", 0) or 0),
        "tax_amount":     float(form.get("tax_amount", 0) or 0),
        "customer_name":  form.get("customer_name", "").strip(),
        "payment_method": form.get("payment_method", "").strip(),
    }
    try:
        income_expense_store.save_income_record(record)
        flash(request, "Income record added!", "success")
    except Exception as e:
        flash(request, f"Error: {e}", "error")
    return RedirectResponse("/income-expense/", status_code=303)


@router.get("/add-expense", name="income_expense_add_expense_get")
async def add_expense_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("income_expense/add_expense.html", template_context(request))


@router.post("/add-expense", name="income_expense_add_expense")
async def add_expense_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    record = {
        "date":           form.get("date", ""),
        "description":    form.get("description", "").strip(),
        "category":       form.get("category", "").strip(),
        "amount":         float(form.get("amount", 0) or 0),
        "tax_amount":     float(form.get("tax_amount", 0) or 0),
        "supplier_name":  form.get("supplier_name", "").strip(),
        "payment_method": form.get("payment_method", "").strip(),
    }
    try:
        income_expense_store.save_expense_record(record)
        flash(request, "Expense record added!", "success")
    except Exception as e:
        flash(request, f"Error: {e}", "error")
    return RedirectResponse("/income-expense/", status_code=303)


@router.get("/export", name="income_expense_export_excel")
async def export_excel(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = income_expense_store.export_to_excel()
        fname = f"income_expense_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Export failed: {e}", "error")
        return RedirectResponse("/income-expense/", status_code=302)
''')

# =============================================================================
# transaction_routes.py
# =============================================================================
write("transaction_routes.py", _HDR + '''
import io
import pandas as pd
from datetime import datetime
from transaction_data_store import TransactionDataStore
from siem_data_store import siem_store

router = APIRouter(prefix="/transactions", tags=["transaction"])
transaction_store = TransactionDataStore()


@router.get("/", name="transaction_dashboard")
@router.get("/dashboard", name="transaction_dashboard_alt")
async def dashboard(request: Request, user=Depends(login_required)):
    stats = transaction_store.get_summary_statistics()
    recent = transaction_store.get_import_history()[-5:]
    recent.reverse()
    _flagged_df = transaction_store.get_flagged_accounts()
    if hasattr(_flagged_df, "to_dict"):
        flagged = [] if _flagged_df.empty else _flagged_df.to_dict("records")
    else:
        flagged = list(_flagged_df) if _flagged_df else []
    ctx = template_context(request)
    ctx.update(stats=stats, recent_imports=recent, flagged_accounts=flagged)
    return templates.TemplateResponse("transaction/dashboard.html", ctx)


@router.get("/import", name="transaction_import_transactions_get")
async def import_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("transaction/import.html", template_context(request))


@router.post("/import", name="transaction_import_transactions")
async def import_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("excel_file")
    if not _file or not getattr(_file, "filename", None):  # type: ignore[union-attr]
        flash(request, "No file selected!", "error")
        return RedirectResponse("/transactions/import", status_code=303)
    if not _file.filename.lower().endswith((".xlsx", ".xls")):  # type: ignore[union-attr]
        flash(request, "Please upload an Excel file", "error")
        return RedirectResponse("/transactions/import", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        if df.empty:
            flash(request, "The Excel file is empty", "error")
            return RedirectResponse("/transactions/import", status_code=303)
        result = transaction_store.import_from_dataframe(df, _file.filename)
        siem_store.log_upload_event(request, module="transaction", endpoint="/transactions/import",
                                    filename=_file.filename,
                                    records_imported=result.get("imported", 0),
                                    status="success" if result["success"] else "failed",
                                    details=result.get("message", ""))
        ctx = template_context(request)
        ctx.update(result=result, filename=_file.filename)
        return templates.TemplateResponse("transaction/import_result.html", ctx)
    except Exception as e:
        flash(request, f"Error importing file: {e}", "error")
        return RedirectResponse("/transactions/import", status_code=303)


@router.get("/download-template", name="transaction_download_template")
async def download_template(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    filepath = transaction_store.generate_sample_excel()
    if filepath:
        return _FR(filepath, filename="transaction_import_template.xlsx",
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    flash(request, "Could not generate template", "danger")
    return RedirectResponse("/transactions/import", status_code=302)


@router.get("/list", name="transaction_transaction_list")
async def transaction_list(request: Request, user=Depends(login_required)):
    transactions = transaction_store.get_all_transactions()
    filter_type  = request.query_params.get("filter", "all")
    search_query = request.query_params.get("search", "").strip().lower()
    review_filter = request.query_params.get("review_status", "")
    if filter_type == "flagged":
        transactions = [t for t in transactions if t.get("is_flagged")]
    elif filter_type == "individual":
        transactions = [t for t in transactions if t.get("has_individual_name")]
    if review_filter:
        transactions = [t for t in transactions if t.get("review_status") == review_filter]
    if search_query:
        transactions = [t for t in transactions
                        if any(search_query in str(t.get(k, "")).lower()
                               for k in ["account_name","account_code","description","counterparty","reference"])]
    transactions.sort(key=lambda x: x.get("date", ""), reverse=True)
    ctx = template_context(request)
    ctx.update(transactions=transactions, stats=transaction_store.get_summary_statistics(),
               filter_type=filter_type, search_query=request.query_params.get("search", ""),
               review_filter=review_filter)
    return templates.TemplateResponse("transaction/transaction_list.html", ctx)


@router.get("/detail/{txn_id}", name="transaction_transaction_detail")
async def transaction_detail(txn_id: str, request: Request, user=Depends(login_required)):
    txn = transaction_store.get_transaction_by_id(txn_id)
    if not txn:
        flash(request, "Transaction not found", "danger")
        return RedirectResponse("/transactions/list", status_code=302)
    return templates.TemplateResponse("transaction/detail.html", {**template_context(request), "transaction": txn})


@router.post("/review/{txn_id}", name="transaction_review_transaction")
async def review_transaction(txn_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    status = form.get("review_status", "pending")
    notes  = form.get("reviewer_notes", "")
    if transaction_store.update_review_status(txn_id, status, notes):
        flash(request, f"Transaction marked as {status}", "success")
    else:
        flash(request, "Failed to update", "danger")
    return RedirectResponse("/transactions/list", status_code=303)


@router.post("/delete/{txn_id}", name="transaction_delete_transaction")
async def delete_transaction(txn_id: str, request: Request, user=Depends(login_required)):
    if transaction_store.delete_transaction(txn_id):
        flash(request, "Transaction deleted", "success")
    else:
        flash(request, "Failed to delete", "danger")
    return RedirectResponse("/transactions/list", status_code=303)


@router.get("/flagged-accounts", name="transaction_flagged_accounts")
async def flagged_accounts(request: Request, user=Depends(login_required)):
    accounts = transaction_store.get_flagged_accounts()
    return templates.TemplateResponse("transaction/flagged_accounts.html",
                                      {**template_context(request), "accounts": accounts})


@router.post("/flag-account", name="transaction_flag_account")
async def flag_account(request: Request, user=Depends(login_required)):
    form   = await request.form()
    code   = form.get("account_code", "").strip()
    name   = form.get("account_name", "").strip()
    reason = form.get("reason", "Manually flagged").strip()
    if not code:
        flash(request, "Account code is required", "danger")
        return RedirectResponse("/transactions/flagged-accounts", status_code=303)
    if transaction_store.add_flagged_account(code, name, reason, auto=False):
        flash(request, f"Account {code} flagged", "success")
    else:
        flash(request, "Failed to flag account", "danger")
    return RedirectResponse("/transactions/flagged-accounts", status_code=303)


@router.post("/unflag-account/{flag_id}", name="transaction_unflag_account")
async def unflag_account(flag_id: str, request: Request, user=Depends(login_required)):
    if transaction_store.remove_flagged_account(flag_id):
        flash(request, "Account unflagged", "success")
    else:
        flash(request, "Failed to unflag", "danger")
    return RedirectResponse("/transactions/flagged-accounts", status_code=303)


@router.get("/export", name="transaction_export_excel")
async def export_excel(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    filepath = transaction_store.export_to_excel()
    if filepath:
        from datetime import datetime as dt
        fname = f"transactions_{dt.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    flash(request, "Export failed", "danger")
    return RedirectResponse("/transactions/", status_code=302)
''')

# =============================================================================
# inventory_routes.py
# =============================================================================
write("inventory_routes.py", _HDR + '''
import io
import pandas as pd
from datetime import datetime
from inventory_data_store import InventoryDataStore

router = APIRouter(prefix="/inventory", tags=["inventory"])
inv_store = InventoryDataStore(data_dir="data")


@router.get("/", name="inventory_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(summary=inv_store.get_dashboard_summary())
    return templates.TemplateResponse("inventory/dashboard.html", ctx)


@router.get("/items", name="inventory_items_list")
async def items_list(request: Request, user=Depends(login_required)):
    category = request.query_params.get("category", "")
    status   = request.query_params.get("status", "")
    items    = inv_store.get_all_items(status=status or None, category=category or None)
    items.reverse()
    ctx = template_context(request)
    ctx.update(items=items, categories=inv_store.get_categories(),
               selected_category=category, selected_status=status)
    return templates.TemplateResponse("inventory/items_list.html", ctx)


@router.get("/items/add", name="inventory_add_item_get")
async def add_item_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("inventory/add_item.html",
                                      {**template_context(request),
                                       "item": {}, "categories": inv_store.get_categories()})


@router.post("/items/add", name="inventory_add_item")
async def add_item_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    item = {k: form.get(k, "").strip() for k in ["name","sku","category","description","unit",
            "serial_number","batch_number","barcode","location","is_rentable","valuation_method"]}
    for f in ["unit_price","cost_price","current_stock","min_stock_level","reorder_point","reorder_quantity"]:
        item[f] = float(form.get(f, 0) or 0)
    if not item["name"]:
        flash(request, "Item name is required", "error")
        return templates.TemplateResponse("inventory/add_item.html",
                                          {**template_context(request), "item": item,
                                           "categories": inv_store.get_categories()})
    if not item["sku"]:
        item["sku"] = inv_store.generate_sku(item["category"], item["name"])
    if inv_store.save_item(item):
        flash(request, f"Item '{item['name']}' added!", "success")
        return RedirectResponse("/inventory/items", status_code=303)
    flash(request, "Error saving item", "error")
    return templates.TemplateResponse("inventory/add_item.html",
                                      {**template_context(request), "item": item,
                                       "categories": inv_store.get_categories()})


@router.get("/items/edit/{item_id}", name="inventory_edit_item_get")
async def edit_item_get(item_id: str, request: Request, user=Depends(login_required)):
    item = inv_store.get_item_by_id(item_id)
    if not item:
        flash(request, "Item not found", "error")
        return RedirectResponse("/inventory/items", status_code=302)
    return templates.TemplateResponse("inventory/edit_item.html",
                                      {**template_context(request), "item": item,
                                       "categories": inv_store.get_categories()})


@router.post("/items/edit/{item_id}", name="inventory_edit_item")
async def edit_item_post(item_id: str, request: Request, user=Depends(login_required)):
    item = inv_store.get_item_by_id(item_id)
    if not item:
        flash(request, "Item not found", "error")
        return RedirectResponse("/inventory/items", status_code=302)
    form = await request.form()
    item.update({k: form.get(k, "").strip() for k in ["name","sku","category","description","unit",
                "serial_number","batch_number","barcode","location","is_rentable","valuation_method"]})
    for f in ["unit_price","cost_price","min_stock_level","reorder_point","reorder_quantity"]:
        item[f] = float(form.get(f, 0) or 0)
    if inv_store.save_item(item):
        flash(request, "Item updated!", "success")
        return RedirectResponse("/inventory/items", status_code=303)
    flash(request, "Error updating item", "error")
    return templates.TemplateResponse("inventory/edit_item.html",
                                      {**template_context(request), "item": item,
                                       "categories": inv_store.get_categories()})


@router.post("/items/delete/{item_id}", name="inventory_delete_item")
async def delete_item(item_id: str, request: Request, user=Depends(login_required)):
    inv_store.delete_item(item_id)
    flash(request, "Item deleted", "success")
    return RedirectResponse("/inventory/items", status_code=303)


@router.get("/items/import", name="inventory_import_items_get")
async def import_items_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("inventory/import_items.html", template_context(request))


@router.post("/items/import", name="inventory_import_items")
async def import_items_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("file")
    if not _file or not getattr(_file, "filename", None):  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/inventory/items/import", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        if df.empty:
            flash(request, "No data in file", "error")
            return RedirectResponse("/inventory/items/import", status_code=303)
        result = inv_store.import_items_from_dataframe(df, _file.filename)
        ctx = template_context(request)
        ctx.update(result=result, filename=_file.filename)
        return templates.TemplateResponse("inventory/import_result.html", ctx)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/inventory/items/import", status_code=303)


@router.get("/items/export", name="inventory_export_items")
async def export_items(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    filepath = inv_store.export_items_to_excel()
    if filepath:
        fname = f"inventory_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    flash(request, "Export failed", "error")
    return RedirectResponse("/inventory/items", status_code=302)


@router.get("/movements", name="inventory_movements_list")
async def movements_list(request: Request, user=Depends(login_required)):
    mtype     = request.query_params.get("type", "")
    movements = inv_store.get_all_movements(movement_type=mtype or None)
    movements.reverse()
    ctx = template_context(request)
    ctx.update(movements=movements, selected_type=mtype)
    return templates.TemplateResponse("inventory/movements_list.html", ctx)


@router.get("/valuation", name="inventory_valuation_report")
async def valuation_report(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(valuation=inv_store.get_valuation_report())
    return templates.TemplateResponse("inventory/valuation.html", ctx)


@router.get("/replenishment", name="inventory_replenishment")
async def replenishment(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(alerts=inv_store.get_replenishment_alerts())
    return templates.TemplateResponse("inventory/replenishment.html", ctx)


@router.get("/allocations", name="inventory_allocations")
async def allocations(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(allocations=inv_store.get_all_allocations())
    return templates.TemplateResponse("inventory/allocations.html", ctx)


@router.get("/maintenance", name="inventory_maintenance")
async def maintenance(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(schedules=inv_store.get_maintenance_schedules())
    return templates.TemplateResponse("inventory/maintenance.html", ctx)
''')

# =============================================================================
# multicompany_routes.py
# =============================================================================
write("multicompany_routes.py", _HDR + '''
from models.multi_company import Company, User, UserRole, SubscriptionPlan, CompanyStatus

router = APIRouter(prefix="/company", tags=["multicompany"])

def _user_manager():
    from multicompany_demo_setup import get_user_manager
    return get_user_manager()

def _payroll_manager(company_id):
    from core.multi_company_payroll import MultiCompanyPayrollManager
    mgr = MultiCompanyPayrollManager()
    mgr.switch_company(company_id)
    return mgr


@router.get("/login", name="multicompany_company_login")
async def company_login_get(request: Request):
    return templates.TemplateResponse("multicompany/login.html", template_context(request))


@router.post("/login", name="multicompany_company_login_post")
async def company_login_post(request: Request):
    form     = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    try:
        um   = _user_manager()
        user = um.authenticate(username, password)
        if user:
            request.session.update({
                "logged_in":         True,
                "user_id":           user.user_id,
                "username":          user.username,
                "full_name":         getattr(user, "full_name", username),
                "privilege_level":   getattr(user, "privilege_level", "viewer"),
            })
            companies = um.get_user_companies(user.user_id)
            if len(companies) == 1:
                request.session["current_company_id"] = companies[0].company_id
                return RedirectResponse("/company/dashboard", status_code=303)
            return RedirectResponse("/company/select", status_code=303)
    except Exception as e:
        logger.warning("Multicompany login error: %s", e)
    flash(request, "Invalid credentials", "error")
    return templates.TemplateResponse("multicompany/login.html", template_context(request))


@router.get("/logout", name="multicompany_company_logout")
async def company_logout(request: Request):
    request.session.clear()
    flash(request, "Logged out.", "info")
    return RedirectResponse("/company/login", status_code=302)


@router.get("/register", name="multicompany_company_register")
async def company_register_get(request: Request):
    return templates.TemplateResponse("multicompany/register.html", template_context(request))


@router.post("/register", name="multicompany_company_register_post")
async def company_register_post(request: Request):
    form = await request.form()
    username  = form.get("username", "").strip()
    password  = form.get("password", "")
    full_name = form.get("full_name", "").strip()
    email     = form.get("email", "").strip()
    try:
        um = _user_manager()
        if um.username_exists(username):
            flash(request, "Username already taken", "error")
            return templates.TemplateResponse("multicompany/register.html", template_context(request))
        user = um.create_user(username=username, password=password,
                              full_name=full_name, email=email)
        flash(request, "Account created! Please login.", "success")
        return RedirectResponse("/company/login", status_code=303)
    except Exception as e:
        flash(request, f"Registration failed: {e}", "error")
        return templates.TemplateResponse("multicompany/register.html", template_context(request))


@router.get("/select", name="multicompany_company_select")
async def company_select(request: Request, user=Depends(login_required)):
    try:
        um        = _user_manager()
        companies = um.get_user_companies(request.session.get("user_id"))
    except Exception:
        companies = []
    ctx = template_context(request)
    ctx.update(companies=companies)
    return templates.TemplateResponse("multicompany/company_select.html", ctx)


@router.get("/switch/{company_id}", name="multicompany_company_switch")
async def company_switch(company_id: str, request: Request, user=Depends(login_required)):
    request.session["current_company_id"] = company_id
    flash(request, "Company switched", "success")
    return RedirectResponse("/company/dashboard", status_code=302)


@router.get("/dashboard", name="multicompany_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    company_id = request.session.get("current_company_id")
    ctx = template_context(request)
    try:
        um      = _user_manager()
        company = um.get_company(company_id)
        role    = um.get_user_role(request.session.get("user_id"), company_id)
        ctx.update(company=company, role=role)
    except Exception:
        ctx.update(company=None, role=None)
    return templates.TemplateResponse("multicompany/dashboard.html", ctx)


@router.get("/create", name="multicompany_create_company_get")
async def create_company_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("multicompany/create.html", template_context(request))


@router.post("/create", name="multicompany_create_company")
async def create_company_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    name = form.get("company_name", "").strip()
    plan = form.get("subscription_plan", "basic")
    if not name:
        flash(request, "Company name is required", "error")
        return templates.TemplateResponse("multicompany/create.html", template_context(request))
    try:
        um      = _user_manager()
        company = um.create_company(name=name, plan=plan,
                                    owner_id=request.session.get("user_id"))
        flash(request, f"Company '{name}' created!", "success")
        return RedirectResponse("/company/select", status_code=303)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return templates.TemplateResponse("multicompany/create.html", template_context(request))


@router.get("/settings", name="multicompany_company_settings")
async def company_settings(request: Request, user=Depends(login_required)):
    company_id = request.session.get("current_company_id")
    ctx = template_context(request)
    try:
        um      = _user_manager()
        company = um.get_company(company_id)
        users   = um.get_company_users(company_id)
        ctx.update(company=company, users=users)
    except Exception:
        ctx.update(company=None, users=[])
    return templates.TemplateResponse("multicompany/settings.html", ctx)
''')

# =============================================================================
# payroll_routes.py  (was add_payroll_routes(app, ledger) — now APIRouter)
# =============================================================================
write("payroll_routes.py", _HDR + '''
import calendar
import io
from datetime import datetime, date, timedelta
from fastapi.responses import Response
from models.ethiopian_payroll import (
    Employee, EmployeeCategory, EthiopianPayrollCalculator,
    PayrollItem, AllowanceType, DeductionType,
)
from core.ethiopian_payroll_integration import EthiopianPayrollIntegration
from payroll_demo_data import add_demo_payroll_data
from employee_data_store import EmployeeDataStore

router = APIRouter(prefix="/payroll", tags=["payroll"])
_employee_store    = EmployeeDataStore()
_demo_data_loaded  = False

# Ledger is injected lazily so tests can import this module without a live DB.
_payroll_integration = None


def set_ledger(ledger):
    """Call this once from app.py after creating the ledger."""
    global _payroll_integration
    _payroll_integration = EthiopianPayrollIntegration(ledger)


def _get_integration():
    global _payroll_integration
    if _payroll_integration is None:
        from core.ledger import Ledger
        _payroll_integration = EthiopianPayrollIntegration(Ledger())
    return _payroll_integration


def _normalize_hire_date(raw):
    if isinstance(raw, str):
        return datetime.strptime(raw, "%Y-%m-%d").date()
    if hasattr(raw, "date") and callable(raw.date):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return date.today()


def _build_employee(data) -> Employee:
    return Employee(
        employee_id=data["employee_id"],
        name=data["name"],
        category=EmployeeCategory(data["category"]),
        basic_salary=float(data.get("basic_salary", 0) or 0),
        hire_date=_normalize_hire_date(data.get("hire_date", date.today())),
        department=data.get("department", ""),
        position=data.get("position", ""),
        bank_account=data.get("bank_account", ""),
        tin_number=data.get("tin_number", ""),
        pension_number=data.get("pension_number", ""),
        work_days_per_month=int(data.get("work_days_per_month", 22) or 22),
        work_hours_per_day=int(data.get("work_hours_per_day", 8) or 8),
        is_active=bool(data.get("is_active", True)),
    )


def _ensure_demo_data():
    global _demo_data_loaded
    if not _demo_data_loaded:
        df = _employee_store.read_all_employees()
        if df.empty:
            tmp: dict = {}
            add_demo_payroll_data(tmp)
            rows = []
            for emp in tmp.values():
                rows.append({
                    "employee_id": emp.employee_id, "name": emp.name,
                    "category": emp.category.value, "basic_salary": emp.basic_salary,
                    "hire_date": emp.hire_date, "department": emp.department,
                    "position": emp.position, "bank_account": emp.bank_account,
                    "tin_number": emp.tin_number, "pension_number": emp.pension_number,
                    "work_days_per_month": emp.work_days_per_month,
                    "work_hours_per_day": emp.work_hours_per_day,
                    "is_active": emp.is_active,
                })
            _employee_store.bulk_import(rows, overwrite=True)
        _demo_data_loaded = True


# ── Views ─────────────────────────────────────────────────────────────────────

@router.get("/", name="payroll_dashboard")
async def payroll_dashboard(request: Request, user=Depends(login_required)):
    _ensure_demo_data()
    df = _employee_store.get_active_employees()
    employees = {} if df.empty else {r["employee_id"]: r.to_dict() for _, r in df.iterrows()}
    ctx = template_context(request)
    ctx.update(employees=employees, employee_count=len(employees))
    return templates.TemplateResponse("payroll/dashboard.html", ctx)


@router.get("/employees", name="payroll_employees_list")
async def employees_list(request: Request, user=Depends(login_required)):
    _ensure_demo_data()
    df = _employee_store.read_all_employees()
    employees = {}
    if not df.empty:
        for _, row in df.iterrows():
            try:
                employees[row["employee_id"]] = _build_employee(row)
            except Exception as e:
                logger.warning("Skip employee %s: %s", row.get("employee_id"), e)
    ctx = template_context(request)
    ctx.update(employees=employees)
    return templates.TemplateResponse("payroll/employees.html", ctx)


@router.get("/employees/add", name="payroll_add_employee_get")
async def add_employee_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("payroll/add_employee.html",
                                      {**template_context(request), "categories": EmployeeCategory})


@router.post("/employees/add", name="payroll_add_employee")
async def add_employee_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    employee_id = form.get("employee_id", "").strip()
    tin_number  = form.get("tin_number", "").strip()
    ctx_base    = {**template_context(request), "categories": EmployeeCategory}
    if _employee_store.employee_exists(employee_id):
        flash(request, "Employee ID already exists!", "error")
        return templates.TemplateResponse("payroll/add_employee.html", ctx_base)
    if not tin_number:
        flash(request, "TIN Number is required!", "error")
        return templates.TemplateResponse("payroll/add_employee.html", ctx_base)
    try:
        emp_data = {
            "employee_id": employee_id, "name": form.get("name", ""),
            "category": form.get("category", ""), "basic_salary": float(form.get("basic_salary", 0)),
            "hire_date": datetime.strptime(form.get("hire_date", ""), "%Y-%m-%d").date(),
            "department": form.get("department", ""), "position": form.get("position", ""),
            "tin_number": tin_number, "pension_number": form.get("pension_number", ""),
        }
        _employee_store.add_employee(emp_data)
        flash(request, "Employee added successfully!", "success")
        return RedirectResponse("/payroll/employees", status_code=303)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return templates.TemplateResponse("payroll/add_employee.html", ctx_base)


@router.get("/employees/{employee_id}/edit", name="payroll_edit_employee_get")
async def edit_employee_get(employee_id: str, request: Request, user=Depends(login_required)):
    data = _employee_store.get_employee(employee_id)
    if not data:
        flash(request, "Employee not found!", "error")
        return RedirectResponse("/payroll/employees", status_code=302)
    try:
        employee = _build_employee(data)
    except Exception as e:
        flash(request, f"Error loading: {e}", "error")
        return RedirectResponse("/payroll/employees", status_code=302)
    return templates.TemplateResponse("payroll/edit_employee.html",
                                      {**template_context(request), "employee": employee,
                                       "categories": EmployeeCategory})


@router.post("/employees/{employee_id}/edit", name="payroll_edit_employee")
async def edit_employee_post(employee_id: str, request: Request, user=Depends(login_required)):
    data = _employee_store.get_employee(employee_id)
    if not data:
        flash(request, "Employee not found!", "error")
        return RedirectResponse("/payroll/employees", status_code=302)
    form = await request.form()
    tin_number = form.get("tin_number", "").strip()
    if not tin_number:
        flash(request, "TIN Number is required!", "error")
        employee = _build_employee(data)
        return templates.TemplateResponse("payroll/edit_employee.html",
                                          {**template_context(request), "employee": employee,
                                           "categories": EmployeeCategory})
    try:
        updated = {
            "name": form.get("name", ""), "category": form.get("category", ""),
            "basic_salary": float(form.get("basic_salary", 0)),
            "hire_date": datetime.strptime(form.get("hire_date", ""), "%Y-%m-%d").date(),
            "department": form.get("department", ""), "position": form.get("position", ""),
            "tin_number": tin_number, "pension_number": form.get("pension_number", ""),
            "is_active": "is_active" in form,
        }
        _employee_store.update_employee(employee_id, updated)
        flash(request, "Employee updated!", "success")
        return RedirectResponse("/payroll/employees", status_code=303)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/payroll/employees/edit/" + employee_id, status_code=302)


@router.get("/calculate", name="payroll_calculate_get")
async def calculate_get(request: Request, user=Depends(login_required)):
    today = date.today()
    df = _employee_store.get_active_employees()
    # Default to Jan 2026 if most employees hired then
    default_year, default_month = today.year, today.month
    if not df.empty:
        jan2026 = sum(1 for _, r in df.iterrows()
                      if (lambda h: h.year == 2026 and h.month == 1)(
                          datetime.strptime(str(r["hire_date"]), "%Y-%m-%d").date()
                          if isinstance(r["hire_date"], str) else r["hire_date"]))
        if jan2026 > len(df) // 2:
            default_year, default_month = 2026, 1
    start = date(default_year, default_month, 1)
    end   = date(default_year, default_month, calendar.monthrange(default_year, default_month)[1])
    active = []
    if not df.empty:
        for _, row in df.iterrows():
            try:
                active.append(_build_employee(row))
            except Exception:
                pass
    ctx = template_context(request)
    ctx.update(default_start=start, default_end=end, active_employees=active)
    return templates.TemplateResponse("payroll/calculate.html", ctx)


@router.post("/calculate", name="payroll_calculate")
async def calculate_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    try:
        pay_start = datetime.strptime(form.get("pay_period_start", ""), "%Y-%m-%d").date()
        pay_end   = datetime.strptime(form.get("pay_period_end",   ""), "%Y-%m-%d").date()
        df = _employee_store.get_active_employees()
        active = []
        if not df.empty:
            for _, row in df.iterrows():
                try:
                    emp = _build_employee(row)
                    if emp.hire_date <= pay_end:
                        active.append(emp)
                except Exception as e:
                    flash(request, f"Warning: could not process {row.get('employee_id')}: {e}", "warning")
        if not active:
            flash(request, "No active employees found!", "error")
            return RedirectResponse("/payroll/calculate", status_code=302)
        result = _get_integration().process_monthly_payroll(active, pay_start, pay_end)
        flash(request, f"Payroll processed for {result['payroll_summary']['total_employees']} employees!", "success")
        ctx = template_context(request)
        ctx.update(summary=result["payroll_summary"], payroll_items=result["payroll_items"],
                   journal_entries=result["journal_entries"])
        return templates.TemplateResponse("payroll/calculate_result.html", ctx)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/payroll/calculate", status_code=302)


@router.get("/employees/{employee_id}/payslip", name="payroll_generate_payslip")
async def generate_payslip(employee_id: str, request: Request, user=Depends(login_required)):
    data = _employee_store.get_employee(employee_id)
    if not data:
        flash(request, "Employee not found!", "error")
        return RedirectResponse("/payroll/employees", status_code=302)
    try:
        employee = _build_employee(data)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/payroll/employees", status_code=302)
    today = date.today()
    ps = date(today.year, today.month, 1)
    pe = date(today.year, today.month, 28)
    calc = EthiopianPayrollCalculator()
    item = PayrollItem(employee=employee, pay_period_start=ps, pay_period_end=pe)
    if employee.basic_salary > 10000:
        item.add_allowance(AllowanceType.TRANSPORT, 600, False, "Transport allowance")
        item.add_allowance(AllowanceType.HOUSING, min(employee.basic_salary * 0.2, 5000), True, "Housing allowance")
    calc_item = calc.calculate_payroll_item(item)
    payslip   = calc.generate_pay_slip(calc_item)
    return templates.TemplateResponse("payroll/payslip.html",
                                      {**template_context(request), "payslip": payslip, "employee": employee})


@router.get("/reports", name="payroll_reports")
async def payroll_reports(request: Request, user=Depends(login_required)):
    today = date.today()
    ps = date(today.year, today.month, 1)
    pe = date(today.year, today.month, 28)
    report = _get_integration().get_payroll_reports(ps, pe)
    ctx = template_context(request)
    ctx.update(report=report, period_start=ps, period_end=pe)
    return templates.TemplateResponse("payroll/reports.html", ctx)


@router.get("/tax-calculator", name="payroll_tax_calculator")
async def tax_calculator(request: Request, user=Depends(login_required)):
    calc = EthiopianPayrollCalculator()
    return templates.TemplateResponse("payroll/tax_calculator.html",
                                      {**template_context(request), "tax_brackets": calc.INCOME_TAX_BRACKETS})


@router.post("/api/tax-calculator", name="payroll_tax_calculator_api")
async def tax_calculator_api(request: Request):
    data = await request.json()
    try:
        taxable = float(data.get("taxable_income", 0))
        calc    = EthiopianPayrollCalculator()
        tax     = calc.calculate_income_tax(taxable)
        return {"taxable_income": taxable, "tax_amount": tax,
                "effective_rate": (tax / taxable * 100) if taxable > 0 else 0,
                "net_income": taxable - tax}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/employees/export-excel", name="payroll_export_excel")
async def export_excel(request: Request, user=Depends(login_required)):
    import pandas as pd
    _ensure_demo_data()
    df = _employee_store.read_all_employees()
    if df.empty:
        flash(request, "No employees to export!", "warning")
        return RedirectResponse("/payroll/employees", status_code=302)
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "Employee ID": row["employee_id"], "Name": row["name"],
            "Category": row["category"], "Basic Salary": row["basic_salary"],
            "Hire Date": str(row["hire_date"]), "Department": row.get("department", ""),
            "Position": row.get("position", ""), "TIN Number": row.get("tin_number", ""),
            "Pension Number": row.get("pension_number", ""),
            "Work Days/Month": row.get("work_days_per_month", 22),
            "Work Hours/Day": row.get("work_hours_per_day", 8),
            "Active": "Yes" if row.get("is_active", True) else "No",
        })
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Employees", index=False)
    output.seek(0)
    fname = f"employees_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(content=output.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/employees/download-template", name="payroll_download_template")
async def download_template(request: Request):
    import pandas as pd
    tpl = [{"Employee ID": "EMP001", "Name": "John Doe",
            "Category": "Regular Employee", "Basic Salary": 12000,
            "Hire Date": "2026-01-15", "Department": "Finance",
            "Position": "Accountant", "TIN Number": "TIN123456789",
            "Pension Number": "PEN987654321", "Work Days/Month": 22,
            "Work Hours/Day": 8, "Active": "Yes"}]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(tpl).to_excel(writer, sheet_name="Employee Template", index=False)
    output.seek(0)
    return Response(content=output.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=employee_import_template.xlsx"})


@router.get("/employees/import-excel", name="payroll_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("payroll/import_employees.html", template_context(request))


@router.post("/employees/import-excel", name="payroll_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    import pandas as pd
    form = await request.form()
    _file = form.get("excel_file")
    if not _file or not getattr(_file, "filename", None):  # type: ignore[union-attr]
        flash(request, "No file selected!", "error")
        return RedirectResponse("/payroll/employees/import-excel", status_code=303)
    content   = await _file.read()  # type: ignore[union-attr]
    df        = pd.read_excel(io.BytesIO(content), sheet_name=0)
    required  = ["Employee ID", "Name", "Category", "Basic Salary", "Hire Date", "TIN Number"]
    missing   = [c for c in required if c not in df.columns]
    if missing:
        flash(request, f"Missing columns: {', '.join(missing)}", "error")
        return RedirectResponse("/payroll/employees/import-excel", status_code=303)
    overwrite = form.get("overwrite") == "on"
    rows      = []
    errors    = []
    for idx, row in df.iterrows():
        try:
            emp_id = str(row["Employee ID"]).strip()
            name   = str(row["Name"]).strip()
            tin    = str(row["TIN Number"]).strip()
            if not emp_id or not name or not tin:
                errors.append(f"Row {idx+2}: required fields missing")
                continue
            if _employee_store.employee_exists(emp_id) and not overwrite:
                errors.append(f"Row {idx+2}: {emp_id} already exists")
                continue
            cat_map = {"Regular Employee": "Regular Employee",
                       "Contract Employee": "Contract Employee",
                       "Casual Worker": "Casual Worker", "Executive": "Executive"}
            cat = str(row["Category"]).strip()
            if cat not in cat_map:
                errors.append(f"Row {idx+2}: invalid category '{cat}'")
                continue
            rows.append({
                "employee_id": emp_id, "name": name, "category": cat_map[cat],
                "basic_salary": float(row["Basic Salary"]),
                "hire_date": pd.to_datetime(row["Hire Date"]).date(),
                "department": str(row.get("Department", "")).strip(),
                "position": str(row.get("Position", "")).strip(),
                "tin_number": tin,
                "pension_number": str(row.get("Pension Number", "")).strip(),
                "work_days_per_month": int(row.get("Work Days/Month", 22)),
                "work_hours_per_day": int(row.get("Work Hours/Day", 8)),
                "is_active": str(row.get("Active", "Yes")).strip().lower() in ["yes","1","true","active"],
            })
        except Exception as e:
            errors.append(f"Row {idx+2}: {e}")
    success = 0
    if rows:
        res     = _employee_store.bulk_import(rows, overwrite)
        success = res["success_count"]
        errors.extend(res.get("errors", []))
    if success:
        flash(request, f"Imported {success} employees!", "success")
    if errors:
        flash(request, "Errors: " + "; ".join(errors[:5]) +
              (f" (+{len(errors)-5} more)" if len(errors) > 5 else ""), "error")
    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(
            request, module="payroll", endpoint="/payroll/employees/import-excel",
            filename=getattr(_file, "filename", ""),
            records_imported=success,
            status="success" if success and not errors else ("partial" if success else "failed"),
            details=f"Imported {success}, {len(errors)} errors")
    except Exception:
        pass
    return RedirectResponse("/payroll/employees", status_code=303)
''')

print("Batch 2 done (vat, income_expense, transaction, inventory, multicompany, payroll)")
