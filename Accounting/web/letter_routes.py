"""
Letter & E-Signature Routes

Endpoints:
  GET  /letters/                   - dashboard / inbox
  GET  /letters/compose            - compose new letter
  POST /letters/compose            - save draft
  GET  /letters/<id>               - view a letter
  POST /letters/<id>/sign          - apply e-signature (PM / FM / MD)
  POST /letters/<id>/send          - mark as sent
  POST /letters/<id>/delete        - delete draft
  GET  /letters/<id>/download      - download .docx
  GET  /letters/tracker            - full mail tracker
  GET  /letters/signatures         - manage stored signatures (admin)
  POST /letters/signatures/save    - upload/save a signature canvas PNG
  POST /letters/signatures/delete  - delete a stored signature
"""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, login_required, admin_required
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
        "company_id": request.session.get("current_company_id", "default"),
    }
    if not data["to"] or not data["subject"] or not data["body"]:
        flash(request, "To, Subject and Body are required.", "error")
        ctx = template_context(request)
        ctx.update(letter=data, signatories=_ds.SIGNATORIES)
        return templates.TemplateResponse("letters/compose.html", ctx)

    letter = _ds.create_letter(data, created_by=request.session.get("username", ""))
    flash(request, f"Letter {letter['ref_number']} created.", "success")
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
