from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

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


@router.get("/import-history", name="transaction_import_history")
async def import_history(request: Request, user=Depends(login_required)):
    history = transaction_store.get_import_history()
    ctx = template_context(request)
    ctx.update(history=history)
    return templates.TemplateResponse("transaction/import_history.html", ctx)


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
