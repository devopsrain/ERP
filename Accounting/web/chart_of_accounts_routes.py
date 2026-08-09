from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required, current_company
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import tempfile
import os
from datetime import datetime

from chart_of_accounts_data_store import ChartOfAccountsDataStore

router = APIRouter(prefix="/accounts", tags=["accounts"])
accounts_store = ChartOfAccountsDataStore()


@router.get("/", name="accounts_accounts_list")
async def accounts_list(request: Request, user=Depends(login_required)):
    company_id   = request.query_params.get("company_id") or current_company(request)
    account_type = request.query_params.get("account_type")
    df = accounts_store.read_all_accounts(company_id)
    if account_type and not df.empty:
        df = df[df["account_type"] == account_type]
    accounts_by_type, type_totals = {}, {}
    if not df.empty:
        for at in df["account_type"].unique():
            subset = df[df["account_type"] == at]
            accounts_by_type[at] = subset.to_dict("records")
            type_totals[at] = float(subset["current_balance"].sum())
    ctx = template_context(request)
    ctx.update(accounts_by_type=accounts_by_type, type_totals=type_totals,
               total_accounts=len(df), filters={"company_id": company_id, "account_type": account_type})
    return templates.TemplateResponse("accounts/list.html", ctx)


@router.get("/dashboard", name="accounts_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id") or current_company(request)
    df = accounts_store.read_all_accounts(company_id)
    stats = {
        "total_accounts":    len(df),
        "active_accounts":   len(df[df["is_active"]]) if not df.empty else 0,
        "asset_accounts":    len(df[df["account_type"] == "Asset"]) if not df.empty else 0,
        "liability_accounts":len(df[df["account_type"] == "Liability"]) if not df.empty else 0,
        "equity_accounts":   len(df[df["account_type"] == "Equity"]) if not df.empty else 0,
        "revenue_accounts":  len(df[df["account_type"] == "Revenue"]) if not df.empty else 0,
        "expense_accounts":  len(df[df["account_type"] == "Expense"]) if not df.empty else 0,
    }
    balance_summary = {}
    for at in ["Asset", "Liability", "Equity", "Revenue", "Expense"]:
        subset = df[df["account_type"] == at] if not df.empty else df
        balance_summary[at] = float(subset["current_balance"].sum()) if not subset.empty else 0
    recent = df.sort_values("created_date", ascending=False).head(10).to_dict("records") if not df.empty else []
    ctx = template_context(request)
    ctx.update(stats=stats, balance_summary=balance_summary, recent_accounts=recent, company_id=company_id)
    return templates.TemplateResponse("accounts/dashboard.html", ctx)


