from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import os
from bid_data_store import bid_store

ALLOWED_EXT = {
    "pdf","doc","docx","xls","xlsx","ppt","pptx",
    "txt","csv","zip","rar","7z","jpg","jpeg","png","gif","bmp","svg",
}

def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

router = APIRouter(prefix="/bid", tags=["bid"])


@router.get("/", name="bid_dashboard")
@router.get("/dashboard", name="bid_dashboard_alt")
async def dashboard(request: Request, user=Depends(login_required)):
    stats = bid_store.get_summary_stats()
    bids  = bid_store.get_all_bids()
    bids.reverse()
    ctx = template_context(request)
    ctx.update(stats=stats, bids=bids)
    return templates.TemplateResponse("bid/dashboard.html", ctx)


@router.get("/add", name="bid_add_bid_get")
async def add_bid_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("bid/add_bid.html", {**template_context(request), "bid": {}})


@router.post("/add", name="bid_add_bid")
async def add_bid_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {k: form.get(k, "").strip() for k in [
        "title","reference_number","organization","description",
        "category","status","deadline","currency","case_handler_name",
        "case_handler_email","notes",
    ]}
    data["status"]     = data.get("status") or "Draft"
    data["bid_amount"] = float(form.get("bid_amount", 0) or 0)
    data["reminder_days_before"] = int(form.get("reminder_days_before", 3) or 3)
    if not data["title"]:
        flash(request, "Bid title is required", "error")
        return templates.TemplateResponse("bid/add_bid.html", {**template_context(request), "bid": data})
    bid_id = bid_store.save_bid(data)
    if bid_id:
        flash(request, "Bid created successfully!", "success")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    flash(request, "Error creating bid", "error")
    return templates.TemplateResponse("bid/add_bid.html", {**template_context(request), "bid": data})


@router.get("/view/{bid_id}", name="bid_view_bid")
async def view_bid(bid_id: str, request: Request, user=Depends(login_required)):
    bid = bid_store.get_bid_by_id(bid_id)
    if not bid:
        flash(request, "Bid not found", "error")
        return RedirectResponse("/bid/", status_code=302)
    doc_groups = {t: [] for t in ["original_bid","technical","financial","supporting","other"]}
    for doc in bid.get("documents", []):
        dt = doc.get("doc_type", "other")
        doc_groups.get(dt, doc_groups["other"]).append(doc)
    ctx = template_context(request)
    ctx.update(bid=bid, doc_groups=doc_groups)
    return templates.TemplateResponse("bid/view_bid.html", ctx)


@router.get("/edit/{bid_id}", name="bid_edit_bid_get")
async def edit_bid_get(bid_id: str, request: Request, user=Depends(login_required)):
    bid = bid_store.get_bid_by_id(bid_id)
    if not bid:
        flash(request, "Bid not found", "error")
        return RedirectResponse("/bid/", status_code=302)
    return templates.TemplateResponse("bid/edit_bid.html", {**template_context(request), "bid": bid})


@router.post("/edit/{bid_id}", name="bid_edit_bid")
async def edit_bid_post(bid_id: str, request: Request, user=Depends(login_required)):
    bid = bid_store.get_bid_by_id(bid_id)
    if not bid:
        flash(request, "Bid not found", "error")
        return RedirectResponse("/bid/", status_code=302)
    form = await request.form()
    bid.update({k: form.get(k, "").strip() for k in [
        "title","reference_number","organization","description","category",
        "status","deadline","submission_date","currency",
        "case_handler_name","case_handler_email","notes",
    ]})
    bid["bid_amount"] = float(form.get("bid_amount", 0) or 0)
    bid["reminder_days_before"] = int(form.get("reminder_days_before", 3) or 3)
    if bid_store.save_bid(bid):
        flash(request, "Bid updated!", "success")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    flash(request, "Error updating bid", "error")
    return templates.TemplateResponse("bid/edit_bid.html", {**template_context(request), "bid": bid})


@router.post("/upload/{bid_id}", name="bid_upload_document")
async def upload_document(bid_id: str, request: Request, user=Depends(login_required)):
    bid = bid_store.get_bid_by_id(bid_id)
    if not bid:
        flash(request, "Bid not found", "error")
        return RedirectResponse("/bid/", status_code=302)
    form  = await request.form()
    _file = form.get("file")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    if not _allowed(_file.filename):  # type: ignore[union-attr]
        flash(request, "File type not allowed", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    doc_id = bid_store.save_document(
        bid_id, _file,  # type: ignore[arg-type]
        form.get("doc_type", "other"),
        form.get("description", "").strip(),
        form.get("uploaded_by", "").strip(),
    )
    if doc_id:
        flash(request, f"Document uploaded!", "success")
    else:
        flash(request, "Error uploading document", "error")
    return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)


@router.get("/download/{bid_id}/{doc_id}", name="bid_download_document")
async def download_document(bid_id: str, doc_id: str, request: Request, user=Depends(login_required)):
    presigned = bid_store.get_presigned_url(bid_id, doc_id)
    if presigned:
        return RedirectResponse(presigned, status_code=302)
    path = bid_store.get_document_path(bid_id, doc_id)
    if not path:
        flash(request, "Document not found", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=302)
    meta = bid_store.get_document_meta(doc_id)
    name = meta.get("original_filename", "document") if meta else "document"
    return FileResponse(path, filename=name)
