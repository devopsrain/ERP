from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import pandas as pd
from datetime import datetime
from cpo_data_store import CPODataStore
from siem_data_store import siem_store

router = APIRouter(prefix="/cpo", tags=["cpo"])
cpo_store = CPODataStore(data_dir="data")


@router.get("/", name="cpo_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    recent = cpo_store.get_all_cpos()[-10:]
    recent.reverse()
    history = cpo_store.get_import_history()[-5:]
    history.reverse()
    ctx.update(summary=cpo_store.get_summary(), recent_cpos=recent, import_history=history)
    return templates.TemplateResponse("cpo/dashboard.html", ctx)


@router.get("/import", name="cpo_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("cpo/import.html", template_context(request))


@router.post("/import", name="cpo_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("file")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/cpo/import", status_code=303)
    if not _file.filename.lower().endswith((".xlsx", ".xls")):  # type: ignore[union-attr]
        flash(request, "Please upload an Excel file", "error")
        return RedirectResponse("/cpo/import", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        import io
        df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        if df.empty:
            flash(request, "The file contains no data", "error")
            return RedirectResponse("/cpo/import", status_code=303)
        result = cpo_store.import_from_dataframe(df, _file.filename)
        siem_store.log_upload_event(request, module="cpo", endpoint="/cpo/import",
                                    filename=_file.filename,
                                    records_imported=result.get("imported", 0),
                                    status="success")
        ctx = template_context(request)
        ctx.update(result=result, filename=_file.filename)
        return templates.TemplateResponse("cpo/import_result.html", ctx)
    except Exception as e:
        flash(request, f"Error reading file: {e}", "error")
        return RedirectResponse("/cpo/import", status_code=303)


@router.get("/list", name="cpo_cpo_list")
async def cpo_list(request: Request, user=Depends(login_required)):
    records = cpo_store.get_all_cpos()
    records.reverse()
    ctx = template_context(request)
    ctx.update(records=records, summary=cpo_store.get_summary())
    return templates.TemplateResponse("cpo/cpo_list.html", ctx)


@router.get("/add", name="cpo_add_cpo_get")
async def add_cpo_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("cpo/add_cpo.html", {**template_context(request), "record": {}})


@router.post("/add", name="cpo_add_cpo")
async def add_cpo_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    is_returned   = form.get("is_returned", "No")
    returned_date = form.get("returned_date", "").strip()
    if is_returned == "Yes" and not returned_date:
        flash(request, "Returned date is required when CPO is marked as returned", "error")
        return templates.TemplateResponse("cpo/add_cpo.html",
                                          {**template_context(request), "record": dict(form)})
    import uuid as _uuid
    record = {
        "id":            str(_uuid.uuid4()),
        "name":          form.get("name", "").strip(),
        "date":          form.get("date", datetime.now().strftime("%Y-%m-%d")),
        "amount":        float(form.get("amount", 0) or 0),
        "bid_name":      form.get("bid_name", "").strip(),
        "is_returned":   is_returned,
        "returned_date": returned_date if is_returned == "Yes" else "",
    }
    if not record["name"]:
        flash(request, "Name is required", "error")
        return templates.TemplateResponse("cpo/add_cpo.html", {**template_context(request), "record": record})
    if cpo_store.save_cpo(record):
        # Mirror a manually entered CPO into the Bid Tracker — but only when
        # the user supplied a bid name. If a bid already exists with that
        # reference number or title (same company scope), link to it by
        # appending a note instead of creating a duplicate. Existing CPO rows
        # are untouched — this only fires for new entries from this form
        # (not edits, not Excel imports). Bid logic must never fail the CPO save.
        message, category = "CPO record added!", "success"
        try:
            bid_name = record["bid_name"]
            if bid_name:
                from bid_data_store import bid_store
                existing = bid_store.find_bid_by_ref_or_title(None, bid_name)
                if existing:
                    bid_store.append_bid_note(
                        existing["id"], None,
                        (f"\n[CPO] Linked CPO {record['id']} — "
                         f"payee {record['name']}, ETB {record['amount']:,.2f}, "
                         f"{record['date']}"))
                    message = (f"CPO record added! CPO linked to existing bid "
                               f"'{existing.get('title', '')}'.")
                else:
                    bid_id = bid_store.save_bid({
                        "title":            bid_name,
                        "reference_number": f"CPO-{record['id'][:8].upper()}",
                        "organization":     record["name"],
                        "category":         "CPO",
                        "status":           "open",
                        "submission_date":  record["date"],
                        "bid_amount":       record["amount"],
                        "currency":         "ETB",
                        "notes": (f"Auto-created from CPO entry {record['id']} — "
                                  f"payee: {record['name']}, date: {record['date']}, "
                                  f"amount: ETB {record['amount']:,.2f}"),
                    })
                    if bid_id:
                        message = "CPO record added! A linked bid was created in the Bid Tracker."
                    else:
                        message = ("CPO saved, but the linked bid could not be "
                                   "created — check server logs.")
                        category = "warning"
        except Exception as bid_err:
            logger.warning("CPO→Bid mirror failed: %s", bid_err)
            message = "CPO saved, but bid linking failed — check server logs."
            category = "warning"
        flash(request, message, category)
        return RedirectResponse("/cpo/list", status_code=303)
    flash(request, "Error saving CPO record", "error")
    return templates.TemplateResponse("cpo/add_cpo.html", {**template_context(request), "record": record})


@router.get("/edit/{cpo_id}", name="cpo_edit_cpo_get")
async def edit_cpo_get(cpo_id: str, request: Request, user=Depends(login_required)):
    record = cpo_store.get_cpo_by_id(cpo_id)
    if not record:
        flash(request, "CPO record not found", "error")
        return RedirectResponse("/cpo/list", status_code=302)
    return templates.TemplateResponse("cpo/edit_cpo.html", {**template_context(request), "record": record})


@router.post("/edit/{cpo_id}", name="cpo_edit_cpo")
async def edit_cpo_post(cpo_id: str, request: Request, user=Depends(login_required)):
    record = cpo_store.get_cpo_by_id(cpo_id)
    if not record:
        flash(request, "CPO record not found", "error")
        return RedirectResponse("/cpo/list", status_code=302)
    form = await request.form()
    is_returned   = form.get("is_returned", "No")
    returned_date = form.get("returned_date", "").strip()
    if is_returned == "Yes" and not returned_date:
        flash(request, "Returned date is required", "error")
        return templates.TemplateResponse("cpo/edit_cpo.html", {**template_context(request), "record": record})
    updates = {
        "name":          form.get("name", "").strip(),
        "date":          form.get("date", ""),
        "amount":        float(form.get("amount", 0) or 0),
        "bid_name":      form.get("bid_name", "").strip(),
        "is_returned":   is_returned,
        "returned_date": returned_date if is_returned == "Yes" else "",
    }
    if cpo_store.update_cpo(cpo_id, updates):
        flash(request, "CPO updated!", "success")
        return RedirectResponse("/cpo/list", status_code=303)
    flash(request, "Error updating CPO", "error")
    record.update(updates)
    return templates.TemplateResponse("cpo/edit_cpo.html", {**template_context(request), "record": record})


@router.post("/delete/{cpo_id}", name="cpo_delete_cpo")
async def delete_cpo(cpo_id: str, request: Request, user=Depends(login_required)):
    cpo_store.delete_cpo(cpo_id)
    flash(request, "CPO deleted", "success")
    return RedirectResponse("/cpo/list", status_code=303)


@router.get("/export", name="cpo_export_excel")
async def export_excel(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    filepath = cpo_store.export_to_excel()
    if filepath:
        fname = f"cpo_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    flash(request, "Export failed", "error")
    return RedirectResponse("/cpo/list", status_code=302)

# ── Excel template download for CPO import ─────────────────────────────────
@router.get("/download-template", name="cpo_download_template")
async def download_template(request: Request, user=Depends(login_required)):
    import pandas as pd, tempfile, os
    from fastapi.responses import FileResponse as _FR
    df = pd.DataFrame({
        "date": ["2024-01-15"],
        "cpo_number": ["CPO-001"],
        "description": ["Sample CPO"],
        "amount": [10000.00],
        "vendor": ["Vendor Co."],
        "status": ["pending"],
    })
    fd, path = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
    df.to_excel(path, index=False)
    return _FR(path, filename="cpo_template.xlsx",
               media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