@router.get("/view/{account_code}", name="accounts_view_account")
async def view_account(account_code: str, request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id") or current_company(request)
    account = accounts_store.get_account_by_code(account_code, company_id)
    if not account:
        flash(request, "Account not found", "error")
        return RedirectResponse("/accounts/", status_code=302)
    return templates.TemplateResponse("accounts/view.html", {**template_context(request), "account": account})


@router.get("/add", name="accounts_add_account_get")
async def add_account_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("accounts/add.html", template_context(request))


@router.post("/add", name="accounts_add_account")
async def add_account_post(request: Request, user=Depends(login_required)):
    data = await request.json()
    account_data = {
        "account_code":    data.get("account_code"),
        "account_name":    data.get("account_name"),
        "account_type":    data.get("account_type"),
        "account_subtype": data.get("account_subtype", ""),
        "parent_account":  data.get("parent_account", ""),
        "description":     data.get("description", ""),
        "normal_balance":  data.get("normal_balance", "Debit"),
        "current_balance": float(data.get("current_balance", 0)),
        "company_id":      data.get("company_id") or current_company(request),
        "is_active":       True,
    }
    if accounts_store.save_account(account_data):
        return {"success": True, "account_code": account_data["account_code"]}
    raise HTTPException(status_code=400, detail="Failed to save account")


@router.get("/edit/{account_code}", name="accounts_edit_account_get")
async def edit_account_get(account_code: str, request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id") or current_company(request)
    account = accounts_store.get_account_by_code(account_code, company_id)
    if not account:
        flash(request, "Account not found", "error")
        return RedirectResponse("/accounts/", status_code=302)
    return templates.TemplateResponse("accounts/edit.html", {**template_context(request), "account": account})


@router.post("/edit/{account_code}", name="accounts_edit_account")
async def edit_account_post(account_code: str, request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id") or current_company(request)
    account = accounts_store.get_account_by_code(account_code, company_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    data = await request.json()
    account.update({
        "account_name":    data.get("account_name", account["account_name"]),
        "account_type":    data.get("account_type", account["account_type"]),
        "current_balance": float(data.get("current_balance", account["current_balance"])),
    })
    if accounts_store.save_account(account):
        return {"success": True}
    raise HTTPException(status_code=400, detail="Failed to update account")


@router.get("/export/excel", name="accounts_export_excel")
async def export_excel(request: Request, company_id: str = None, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = accounts_store.export_to_excel(company_id)
        fname = f"chart_of_accounts_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Export failed: {e}", "error")
        return RedirectResponse("/accounts/", status_code=302)


@router.get("/import/excel", name="accounts_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("accounts/import_excel.html", template_context(request))


@router.post("/import/excel", name="accounts_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("excel_file")
    company_id = request.session.get("current_company_id") or form.get("company_id", "default")
    if not _file or not getattr(_file, "filename", None):  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/accounts/import/excel", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = accounts_store.import_from_excel(tmp_path, company_id)
        os.unlink(tmp_path)
        if result["success"]:
            flash(request, f"Imported {result['imported_count']} accounts!", "success")
        else:
            flash(request, "Import failed.", "error")
    except Exception as e:
        flash(request, f"Import failed: {e}", "error")
    return RedirectResponse("/accounts/", status_code=303)


@router.get("/trial-balance", name="accounts_trial_balance")
async def trial_balance(request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id") or current_company(request)
    df = accounts_store.read_all_accounts(company_id)
    ctx = template_context(request)
    if df.empty:
        ctx.update(accounts=[], totals={"debit": 0, "credit": 0})
        return templates.TemplateResponse("accounts/trial_balance.html", ctx)
    active = df[df["is_active"] & (df["current_balance"] != 0)]
    tb_accounts, total_debits, total_credits = [], 0.0, 0.0
    for _, row in active.iterrows():
        balance = float(row["current_balance"])
        if row["normal_balance"] == "Debit":
            d = max(balance, 0); c = max(-balance, 0)
        else:
            c = max(balance, 0); d = max(-balance, 0)
        tb_accounts.append({**row.to_dict(), "debit_balance": d, "credit_balance": c})
        total_debits  += d
        total_credits += c
    ctx.update(accounts=tb_accounts, totals={"debit": total_debits, "credit": total_credits})
    return templates.TemplateResponse("accounts/trial_balance.html", ctx)


@router.get("/download/sample", name="accounts_download_sample")
async def download_sample(request: Request, user=Depends(login_required)):
    """Download a sample Excel template for chart of accounts import."""
    import pandas as pd
    from fastapi.responses import FileResponse as _FR
    
    sample_data = {
        'account_code': ['1000', '1100', '1200', '2000', '3000', '4000', '5000'],
        'account_name': ['Assets', 'Cash', 'Accounts Receivable', 'Liabilities', 'Equity', 'Revenue', 'Expenses'],
        'account_type': ['Asset', 'Asset', 'Asset', 'Liability', 'Equity', 'Revenue', 'Expense'],
        'account_subtype': ['', 'Current Assets', 'Current Assets', '', '', '', ''],
        'description': ['Main asset category', 'Cash and equivalents', 'Amounts due from customers', 
                       'Main liability category', 'Owner equity', 'Revenue accounts', 'Expense accounts'],
        'normal_balance': ['Debit', 'Debit', 'Debit', 'Credit', 'Credit', 'Credit', 'Debit'],
        'current_balance': [0, 0, 0, 0, 0, 0, 0],
    }
    
    df = pd.DataFrame(sample_data)
    fd, filepath = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    df.to_excel(filepath, index=False, sheet_name='Chart of Accounts')
    
    return _FR(filepath, filename="chart_of_accounts_template.xlsx",
               media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
