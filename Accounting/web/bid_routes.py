from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required, validate_upload
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import os
from datetime import date, datetime, timedelta
from bid_data_store import bid_store, CONFIDENTIAL_DOC_TYPE

ALLOWED_EXT = {
    "pdf","doc","docx","xls","xlsx","ppt","pptx",
    "txt","csv","zip","rar","7z","jpg","jpeg","png","gif","bmp","svg",
}

def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

router = APIRouter(prefix="/bid", tags=["bid"])

DOC_TYPES = ["original_bid", "technical", "financial", "supporting", "other"]


def _is_admin(request: Request) -> bool:
    """Admin / super_admin session — the only users who may see supplier
    confidential documents (they carry supplier price information)."""
    try:
        from auth_data_store import PRIVILEGE_LEVELS
        lvl = PRIVILEGE_LEVELS.get(request.session.get("privilege_level", "viewer"), 0)
        return lvl >= PRIVILEGE_LEVELS.get("admin", 50)
    except Exception:
        return request.session.get("privilege_level") in ("admin", "super_admin")


def _deny(request: Request, bid_id: str):
    flash(request, "Supplier confidential documents are visible to administrators only", "error")
    return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)


def _doc_is_confidential(doc_id: str) -> bool:
    meta = bid_store.get_document_meta(doc_id) or {}
    return (meta.get("doc_type") or "") == CONFIDENTIAL_DOC_TYPE


def _results_summary(bid: dict, results: list) -> dict:
    """Where our own bid lands among the recorded competitor prices."""
    try:
        ours = float(bid.get("bid_amount") or 0)
    except (TypeError, ValueError):
        ours = 0.0
    prices = [r["price"] for r in results if r.get("price")]
    summary = {"count": len(results), "ours": ours, "position": None,
               "lowest": min(prices) if prices else None,
               "highest": max(prices) if prices else None,
               "winner": next((r for r in results if r.get("is_winner")), None)}
    if ours > 0:
        others = [p for p in prices]
        summary["position"] = 1 + sum(1 for p in others if p < ours)
        summary["total_with_ours"] = len(others) + (0 if any(r.get("is_ours") for r in results) else 1)
    return summary

# Statuses for which we stop flagging a missed delivery date
_CLOSED_STATUSES = {"won", "lost", "cancelled", "closed"}


def _with_delivery_due(bid: dict) -> dict:
    """Attach due_date (contract_date + delivery_days) and overdue flag."""
    bid["due_date"] = None
    bid["overdue"] = False
    cd = bid.get("contract_date")
    try:
        days = int(bid.get("delivery_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if not cd or days <= 0:
        return bid
    if isinstance(cd, str):
        try:
            cd = datetime.strptime(cd[:10], "%Y-%m-%d").date()
        except ValueError:
            return bid
    elif isinstance(cd, datetime):
        cd = cd.date()
    bid["due_date"] = cd + timedelta(days=days)
    bid["overdue"] = (bid["due_date"] < date.today()
                      and (bid.get("status") or "").lower() not in _CLOSED_STATUSES)
    return bid


@router.get("/", name="bid_dashboard")
@router.get("/dashboard", name="bid_dashboard_alt")
async def dashboard(request: Request, user=Depends(login_required)):
    stats = bid_store.get_summary_stats()
    bids  = bid_store.get_all_bids()
    bids.reverse()
    bids = [_with_delivery_due(b) for b in bids]
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
        "case_handler_email","notes","contract_date",
    ]}
    data["status"]     = data.get("status") or "Draft"
    data["bid_amount"] = float(form.get("bid_amount", 0) or 0)
    data["reminder_days_before"] = int(form.get("reminder_days_before", 3) or 3)
    data["delivery_days"] = max(0, int(form.get("delivery_days", 0) or 0))
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
    bid = _with_delivery_due(bid)
    is_admin = _is_admin(request)
    doc_groups = {t: [] for t in DOC_TYPES}
    if is_admin:
        doc_groups[CONFIDENTIAL_DOC_TYPE] = []
    for doc in bid.get("documents", []):
        dt = doc.get("doc_type", "other")
        if dt == CONFIDENTIAL_DOC_TYPE and not is_admin:
            continue  # never even list confidential supplier files to non-admins
        doc_groups.get(dt, doc_groups["other"]).append(doc)
    results = bid_store.get_results(bid_id)
    ctx = template_context(request)
    ctx.update(bid=bid, doc_groups=doc_groups, is_admin=is_admin,
               confidential_type=CONFIDENTIAL_DOC_TYPE,
               results=results, results_summary=_results_summary(bid, results))
    return templates.TemplateResponse("bid/view_bid.html", ctx)


