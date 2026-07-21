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
               totals=totals,
               total_gross=totals["gross_amount"], total_vat=totals["vat_amount"],
               total_net=totals["net_amount"],
               income_categories=IncomeCategory,
               vat_types=VATType, filters={"start_date": start_date, "end_date": end_date, "category": category})
    return templates.TemplateResponse("vat/income_list.html", ctx)


@router.get("/income/add", name="vat_add_income_get")
async def add_income_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(income_categories=IncomeCategory, vat_types=VATType,
               vat_configs=vat_manager.get_vat_configurations(),
               recent_income=vat_manager.get_company_income_records(_company(request))[-5:])
    return templates.TemplateResponse("vat/add_income.html", ctx)


_VAT_DEFAULT_RATES = {
    "STANDARD": "0.15", "ZERO_RATED": "0", "EXEMPT": "0",
    "WITHHOLDING": "0.02", "WITHHELD": "0.02",
}


def _parse_enum(enum_cls, raw, default):
    """Accept enum NAMES (HTML form selects) or VALUES (JSON API clients)."""
    if not raw:
        return default
    if raw in enum_cls.__members__:
        return enum_cls[raw]
    return enum_cls(raw)


@router.post("/income/add", name="vat_add_income")
async def add_income_post(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    is_json = "application/json" in request.headers.get("content-type", "")
    if is_json:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)
    try:
        vat_type = _parse_enum(VATType, data.get("vat_type"), VATType.STANDARD)
        default_rate = _VAT_DEFAULT_RATES.get(vat_type.name, "0.15")
        income_data = {
            "contract_date":  datetime.strptime(data.get("contract_date"), "%Y-%m-%d").date(),
            "description":    data.get("description"),
            "category":       _parse_enum(IncomeCategory, data.get("category"),
                                          IncomeCategory.OTHER_INCOME),
            "gross_amount":   Decimal(str(data.get("gross_amount", 0))),
            "vat_type":       vat_type,
            "vat_rate":       Decimal(str(data.get("vat_rate") or default_rate)),
            "customer_name":  data.get("customer_name") or data.get("client_name", ""),
            "customer_tin":   data.get("customer_tin") or data.get("client_tin", ""),
            "invoice_number": data.get("invoice_number", ""),
            "created_by":     request.session.get("username", ""),
        }
        rec = vat_manager.add_income_record(company_id, income_data)
        if is_json:
            return {"success": True, "income_id": rec.income_id}
        flash(request, "Income record added!", "success")
        return RedirectResponse("/vat/income", status_code=303)
    except Exception as e:
        if is_json:
            raise HTTPException(status_code=400, detail=str(e))
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/vat/income/add", status_code=303)


# ── Excel import for income records ─────────────────────────────────────────

def _coerce_date(value):
    """Accept a date, datetime/pandas Timestamp, or 'YYYY-MM-DD' string."""
    if isinstance(value, datetime):  # datetime and pandas Timestamp
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


@router.get("/income/import", name="vat_import_income_get")
async def import_income_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(income_categories=IncomeCategory, vat_types=VATType)
    return templates.TemplateResponse("vat/import_income.html", ctx)


