from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import tempfile
import os
from datetime import datetime

from journal_entry_data_store import JournalEntryDataStore

router = APIRouter(prefix="/journal", tags=["journal"])
journal_store = JournalEntryDataStore()


@router.get("/", name="journal_entries_journal_list")
async def journal_list(request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    start_date = request.query_params.get("start_date")
    end_date   = request.query_params.get("end_date")
    from datetime import datetime as _dt
    start_obj = _dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end_obj   = _dt.strptime(end_date,   "%Y-%m-%d").date() if end_date   else None
    df = journal_store.read_journal_entries(company_id, start_obj, end_obj)
    ctx = template_context(request)
    ctx.update(
        entries=df.to_dict("records") if not df.empty else [],
        total_entries=len(df),
        total_debits=df["total_debit"].sum() if not df.empty else 0,
        total_credits=df["total_credit"].sum() if not df.empty else 0,
        filters={"company_id": company_id, "start_date": start_date, "end_date": end_date},
    )
    return templates.TemplateResponse("journal_entries/list.html", ctx)


@router.get("/view/{entry_id}", name="journal_entries_view_entry")
async def view_entry(entry_id: str, request: Request, user=Depends(login_required)):
    df = journal_store.read_journal_entries()
    entry_df = df[df["entry_id"] == entry_id]
    if entry_df.empty:
        flash(request, "Journal entry not found", "error")
        return RedirectResponse("/journal/", status_code=302)
    lines_df = journal_store.read_entry_lines(entry_id, company_id=request.session.get('current_company_id', 'default'))
    ctx = template_context(request)
    ctx.update(entry=entry_df.iloc[0].to_dict(), lines=lines_df.to_dict("records"))
    return templates.TemplateResponse("journal_entries/view.html", ctx)


@router.get("/add", name="journal_entries_add_entry_get")
async def add_entry_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("journal_entries/add.html", template_context(request))


@router.post("/add", name="journal_entries_add_entry")
async def add_entry_post(request: Request, user=Depends(login_required)):
    try:
        data = await request.json()
        from datetime import datetime as _dt
        entry_data = {
            "company_id":       data.get("company_id", "default"),
            "entry_date":       _dt.strptime(data.get("entry_date"), "%Y-%m-%d").date(),
            "description":      data.get("description"),
            "reference_number": data.get("reference_number", ""),
        }
        lines_data, total_debit, total_credit = [], 0.0, 0.0
        for line in data.get("lines", []):
            d = float(line.get("debit_amount", 0))
            c = float(line.get("credit_amount", 0))
            lines_data.append({
                "account_code":  line.get("account_code"),
                "account_name":  line.get("account_name", ""),
                "description":   line.get("description", entry_data["description"]),
                "debit_amount":  d,
                "credit_amount": c,
            })
            total_debit  += d
            total_credit += c
        entry_data["total_debit"]  = total_debit
        entry_data["total_credit"] = total_credit
        if abs(total_debit - total_credit) > 0.01:
            return {"success": False, "error": "Debits must equal credits"}
        entry_id = journal_store.save_journal_entry(entry_data, lines_data)
        return {"success": True, "entry_id": entry_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/export/excel", name="journal_entries_export_excel")
async def export_excel(request: Request, company_id: str = None, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = journal_store.export_to_excel(company_id)
        fname = f"journal_entries_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Export failed: {e}", "error")
        return RedirectResponse("/journal/", status_code=302)


@router.get("/import/excel", name="journal_entries_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("journal_entries/import_excel.html", template_context(request))


@router.post("/import/excel", name="journal_entries_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("excel_file")
    company_id = request.session.get("current_company_id") or form.get("company_id", "default")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/journal/import/excel", status_code=303)
    if not _file.filename.lower().endswith((".xlsx", ".xls")):  # type: ignore[union-attr]
        flash(request, "Please upload a valid Excel file", "error")
        return RedirectResponse("/journal/import/excel", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = journal_store.import_from_excel(tmp_path, company_id)
        os.unlink(tmp_path)
        if result["success"]:
            flash(request, f"Imported {result['imported_count']} entries!", "success")
        else:
            flash(request, "Import failed.", "error")
    except Exception as e:
        flash(request, f"Import failed: {e}", "error")
    return RedirectResponse("/journal/", status_code=303)


@router.get("/download/sample", name="journal_entries_download_sample")
async def download_sample(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = journal_store.create_sample_excel_file()
        return _FR(filepath, filename="journal_entries_sample_data.xlsx",
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Sample download failed: {e}", "error")
        return RedirectResponse("/journal/", status_code=302)