# ── Bid results (opening record: bidder + price) ──────────────────────────
@router.post("/results/{bid_id}", name="bid_add_result")
async def add_result(bid_id: str, request: Request, user=Depends(login_required)):
    bid = bid_store.get_bid_by_id(bid_id)
    if not bid:
        flash(request, "Bid not found", "error")
        return RedirectResponse("/bid/", status_code=302)
    form = await request.form()
    data = {
        "bidder_name": form.get("bidder_name", ""),
        "price": form.get("price", ""),
        "currency": form.get("currency") or bid.get("currency") or "ETB",
        "technical_score": form.get("technical_score", ""),
        "notes": form.get("notes", ""),
        "is_winner": form.get("is_winner") in ("on", "1", "true"),
        "is_ours": form.get("is_ours") in ("on", "1", "true"),
        "recorded_by": request.session.get("username", ""),
    }
    if not data["bidder_name"].strip():
        flash(request, "Bidder name is required", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    rid = bid_store.add_result(bid_id, "default", data)
    flash(request, "Bid result recorded" if rid else "Error recording bid result",
          "success" if rid else "error")
    return RedirectResponse(f"/bid/view/{bid_id}#results", status_code=303)


@router.post("/results/{bid_id}/{result_id}/winner", name="bid_result_winner")
async def result_winner(bid_id: str, result_id: str, request: Request, user=Depends(login_required)):
    ok = bid_store.set_result_winner(bid_id, result_id)
    flash(request, "Winner marked" if ok else "Could not update result", "success" if ok else "error")
    return RedirectResponse(f"/bid/view/{bid_id}#results", status_code=303)


@router.post("/results/{bid_id}/{result_id}/delete", name="bid_result_delete")
async def result_delete(bid_id: str, result_id: str, request: Request, user=Depends(login_required)):
    ok = bid_store.delete_result(bid_id, result_id)
    flash(request, "Result removed" if ok else "Could not remove result", "success" if ok else "error")
    return RedirectResponse(f"/bid/view/{bid_id}#results", status_code=303)


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
        "case_handler_name","case_handler_email","notes","contract_date",
    ]})
    bid["bid_amount"] = float(form.get("bid_amount", 0) or 0)
    bid["reminder_days_before"] = int(form.get("reminder_days_before", 3) or 3)
    bid["delivery_days"] = max(0, int(form.get("delivery_days", 0) or 0))
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
    doc_type = (form.get("doc_type") or "other").strip()
    if doc_type == CONFIDENTIAL_DOC_TYPE and not _is_admin(request):
        return _deny(request, bid_id)
    if doc_type not in DOC_TYPES + [CONFIDENTIAL_DOC_TYPE]:
        doc_type = "other"
    _file = form.get("file")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    # AICC 6.3: whitelist check + dangerous-extension block + empty-file check
    _content = await _file.read()  # type: ignore[union-attr]
    await _file.seek(0)            # type: ignore[union-attr]  # rewind for save_document
    ok, upload_error = validate_upload(_file.filename, _content, allowed_exts=ALLOWED_EXT)  # type: ignore[union-attr]
    if not ok:
        flash(request, upload_error, "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    doc_id = bid_store.save_document(
        bid_id, _file,  # type: ignore[arg-type]
        doc_type,
        form.get("description", "").strip(),
        form.get("uploaded_by", "").strip() or request.session.get("username", ""),
    )
    if doc_id:
        flash(request, "Document uploaded!", "success")
    else:
        flash(request, "Error uploading document", "error")
    return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)


@router.get("/download/{bid_id}/{doc_id}", name="bid_download_document")
async def download_document(bid_id: str, doc_id: str, request: Request, user=Depends(login_required)):
    if _doc_is_confidential(doc_id) and not _is_admin(request):
        return _deny(request, bid_id)
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

# ── Delete / preview / test-email stubs ────────────────────────────────────
@router.post("/delete/{bid_id}", name="bid_delete_bid")
async def delete_bid(bid_id: str, request: Request, user=Depends(login_required)):
    # Every other bid_store call in this module relies on the store's
    # hardcoded 'default' tenant (no session lookup); delete must target the
    # same tenant or bids visible in the list can never be deleted.
    ok = bid_store.delete_bid(bid_id)
    flash(request, "Bid deleted" if ok else "Delete failed", "success" if ok else "error")
    return RedirectResponse("/bid/", status_code=303)


@router.post("/delete/{bid_id}/{doc_id}", name="bid_delete_document")
async def delete_document(bid_id: str, doc_id: str, request: Request, user=Depends(login_required)):
    if _doc_is_confidential(doc_id) and not _is_admin(request):
        return _deny(request, bid_id)
    ok = bid_store.delete_document(doc_id)
    flash(request, "Document deleted" if ok else "Delete failed", "success" if ok else "error")
    return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)


@router.get("/preview/{bid_id}/{doc_id}", name="bid_preview_document")
async def preview_document(bid_id: str, doc_id: str, request: Request, user=Depends(login_required)):
    if _doc_is_confidential(doc_id) and not _is_admin(request):
        return _deny(request, bid_id)
    presigned = bid_store.get_presigned_url(bid_id, doc_id)
    if presigned:
        return RedirectResponse(presigned, status_code=302)
    path = bid_store.get_document_path(bid_id, doc_id)
    if not path:
        flash(request, "Document not found", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=302)
    meta = bid_store.get_document_meta(doc_id) or {}
    name = meta.get("original_filename", "document")
    return FileResponse(path, filename=name, headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.post("/test-email", name="bid_test_email")
async def test_email(request: Request, user=Depends(login_required)):
    flash(request, "Test email queued (delivery feature pending)", "info")
    referer = request.headers.get("referer") or "/bid/"
    return RedirectResponse(referer, status_code=303)