@router.get("/income/import/template", name="vat_income_import_template")
async def income_import_template(request: Request, user=Depends(login_required)):
    import io
    import pandas as pd
    from fastapi.responses import Response
    sample = pd.DataFrame([
        {"contract_date": "2026-07-01", "description": "Consulting invoice #042",
         "category": "SERVICE_INCOME", "vat_type": "STANDARD", "gross_amount": 115000,
         "customer_name": "Ethio Telecom", "customer_tin": "123456789",
         "invoice_number": "INV-042"},
        {"contract_date": "2026-07-05", "description": "Product sale",
         "category": "SALES_REVENUE", "vat_type": "EXEMPT", "gross_amount": 40000,
         "customer_name": "Awash Bank", "customer_tin": "", "invoice_number": ""},
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sample.to_excel(writer, sheet_name="Income", index=False)
        pd.DataFrame({
            "column": ["contract_date", "description", "category", "vat_type",
                       "gross_amount", "customer_name", "customer_tin", "invoice_number"],
            "required": ["yes", "yes", "no (default OTHER_INCOME)", "no (default STANDARD)",
                         "yes", "no", "no", "no"],
            "notes": ["YYYY-MM-DD",
                      "Free text",
                      "One of: " + ", ".join(c.name for c in IncomeCategory),
                      "One of: " + ", ".join(t.name for t in VATType),
                      "Gross amount incl. VAT, in ETB",
                      "Customer / client name", "9-digit TIN", "Invoice reference"],
        }).to_excel(writer, sheet_name="Field Descriptions", index=False)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="vat_income_import_template.xlsx"'},
    )


@router.post("/income/import", name="vat_import_income")
async def import_income_post(request: Request, user=Depends(login_required)):
    import io
    import pandas as pd
    company_id = _company(request)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        flash(request, "Please choose an Excel file to import.", "error")
        return RedirectResponse("/vat/income/import", status_code=303)
    try:
        df = pd.read_excel(io.BytesIO(await upload.read()))
    except Exception as e:
        flash(request, f"Could not read Excel file: {e}", "error")
        return RedirectResponse("/vat/income/import", status_code=303)

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    imported, errors = 0, []
    for idx, row in df.iterrows():
        rownum = idx + 2  # header is row 1 in Excel
        try:
            raw = {k: ("" if pd.isna(v) else v) for k, v in row.items()}
            if not str(raw.get("description", "")).strip() and not raw.get("gross_amount"):
                continue  # skip fully blank rows
            vat_type = _parse_enum(VATType,
                                   str(raw.get("vat_type", "")).strip().upper() or None,
                                   VATType.STANDARD)
            income_data = {
                "contract_date":  _coerce_date(raw.get("contract_date") or raw.get("date")),
                "description":    str(raw.get("description", "")).strip(),
                "category":       _parse_enum(IncomeCategory,
                                              str(raw.get("category", "")).strip().upper() or None,
                                              IncomeCategory.OTHER_INCOME),
                "gross_amount":   Decimal(str(raw.get("gross_amount") or raw.get("amount") or 0)),
                "vat_type":       vat_type,
                "vat_rate":       Decimal(_VAT_DEFAULT_RATES.get(vat_type.name, "0.15")),
                "customer_name":  str(raw.get("customer_name") or raw.get("client_name") or ""),
                "customer_tin":   str(raw.get("customer_tin") or raw.get("client_tin") or ""),
                "invoice_number": str(raw.get("invoice_number", "")),
                "created_by":     request.session.get("username", ""),
            }
            vat_manager.add_income_record(company_id, income_data)
            imported += 1
        except Exception as e:
            errors.append(f"Row {rownum}: {e}")

    if imported:
        flash(request, f"Imported {imported} income record(s).", "success")
    if errors:
        shown = "; ".join(errors[:5]) + (f" (+{len(errors)-5} more)" if len(errors) > 5 else "")
        flash(request, f"{len(errors)} row(s) skipped — {shown}", "warning")
    if not imported and not errors:
        flash(request, "The file contained no data rows.", "warning")
    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(
            request, module="vat", endpoint="/vat/income/import",
            filename=upload.filename, records_imported=imported,
            status="success" if not errors else ("partial" if imported else "failed"),
            details=f"income import: {imported} ok, {len(errors)} errors")
    except Exception:
        pass
    return RedirectResponse("/vat/income", status_code=303)


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
               totals=totals,
               total_gross=totals["gross_amount"], total_vat=totals["vat_amount"],
               total_net=totals["net_amount"],
               expense_categories=ExpenseCategory,
               vat_types=VATType, filters={"start_date": start_date, "end_date": end_date, "category": category})
    return templates.TemplateResponse("vat/expense_list.html", ctx)


@router.get("/expenses/add", name="vat_add_expense_get")
async def add_expense_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(expense_categories=ExpenseCategory, vat_types=VATType,
               vat_configs=vat_manager.get_vat_configurations(),
               recent_expenses=vat_manager.get_company_expense_records(_company(request))[-5:])
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
            "category":       ExpenseCategory[data.get("category").upper()],
            "gross_amount":   Decimal(str(data.get("gross_amount", 0))),
            "vat_type":       VATType[data.get("vat_type").upper()],
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
    form = await request.form()
    try:
        from decimal import Decimal as D
        cap_data = {
            "transaction_date": datetime.strptime(form.get("transaction_date"), "%Y-%m-%d").date(),
            "description":      form.get("description", ""),
            "capital_type":     form.get("capital_type", ""),
            "transaction_type": form.get("transaction_type") or "INJECTION",
            "amount":           D(str(form.get("amount", 0))),
            "source":           form.get("source_destination", ""),
            "created_by":       request.session.get("username", ""),
        }
        rec = vat_manager.add_capital_record(company_id, cap_data)
        flash(request, f"Capital transaction added successfully (ID: {rec.capital_id})", "success")
        return RedirectResponse("/vat/capital", status_code=303)
    except Exception as e:
        logger.error(f"Error adding capital: {e}")
        flash(request, f"Error adding capital: {str(e)}", "error")
        return templates.TemplateResponse("vat/add_capital.html", template_context(request))


@router.get("/summary", name="vat_financial_summary")
async def financial_summary(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    start_date = request.query_params.get("start_date")
    end_date   = request.query_params.get("end_date")
    s = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else date(date.today().year, 1, 1)
    e = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else date.today()
    summary = vat_manager.generate_financial_summary(company_id, s, e)
    ctx = template_context(request)
    ctx.update(
        summary=summary, start_date=s, end_date=e,
        income_transactions=vat_manager.get_company_income_records(company_id, s, e),
        expense_transactions=vat_manager.get_company_expense_records(company_id, s, e),
        capital_transactions=vat_manager.get_company_capital_records(company_id, s, e),
    )
    return templates.TemplateResponse("vat/financial_summary.html", ctx)

# ── VAT configuration page ─────────────────────────────────────────────────
# Rendering dashboard.html without the dashboard context crashes (undefined
# loops), so send /config to the real dashboard until a config page exists.
@router.get("/config", name="vat_vat_config")
async def vat_config(request: Request, user=Depends(login_required)):
    return RedirectResponse("/vat/dashboard", status_code=302)
