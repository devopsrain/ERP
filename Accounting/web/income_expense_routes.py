from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required, current_company
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

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
    company_id = current_company(request)
    record = {
        "date":           form.get("date", ""),
        "description":    form.get("description", "").strip(),
        "category":       form.get("category", "").strip(),
        "amount":         float(form.get("amount", 0) or 0),
        "tax_amount":     float(form.get("tax_amount", 0) or 0),
        "customer_name":  form.get("customer_name", "").strip(),
        "payment_method": form.get("payment_method", "").strip(),
        "company_id":     company_id,
        "created_by":     request.session.get("username", ""),
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
    company_id = current_company(request)
    record = {
        "date":           form.get("date", ""),
        "description":    form.get("description", "").strip(),
        "category":       form.get("category", "").strip(),
        "amount":         float(form.get("amount", 0) or 0),
        "tax_amount":     float(form.get("tax_amount", 0) or 0),
        "supplier_name":  form.get("supplier_name", "").strip(),
        "payment_method": form.get("payment_method", "").strip(),
        "company_id":     company_id,
        "created_by":     request.session.get("username", ""),
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


@router.get("/download-sample", name="income_expense_download_sample")
async def download_sample(request: Request, user=Depends(login_required)):
    """Download a sample Excel template for income/expense import."""
    import pandas as pd
    import tempfile
    import os
    from fastapi.responses import FileResponse as _FR
    
    income_sample = {
        'date': ['2024-01-15', '2024-01-20', '2024-01-25'],
        'description': ['Client payment - ABC Corp', 'Service revenue', 'Product sale'],
        'category': ['Services', 'Services', 'Products'],
        'amount': [15000.00, 8500.00, 25000.00],
        'tax_amount': [2250.00, 1275.00, 3750.00],
        'customer_name': ['ABC Corp', 'XYZ Ltd', 'Client Co'],
        'payment_method': ['Bank Transfer', 'Bank Transfer', 'Cash'],
    }
    
    expense_sample = {
        'date': ['2024-01-10', '2024-01-18', '2024-01-22'],
        'description': ['Office rent', 'Utility bill', 'Office supplies'],
        'category': ['Rent', 'Utilities', 'Supplies'],
        'amount': [12000.00, 3500.00, 1500.00],
        'tax_amount': [0.00, 525.00, 225.00],
        'supplier_name': ['Property Management', 'Ethio Telecom', 'Office Depot'],
        'payment_method': ['Bank Transfer', 'Bank Transfer', 'Cash'],
    }
    
    fd, filepath = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        pd.DataFrame(income_sample).to_excel(writer, sheet_name='Income', index=False)
        pd.DataFrame(expense_sample).to_excel(writer, sheet_name='Expenses', index=False)
    
    return _FR(filepath, filename="income_expense_template.xlsx",
               media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/reports", name="income_expense_reports")
async def reports(request: Request, user=Depends(login_required)):
    """Income & Expense reports with filtering by period."""
    from collections import defaultdict
    from datetime import timedelta
    
    period = request.query_params.get("period", "all")
    start_date = request.query_params.get("start_date", "")
    end_date = request.query_params.get("end_date", "")
    
    # Handle quick period selections
    today = date.today()
    if period == "month":
        start_date = today.replace(day=1).isoformat()
        end_date = today.isoformat()
    elif period == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start_date = today.replace(month=quarter_start_month, day=1).isoformat()
        end_date = today.isoformat()
    elif period == "year":
        start_date = today.replace(month=1, day=1).isoformat()
        end_date = today.isoformat()
    
    # Get filtered data
    stats = income_expense_store.get_summary(start_date=start_date or None, end_date=end_date or None)
    
    # Get all records for category breakdown
    income_records = income_expense_store.get_all_income_records()
    expense_records = income_expense_store.get_all_expense_records()
    
    # Filter by date range if specified
    if start_date:
        income_records = [r for r in income_records if r.get("date", "") >= start_date]
        expense_records = [r for r in expense_records if r.get("date", "") >= start_date]
    if end_date:
        income_records = [r for r in income_records if r.get("date", "") <= end_date]
        expense_records = [r for r in expense_records if r.get("date", "") <= end_date]
    
    # Calculate category breakdowns
    income_by_category = defaultdict(float)
    expense_by_category = defaultdict(float)
    
    for r in income_records:
        cat = r.get("category", "Uncategorized") or "Uncategorized"
        income_by_category[cat] += float(r.get("amount", 0) or 0)
    
    for r in expense_records:
        cat = r.get("category", "Uncategorized") or "Uncategorized"
        expense_by_category[cat] += float(r.get("amount", 0) or 0)
    
    record_count = {
        "income": len(income_records),
        "expense": len(expense_records)
    }
    
    ctx = template_context(request)
    ctx.update(
        stats=stats,
        record_count=record_count,
        income_by_category=dict(income_by_category),
        expense_by_category=dict(expense_by_category),
        period=period,
        start_date=start_date,
        end_date=end_date
    )
    return templates.TemplateResponse("income_expense/reports.html", ctx)


@router.get("/import-excel", name="income_expense_import_excel")
async def import_excel_get(request: Request, user=Depends(login_required)):
    """Display the Excel import form."""
    return templates.TemplateResponse("income_expense/import_excel.html", template_context(request))


@router.post("/import-excel", name="income_expense_import_excel_post")
async def import_excel_post(request: Request, user=Depends(login_required)):
    """Process uploaded Excel file with income and expense data."""
    import pandas as pd
    
    form = await request.form()
    excel_file = form.get("excel_file")
    
    if not excel_file or not excel_file.filename:
        flash(request, "No file uploaded", "error")
        return RedirectResponse("/income-expense/import-excel", status_code=303)
    
    try:
        contents = await excel_file.read()
        xls = pd.ExcelFile(io.BytesIO(contents))
        company_id = current_company(request)
        username   = request.session.get("username", "")
        imported_income = 0
        imported_expense = 0
        errors = []
        
        # Try to read Income sheet
        for sheet_name in ['Income', 'Income Records', 'income']:
            if sheet_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet_name)
                for _, row in df.iterrows():
                    try:
                        record = {
                            "date": str(row.get("date", ""))[:10] if pd.notna(row.get("date")) else "",
                            "description": str(row.get("description", "")) if pd.notna(row.get("description")) else "",
                            "category": str(row.get("category", "")) if pd.notna(row.get("category")) else "",
                            "amount": float(row.get("amount", 0) or row.get("gross_amount", 0) or 0),
                            "tax_amount": float(row.get("tax_amount", 0) or 0),
                            "customer_name": str(row.get("customer_name", "") or row.get("client_name", "")) if pd.notna(row.get("customer_name", row.get("client_name"))) else "",
                            "payment_method": str(row.get("payment_method", "")) if pd.notna(row.get("payment_method")) else "",
                            "company_id": company_id,
                            "created_by": username,
                        }
                        if record["date"] and record["amount"]:
                            income_expense_store.save_income_record(record)
                            imported_income += 1
                    except Exception as e:
                        errors.append(f"Income row error: {e}")
                break
        
        # Try to read Expense sheet
        for sheet_name in ['Expenses', 'Expense Records', 'expenses']:
            if sheet_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet_name)
                for _, row in df.iterrows():
                    try:
                        record = {
                            "date": str(row.get("date", ""))[:10] if pd.notna(row.get("date")) else "",
                            "description": str(row.get("description", "")) if pd.notna(row.get("description")) else "",
                            "category": str(row.get("category", "")) if pd.notna(row.get("category")) else "",
                            "amount": float(row.get("amount", 0) or row.get("gross_amount", 0) or 0),
                            "tax_amount": float(row.get("tax_amount", 0) or 0),
                            "supplier_name": str(row.get("supplier_name", "")) if pd.notna(row.get("supplier_name")) else "",
                            "payment_method": str(row.get("payment_method", "")) if pd.notna(row.get("payment_method")) else "",
                            "company_id": company_id,
                            "created_by": username,
                        }
                        if record["date"] and record["amount"]:
                            income_expense_store.save_expense_record(record)
                            imported_expense += 1
                    except Exception as e:
                        errors.append(f"Expense row error: {e}")
                break
        
        if imported_income > 0 or imported_expense > 0:
            flash(request, f"Imported {imported_income} income and {imported_expense} expense records", "success")
        else:
            flash(request, "No records imported. Check file format.", "warning")
        
        if errors:
            flash(request, f"{len(errors)} errors occurred during import", "warning")
            
    except Exception as e:
        flash(request, f"Import error: {e}", "error")
    
    return RedirectResponse("/income-expense/", status_code=303)

# ── Detail / delete / IT-payroll auto-create stubs ────────────────────────
@router.get("/income/{record_id}", name="income_expense_view_income")
async def view_income(record_id: int, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    rows = [r for r in income_expense_store.get_income(company_id=cid) if r.get("id") == record_id]
    record = rows[0] if rows else None
    if not record:
        flash(request, "Record not found", "error")
        return RedirectResponse("/income-expense/income", status_code=302)
    ctx = template_context(request)
    ctx.update(record=record, kind="income")
    return templates.TemplateResponse("income_expense/view_record.html", ctx)


@router.get("/expense/{record_id}", name="income_expense_view_expense")
async def view_expense(record_id: int, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    rows = [r for r in income_expense_store.get_expenses(company_id=cid) if r.get("id") == record_id]
    record = rows[0] if rows else None
    if not record:
        flash(request, "Record not found", "error")
        return RedirectResponse("/income-expense/expenses", status_code=302)
    ctx = template_context(request)
    ctx.update(record=record, kind="expense")
    return templates.TemplateResponse("income_expense/view_record.html", ctx)


@router.post("/income/{record_id}/delete", name="income_expense_delete_income")
async def delete_income(record_id: int, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    if income_expense_store.delete_income(record_id, company_id=cid):
        flash(request, "Income record deleted", "success")
    else:
        flash(request, "Delete failed", "error")
    return RedirectResponse("/income-expense/income", status_code=303)


@router.post("/expense/{record_id}/delete", name="income_expense_delete_expense")
async def delete_expense(record_id: int, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    if income_expense_store.delete_expense(record_id, company_id=cid):
        flash(request, "Expense record deleted", "success")
    else:
        flash(request, "Delete failed", "error")
    return RedirectResponse("/income-expense/expenses", status_code=303)


@router.post("/create-monthly-it-expenses", name="income_expense_create_monthly_it_expenses")
async def create_monthly_it_expenses(request: Request, user=Depends(login_required)):
    try:
        count = _auto_create_it_expenses()
        flash(request, f"Created {count} monthly IT expense entries", "success")
    except Exception as e:
        flash(request, f"Failed: {e}", "error")
    return RedirectResponse("/income-expense/", status_code=303)
