from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

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
