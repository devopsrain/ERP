"""
Letter & E-Signature Routes

Endpoints:
  GET  /letters/                   - dashboard / inbox
  GET  /letters/compose            - compose new letter
  POST /letters/compose            - save draft
  GET  /letters/upload             - upload a ready-made letter (.docx/.pdf)
  POST /letters/upload             - save uploaded letter
  GET  /letters/<id>               - view a letter
  POST /letters/<id>/sign          - apply e-signature (PM / FM / MD)
  POST /letters/<id>/send          - mark as sent
  POST /letters/<id>/delete        - delete draft
  GET  /letters/<id>/download      - download .docx (composed letters)
  GET  /letters/<id>/download-original - download the uploaded original file
  GET  /letters/tracker            - full mail tracker
  GET  /letters/signatures         - manage stored signatures (admin)
  POST /letters/signatures/save    - upload/save a signature canvas PNG
  POST /letters/signatures/delete  - delete a stored signature
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, login_required, admin_required, validate_upload, current_company
from template_engine import templates
import logging

logger = logging.getLogger(__name__)

import letter_data_store as _ds
from letter_docx import generate_letter_docx

router = APIRouter(prefix="/letters", tags=["letters"])


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", name="letters_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    letters  = _ds.get_all_letters()
    sigs     = _ds.get_all_signatures()
    ctx      = template_context(request)
    ctx.update(letters=letters, signatures=sigs, signatories=_ds.SIGNATORIES)
    return templates.TemplateResponse("letters/dashboard.html", ctx)


# ── Compose ──────────────────────────────────────────────────────────────────

@router.get("/compose", name="letters_compose_get")
async def compose_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(letter={}, signatories=_ds.SIGNATORIES)
    return templates.TemplateResponse("letters/compose.html", ctx)


@router.post("/compose", name="letters_compose_post")
async def compose_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {
        "to":         form.get("to", "").strip(),
        "to_address": form.get("to_address", "").strip(),
        "subject":    form.get("subject", "").strip(),
        "body":       form.get("body", "").strip(),
        "cc":         form.get("cc", "").strip(),
        "date":       form.get("date", "").strip(),
        "company_id": current_company(request),
    }
    if not data["to"] or not data["subject"] or not data["body"]:
        flash(request, "To, Subject and Body are required.", "error")
        ctx = template_context(request)
        ctx.update(letter=data, signatories=_ds.SIGNATORIES)
        return templates.TemplateResponse("letters/compose.html", ctx)

    letter = _ds.create_letter(data, created_by=request.session.get("username", ""))
    flash(request, f"Letter {letter['ref_number']} created.", "success")
    return RedirectResponse(f"/letters/{letter['letter_id']}", status_code=303)


# ── Upload ready-made letter ─────────────────────────────────────────────────
# NOTE: static /upload paths must be declared BEFORE the /{letter_id} routes.

_UPLOAD_ALLOWED_EXTS = {".docx", ".pdf"}


@router.get("/upload", name="letters_upload_get")
async def upload_get(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(letter={})
    return templates.TemplateResponse("letters/upload.html", ctx)


@router.post("/upload", name="letters_upload_post")
async def upload_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {
        "to":         (form.get("to") or "").strip(),
        "subject":    (form.get("subject") or "").strip(),
        "category":   (form.get("category") or "").strip(),
        "date":       (form.get("date") or "").strip(),
        "company_id": current_company(request),
    }
    upload = form.get("file")

    def _reshow():
        ctx = template_context(request)
        ctx.update(letter=data)
        return templates.TemplateResponse("letters/upload.html", ctx)

    if not data["subject"]:
        flash(request, "Subject is required.", "error")
        return _reshow()

    filename = getattr(upload, "filename", "") or ""
    content = await upload.read() if hasattr(upload, "read") else None
    ok, err = validate_upload(filename, content, allowed_exts=_UPLOAD_ALLOWED_EXTS)
    if not ok:
        flash(request, err, "error")
        return _reshow()

    ext = Path(filename).suffix.lower()
    stored_filename = f"{uuid.uuid4()}{ext}"
    try:
        (_ds.UPLOADS_DIR / stored_filename).write_bytes(content)
    except Exception as e:
        logger.error("letters upload: could not store file: %s", e)
        flash(request, "Could not store the uploaded file. Check server logs.", "error")
        return _reshow()

    data.update(stored_filename=stored_filename,
                original_filename=Path(filename.replace("\\", "/")).name)
    letter = _ds.create_uploaded_letter(data, created_by=request.session.get("username", ""))
    flash(request, f"Letter {letter['ref_number']} uploaded.", "success")
    return RedirectResponse(f"/letters/{letter['letter_id']}", status_code=303)


# ── View ──────────────────────────────────────────────────────────────────────

@router.get("/{letter_id}", name="letters_view")
async def view_letter(letter_id: str, request: Request, user=Depends(login_required)):
    letter = _ds.get_letter_by_id(letter_id)
    if not letter:
        flash(request, "Letter not found.", "error")
        return RedirectResponse("/letters/", status_code=302)
    sigs    = _ds.get_all_signatures()
    tracker = _ds.get_tracker(letter_id)
    ctx     = template_context(request)
    ctx.update(letter=letter, signatures=sigs, tracker=tracker,
               signatories=_ds.SIGNATORIES)
    return templates.TemplateResponse("letters/view.html", ctx)


# ── Sign ──────────────────────────────────────────────────────────────────────

@router.post("/{letter_id}/sign", name="letters_sign")
async def sign_letter(letter_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    role = form.get("role", "").strip().upper()
    if role not in _ds.SIGNATORIES:
        flash(request, f"Invalid signatory role '{role}'.", "error")
        return RedirectResponse(f"/letters/{letter_id}", status_code=303)
    result = _ds.sign_letter(letter_id, role, signed_by=request.session.get("username", ""))
    if result:
        flash(request, f"Letter signed by {role}.", "success")
    else:
        flash(request, "Could not sign letter.", "error")
    return RedirectResponse(f"/letters/{letter_id}", status_code=303)


# ── Send ─────────────────────────────────────────────────────────────────────

@router.post("/{letter_id}/send", name="letters_send")
async def send_letter(letter_id: str, request: Request, user=Depends(login_required)):
    result = _ds.mark_sent(letter_id, sent_by=request.session.get("username", ""))
    if result:
        flash(request, f"Letter {result['ref_number']} marked as sent.", "success")
    else:
        flash(request, "Letter not found.", "error")
    return RedirectResponse(f"/letters/{letter_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{letter_id}/delete", name="letters_delete")
async def delete_letter(letter_id: str, request: Request, user=Depends(login_required)):
    letter = _ds.get_letter_by_id(letter_id)
    if letter and letter.get("status") != "draft":
        flash(request, "Only draft letters can be deleted.", "error")
        return RedirectResponse(f"/letters/{letter_id}", status_code=303)
    _ds.delete_letter(letter_id)
    flash(request, "Letter deleted.", "info")
    return RedirectResponse("/letters/", status_code=303)


# ── Download DOCX ─────────────────────────────────────────────────────────────

@router.get("/{letter_id}/download", name="letters_download")
async def download_letter(letter_id: str, request: Request, user=Depends(login_required)):
    letter = _ds.get_letter_by_id(letter_id)
    if not letter:
        flash(request, "Letter not found.", "error")
        return RedirectResponse("/letters/", status_code=302)
    if letter.get("source") == "uploaded":
        # Uploaded letters have no composed body — serve the original file
        return RedirectResponse(f"/letters/{letter_id}/download-original", status_code=302)
    sigs    = _ds.get_all_signatures()
    out     = generate_letter_docx(letter, sigs)
    if not out:
        flash(request, "Could not generate document. Check server logs.", "error")
        return RedirectResponse(f"/letters/{letter_id}", status_code=302)
    fname = f"{letter.get('ref_number', letter_id)}.docx"
    return FileResponse(
        out,
        filename=fname,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ── Download uploaded original ───────────────────────────────────────────────

_ORIGINAL_MEDIA_TYPES = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.get("/{letter_id}/download-original", name="letters_download_original")
async def download_original(letter_id: str, request: Request, user=Depends(login_required)):
    letter = _ds.get_letter_by_id(letter_id)
    if not letter:
        flash(request, "Letter not found.", "error")
        return RedirectResponse("/letters/", status_code=302)
    path = _ds.get_uploaded_file_path(letter)
    if not path:
        flash(request, "No uploaded document is attached to this letter.", "error")
        return RedirectResponse(f"/letters/{letter_id}", status_code=302)
    fname = letter.get("original_filename") or path.name
    media = _ORIGINAL_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, filename=fname, media_type=media)


# ── Mail Tracker ─────────────────────────────────────────────────────────────

@router.get("/tracker/all", name="letters_tracker")
async def tracker(request: Request, user=Depends(login_required)):
    events  = _ds.get_tracker()
    letters = {l["letter_id"]: l for l in _ds.get_all_letters()}
    ctx     = template_context(request)
    ctx.update(events=events, letters=letters)
    return templates.TemplateResponse("letters/tracker.html", ctx)


# ── Signature Management ──────────────────────────────────────────────────────

@router.get("/signatures/manage", name="letters_signatures")
async def manage_signatures(request: Request, user=Depends(login_required)):
    sigs = _ds.get_all_signatures()
    ctx  = template_context(request)
    ctx.update(signatures=sigs, signatories=_ds.SIGNATORIES)
    return templates.TemplateResponse("letters/signatures.html", ctx)


@router.post("/signatures/save", name="letters_signatures_save")
async def save_signature(request: Request, user=Depends(login_required)):
    form     = await request.form()
    role     = form.get("role", "").strip().upper()
    data_url = form.get("signature_data", "").strip()
    if not role or not data_url:
        flash(request, "Role and signature data are required.", "error")
        return RedirectResponse("/letters/signatures/manage", status_code=303)
    ok = _ds.save_signature(role, data_url, saved_by=request.session.get("username", ""))
    if ok:
        flash(request, f"{role} signature saved.", "success")
    else:
        flash(request, "Invalid role or signature data.", "error")
    return RedirectResponse("/letters/signatures/manage", status_code=303)


@router.post("/signatures/delete", name="letters_signatures_delete")
async def delete_signature(request: Request, user=Depends(login_required)):
    form = await request.form()
    role = form.get("role", "").strip().upper()
    _ds.delete_signature(role)
    flash(request, f"{role} signature removed.", "info")
    return RedirectResponse("/letters/signatures/manage", status_code=303)
