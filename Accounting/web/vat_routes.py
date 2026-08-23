from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required, current_company
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
    # Unified fallback is "default" (was "demo_company" — legacy rows are
    # re-homed by aws-deployment/init_db.sql).
    return current_company(request)


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
        "penalty_fee":  sum(r.penalty_fee  for r in records),
    }
    ctx = template_context(request)
    ctx.update(income_records=records, income_transactions=records,
               totals=totals,
               total_gross=totals["gross_amount"], total_vat=totals["vat_amount"],
               total_net=totals["net_amount"], total_penalty_fee=totals["penalty_fee"],
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
            "income_date":    (datetime.strptime(data["income_date"], "%Y-%m-%d").date()
                               if data.get("income_date") else None),
            "description":    data.get("description"),
            "category":       _parse_enum(IncomeCategory, data.get("category"),
                                          IncomeCategory.OTHER_INCOME),
            "gross_amount":   Decimal(str(data.get("gross_amount", 0))),
            "vat_type":       vat_type,
            "vat_rate":       Decimal(str(data.get("vat_rate") or default_rate)),
            "customer_name":  data.get("customer_name") or data.get("client_name", ""),
            "customer_tin":   data.get("customer_tin") or data.get("client_tin", ""),
            "invoice_number": data.get("invoice_number", ""),
            "tender_id":      (data.get("tender_id") or "").strip(),
            "payment_mode":   (data.get("payment_mode") or "").strip().lower(),
            "income_type":    (data.get("income_type") or "").strip().lower(),
            "penalty":        "yes" if (data.get("penalty") or "").strip().lower() in ("yes", "on", "true", "1") else "no",
            "brand":          (data.get("brand") or "").strip(),
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
        {"contract_date": "2026-07-01", "income_date": "2026-07-03",
         "description": "Consulting invoice #042",
         "category": "SERVICE_INCOME", "vat_type": "STANDARD", "gross_amount": 115000,
         "customer_name": "Ethio Telecom", "customer_tin": "123456789",
         "invoice_number": "INV-042", "tender_id": "BID-2026-014", "payment_mode": "advance",
         "income_type": "service", "penalty": "no", "brand": "Cisco"},
        {"contract_date": "2026-07-05", "income_date": "2026-07-05",
         "description": "Product sale",
         "category": "SALES_REVENUE", "vat_type": "EXEMPT", "gross_amount": 40000,
         "customer_name": "Awash Bank", "customer_tin": "", "invoice_number": "",
         "tender_id": "", "payment_mode": "total",
         "income_type": "hardware", "penalty": "yes", "brand": "Tenable"},
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sample.to_excel(writer, sheet_name="Income", index=False)
        pd.DataFrame({
            "column": ["contract_date", "income_date", "description", "category", "vat_type",
                       "gross_amount", "customer_name", "customer_tin", "invoice_number",
                       "tender_id", "payment_mode", "income_type", "penalty", "brand"],
            "required": ["yes", "no (defaults to contract_date)", "yes",
                         "no (default OTHER_INCOME)", "no (default STANDARD)",
                         "yes", "no", "no", "no", "no", "no", "no", "no (default 'no')", "no"],
            "notes": ["YYYY-MM-DD — agreement date",
                      "YYYY-MM-DD — date revenue received (used for period filters)",
                      "Free text",
                      "One of: " + ", ".join(c.name for c in IncomeCategory),
                      "One of: " + ", ".join(t.name for t in VATType),
                      "Gross amount incl. VAT, in ETB",
                      "Customer / client name", "9-digit TIN", "Invoice reference",
                      "Tender/bid reference this income relates to",
                      "'advance' or 'total'",
                      "'hardware', 'software' or 'service'",
                      "'yes' or 'no' — penalty fee (10% of gross) is auto-calculated",
                      "Brand of the goods/services (e.g. Cisco, Tenable)"],
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
                "income_date":    (_coerce_date(raw.get("income_date"))
                                   if raw.get("income_date") else None),
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
                "tender_id":      str(raw.get("tender_id", "")).strip(),
                "payment_mode":   str(raw.get("payment_mode", "")).strip().lower(),
                "income_type":    str(raw.get("income_type", "")).strip().lower(),
                "penalty":        "yes" if str(raw.get("penalty", "")).strip().lower() in ("yes", "true", "1") else "no",
                "brand":          str(raw.get("brand", "")).strip(),
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


# ── Single income record: JSON detail + edit ────────────────────────────────
# CRITICAL: these parametric routes MUST stay registered AFTER the static
# /income/add, /income/import and /income/import/template routes above —
# FastAPI matches in registration order, so an earlier /income/{income_id}
# would shadow them ("add" would be parsed as an income_id).

def _stored_enum(enum_cls, raw, default):
    """Enum member for a stored string that may be a NAME or a VALUE."""
    try:
        return _parse_enum(enum_cls, str(raw) if raw else None, default)
    except (KeyError, ValueError):
        return default


def _income_row_json(row: dict) -> dict:
    """Serialize a vat_income DB row: dates ISO, Decimals as floats,
    enums as .name (plus a *_value alias with the display value)."""
    def iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def num(v):
        return float(v) if isinstance(v, Decimal) else v

    category = _stored_enum(IncomeCategory, row.get("category"), IncomeCategory.OTHER_INCOME)
    vat_type = _stored_enum(VATType, row.get("vat_type"), VATType.STANDARD)
    customer_name = row.get("customer_name") or ""
    customer_tin  = row.get("customer_tin") or ""
    return {
        "income_id":      row.get("income_id"),
        "company_id":     row.get("company_id"),
        "contract_date":  iso(row.get("contract_date")),
        "income_date":    iso(row.get("income_date") or row.get("contract_date")),
        "description":    row.get("description") or "",
        "category":       category.name,
        "category_value": category.value,
        "vat_type":       vat_type.name,
        "vat_type_value": vat_type.value,
        "gross_amount":   num(row.get("gross_amount") or 0),
        "vat_rate":       num(row.get("vat_rate") or 0),
        "vat_amount":     num(row.get("vat_amount") or 0),
        "net_amount":     num(row.get("net_amount") or 0),
        "customer_name":  customer_name,
        "client_name":    customer_name,   # template JS alias
        "customer_tin":   customer_tin,
        "client_tin":     customer_tin,    # template JS alias
        "invoice_number": row.get("invoice_number") or "",
        "tender_id":      row.get("tender_id") or "",
        "payment_mode":   row.get("payment_mode") or "",
        "income_type":    row.get("income_type") or "",
        "penalty":        row.get("penalty") or "no",
        "penalty_fee":    num(row.get("penalty_fee") or 0),
        "brand":          row.get("brand") or "",
        "created_date":   iso(row.get("created_date")),
        "updated_date":   iso(row.get("updated_date")),
        "created_by":     row.get("created_by") or "",
        "is_active":      bool(row.get("is_active", True)),
    }


@router.get("/income/{income_id}", name="vat_income_detail")
async def income_detail(income_id: str, request: Request, user=Depends(login_required)):
    """JSON detail for one income record (used by the list-page modals)."""
    company_id = _company(request)
    row = vat_data_store.get_income_record(company_id, income_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Income record not found")
    return {"success": True, "income": _income_row_json(row)}


@router.post("/income/{income_id}/edit", name="vat_income_edit")
async def income_edit(income_id: str, request: Request, user=Depends(login_required)):
    """Update one income record (JSON or form). vat_amount / net_amount /
    penalty_fee are ALWAYS recomputed server-side via IncomeRecord rules."""
    company_id = _company(request)
    row = vat_data_store.get_income_record(company_id, income_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Income record not found")
    is_json = "application/json" in request.headers.get("content-type", "")
    if is_json:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)
    try:
        old_vat_type = _stored_enum(VATType, row.get("vat_type"), VATType.STANDARD)
        vat_type = _parse_enum(VATType, data.get("vat_type"), old_vat_type)
        if data.get("vat_rate"):
            vat_rate = Decimal(str(data["vat_rate"]))
        elif vat_type != old_vat_type:
            # VAT type changed without an explicit rate — use the new default
            vat_rate = Decimal(_VAT_DEFAULT_RATES.get(vat_type.name, "0.15"))
        elif row.get("vat_rate") is not None:
            # Preserve the stored rate (including a legitimate 0 for
            # ZERO_RATED / EXEMPT — `or` would silently bump it to 0.15)
            vat_rate = Decimal(str(row["vat_rate"]))
        else:
            vat_rate = Decimal(_VAT_DEFAULT_RATES.get(vat_type.name, "0.15"))
        raw_penalty = data.get("penalty")
        if raw_penalty is None:
            penalty = row.get("penalty") or "no"
        else:
            penalty = "yes" if str(raw_penalty).strip().lower() in ("yes", "on", "true", "1") else "no"

        def txt(key, *aliases):
            """Submitted value wins (empty string clears); else keep stored."""
            for k in (key,) + aliases:
                if k in data:
                    return str(data[k]).strip()
            return str(row.get(key) or "")

        # Rebuild the record so __post_init__ recomputes vat/net/penalty_fee
        rec = IncomeRecord(
            income_id=income_id,
            company_id=company_id,
            contract_date=(datetime.strptime(data["contract_date"], "%Y-%m-%d").date()
                           if data.get("contract_date") else row.get("contract_date")),
            income_date=(datetime.strptime(data["income_date"], "%Y-%m-%d").date()
                         if data.get("income_date")
                         else row.get("income_date") or row.get("contract_date")),
            description=txt("description"),
            category=_parse_enum(IncomeCategory, data.get("category"),
                                 _stored_enum(IncomeCategory, row.get("category"),
                                              IncomeCategory.OTHER_INCOME)),
            gross_amount=Decimal(str(data.get("gross_amount") or row.get("gross_amount") or 0)),
            vat_type=vat_type,
            vat_rate=vat_rate,
            customer_name=txt("customer_name", "client_name"),
            customer_tin=txt("customer_tin", "client_tin"),
            invoice_number=txt("invoice_number"),
            tender_id=txt("tender_id"),
            payment_mode=txt("payment_mode").lower(),
            income_type=txt("income_type").lower(),
            penalty=penalty,
            brand=txt("brand"),
        )
        updates = {
            "contract_date":  rec.contract_date,
            "income_date":    rec.income_date,
            "description":    rec.description,
            "category":       rec.category.value,
            "gross_amount":   float(rec.gross_amount),
            "vat_type":       rec.vat_type.value,
            "vat_rate":       float(rec.vat_rate),
            "vat_amount":     float(rec.vat_amount),
            "net_amount":     float(rec.net_amount),
            "customer_name":  rec.customer_name,
            "customer_tin":   rec.customer_tin,
            "invoice_number": rec.invoice_number,
            "tender_id":      rec.tender_id,
            "payment_mode":   rec.payment_mode,
            "income_type":    rec.income_type,
            "penalty":        rec.penalty,
            "penalty_fee":    float(rec.penalty_fee),
            "brand":          rec.brand,
            "updated_date":   datetime.now(),
        }
        if not vat_data_store.update_income_record(company_id, income_id, updates):
            raise RuntimeError("Income record could not be updated — check server logs")
        if is_json:
            return {"success": True, "income_id": income_id}
        flash(request, "Income record updated!", "success")
        return RedirectResponse("/vat/income", status_code=303)
    except HTTPException:
        raise
    except Exception as e:
        if is_json:
            raise HTTPException(status_code=400, detail=str(e))
        flash(request, f"Error: {e}", "error")
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
            "tender_id":      (data.get("tender_id") or "").strip(),
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


# ── Single expense / capital records: JSON detail + edit ────────────────────
# CRITICAL: registered at the END of the module so the parametric routes can
# never shadow the static /expenses/add and /capital/add routes above
# (FastAPI matches in registration order).

def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _merged_text(data: dict, row: dict, key: str, *aliases, row_key: str = None):
    """Submitted value wins (empty string clears); else keep the stored one."""
    for k in (key,) + aliases:
        if k in data:
            return str(data[k]).strip()
    return str(row.get(row_key or key) or "")


async def _request_data(request: Request):
    """(data, is_json) from a JSON or form-encoded request body."""
    is_json = "application/json" in request.headers.get("content-type", "")
    if is_json:
        return await request.json(), True
    form = await request.form()
    return dict(form), False


def _expense_row_json(row: dict) -> dict:
    """Serialize a vat_expenses DB row: dates ISO, Decimals as floats,
    enums as .name (plus a *_value alias with the display value)."""
    category = _stored_enum(ExpenseCategory, row.get("category"),
                            ExpenseCategory.OTHER_EXPENSES)
    vat_type = _stored_enum(VATType, row.get("vat_type"), VATType.STANDARD)
    return {
        "expense_id":     row.get("expense_id"),
        "company_id":     row.get("company_id"),
        "expense_date":   _iso(row.get("expense_date")),
        "description":    row.get("description") or "",
        "category":       category.name,
        "category_value": category.value,
        "vat_type":       vat_type.name,
        "vat_type_value": vat_type.value,
        "gross_amount":   _num(row.get("gross_amount") or 0),
        "vat_rate":       _num(row.get("vat_rate") or 0),
        "vat_amount":     _num(row.get("vat_amount") or 0),
        "net_amount":     _num(row.get("net_amount") or 0),
        "total_amount":   _num(row.get("net_amount") or 0),  # net = gross + VAT
        "supplier_name":  row.get("supplier_name") or "",
        "supplier_tin":   row.get("supplier_tin") or "",
        "receipt_number": row.get("receipt_number") or "",
        "tender_id":      row.get("tender_id") or "",
        "created_date":   _iso(row.get("created_date")),
        "updated_date":   _iso(row.get("updated_date")),
        "created_by":     row.get("created_by") or "",
        "is_active":      bool(row.get("is_active", True)),
    }


@router.get("/expenses/{expense_id}", name="vat_expense_detail")
async def expense_detail(expense_id: str, request: Request, user=Depends(login_required)):
    """JSON detail for one expense record (used by the list-page modals)."""
    company_id = _company(request)
    row = vat_data_store.get_expense_record(company_id, expense_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Expense record not found")
    return {"success": True, "expense": _expense_row_json(row)}


@router.post("/expenses/{expense_id}/edit", name="vat_expense_edit")
async def expense_edit(expense_id: str, request: Request, user=Depends(login_required)):
    """Update one expense record (JSON or form). vat_amount / net_amount are
    ALWAYS recomputed server-side via ExpenseRecord rules (net = gross + VAT)."""
    company_id = _company(request)
    row = vat_data_store.get_expense_record(company_id, expense_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Expense record not found")
    data, is_json = await _request_data(request)
    try:
        old_vat_type = _stored_enum(VATType, row.get("vat_type"), VATType.STANDARD)
        vat_type = _parse_enum(VATType, data.get("vat_type"), old_vat_type)
        if data.get("vat_rate"):
            vat_rate = Decimal(str(data["vat_rate"]))
        elif vat_type != old_vat_type:
            vat_rate = Decimal(_VAT_DEFAULT_RATES.get(vat_type.name, "0.15"))
        elif row.get("vat_rate") is not None:
            vat_rate = Decimal(str(row["vat_rate"]))
        else:
            vat_rate = Decimal(_VAT_DEFAULT_RATES.get(vat_type.name, "0.15"))

        # Rebuild the record so __post_init__ recomputes vat/net amounts
        rec = ExpenseRecord(
            expense_id=expense_id,
            company_id=company_id,
            expense_date=(datetime.strptime(data["expense_date"], "%Y-%m-%d").date()
                          if data.get("expense_date") else row.get("expense_date")),
            description=_merged_text(data, row, "description"),
            category=_parse_enum(ExpenseCategory, data.get("category"),
                                 _stored_enum(ExpenseCategory, row.get("category"),
                                              ExpenseCategory.OTHER_EXPENSES)),
            gross_amount=Decimal(str(data.get("gross_amount") or row.get("gross_amount") or 0)),
            vat_type=vat_type,
            vat_rate=vat_rate,
            supplier_name=_merged_text(data, row, "supplier_name", "vendor_name"),
            supplier_tin=_merged_text(data, row, "supplier_tin"),
            receipt_number=_merged_text(data, row, "receipt_number", "invoice_number"),
            tender_id=_merged_text(data, row, "tender_id"),
        )
        updates = {
            "expense_date":   rec.expense_date,
            "description":    rec.description,
            "category":       rec.category.value,
            "gross_amount":   float(rec.gross_amount),
            "vat_type":       rec.vat_type.value,
            "vat_rate":       float(rec.vat_rate),
            "vat_amount":     float(rec.vat_amount),
            "net_amount":     float(rec.net_amount),
            "supplier_name":  rec.supplier_name,
            "supplier_tin":   rec.supplier_tin,
            "receipt_number": rec.receipt_number,
            "tender_id":      rec.tender_id,
            "updated_date":   datetime.now(),
        }
        if not vat_data_store.update_expense_record(company_id, expense_id, updates):
            raise RuntimeError("Expense record could not be updated — check server logs")
        if is_json:
            return {"success": True, "expense_id": expense_id}
        flash(request, "Expense record updated!", "success")
        return RedirectResponse("/vat/expenses", status_code=303)
    except HTTPException:
        raise
    except Exception as e:
        if is_json:
            raise HTTPException(status_code=400, detail=str(e))
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/vat/expenses", status_code=303)


def _capital_row_json(row: dict) -> dict:
    """Serialize a vat_capital DB row. NOTE the column↔model mapping:
    DB investment_date ↔ model transaction_date, DB investor_name ↔ source."""
    source = row.get("investor_name") or ""
    return {
        "capital_id":         row.get("capital_id"),
        "company_id":         row.get("company_id"),
        "transaction_date":   _iso(row.get("investment_date")),
        "investment_date":    _iso(row.get("investment_date")),  # DB-name alias
        "description":        row.get("description") or "",
        "capital_type":       row.get("capital_type") or "",
        "transaction_type":   (row.get("transaction_type") or "INJECTION").upper(),
        "amount":             _num(row.get("amount") or 0),
        "source":             source,
        "source_destination": source,  # add-form field alias
        "investor_name":      source,  # DB-name alias
        "created_date":       _iso(row.get("created_date")),
        "updated_date":       _iso(row.get("updated_date")),
        "created_by":         row.get("created_by") or "",
        "is_active":          bool(row.get("is_active", True)),
    }


@router.get("/capital/{capital_id}", name="vat_capital_detail")
async def capital_detail(capital_id: str, request: Request, user=Depends(login_required)):
    """JSON detail for one capital record (used by the list-page modals)."""
    company_id = _company(request)
    row = vat_data_store.get_capital_record(company_id, capital_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Capital record not found")
    return {"success": True, "capital": _capital_row_json(row)}


@router.post("/capital/{capital_id}/edit", name="vat_capital_edit")
async def capital_edit(capital_id: str, request: Request, user=Depends(login_required)):
    """Update one capital record (JSON or form)."""
    company_id = _company(request)
    row = vat_data_store.get_capital_record(company_id, capital_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Capital record not found")
    data, is_json = await _request_data(request)
    try:
        raw_tx_type = (data.get("transaction_type")
                       or row.get("transaction_type") or "INJECTION")
        tx_type = str(raw_tx_type).strip().upper()
        if tx_type not in ("INJECTION", "WITHDRAWAL"):
            raise ValueError(f"Invalid transaction type: {raw_tx_type!r}")
        raw_date = data.get("transaction_date") or data.get("investment_date")
        # Uppercase only a *submitted* capital_type (the add-form's option
        # values are uppercase); a stored legacy value is preserved as-is.
        cap_type = _merged_text(data, row, "capital_type")
        if data.get("capital_type"):
            cap_type = cap_type.upper()
        rec = CapitalRecord(
            capital_id=capital_id,
            company_id=company_id,
            transaction_date=(datetime.strptime(raw_date, "%Y-%m-%d").date()
                              if raw_date else row.get("investment_date") or date.today()),
            description=_merged_text(data, row, "description"),
            capital_type=cap_type,
            transaction_type=tx_type,
            amount=Decimal(str(data.get("amount") or row.get("amount") or 0)),
            source=_merged_text(data, row, "source", "source_destination",
                                row_key="investor_name"),
        )
        updates = {
            "investment_date":  rec.transaction_date,  # DB column name
            "description":      rec.description,
            "capital_type":     rec.capital_type,
            "transaction_type": rec.transaction_type,
            "amount":           float(rec.amount),
            "investor_name":    rec.source,            # DB column name
            "updated_date":     datetime.now(),
        }
        if not vat_data_store.update_capital_record(company_id, capital_id, updates):
            raise RuntimeError("Capital record could not be updated — check server logs")
        if is_json:
            return {"success": True, "capital_id": capital_id}
        flash(request, "Capital record updated!", "success")
        return RedirectResponse("/vat/capital", status_code=303)
    except HTTPException:
        raise
    except Exception as e:
        if is_json:
            raise HTTPException(status_code=400, detail=str(e))
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/vat/capital", status_code=303)
