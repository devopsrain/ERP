from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
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
