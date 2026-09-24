"""
Contract Management Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, Response
from deps import flash, template_context, login_required, current_company, validate_upload
from template_engine import templates
from contract_data_store import contract_store
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contract", tags=["contract"])

VALID_STATUSES = ("draft", "active", "expired", "terminated", "renewed")
STATUS_EVENTS = {"active": "activated", "terminated": "terminated", "expired": "expired"}

# Signed contracts, annexes, amendments — stored through the shared document
# backend (Nextcloud when configured, local disk otherwise).
CONTRACT_FILE_EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png", "zip", "txt"}
DOC_MODULE = "contract"


@router.on_event("startup")
async def _startup():
    contract_store.ensure_schema()


def _actor(request: Request) -> str:
    return request.session.get("username", "")


def _contract_documents(cid: str, contract_id: str) -> list:
    try:
        import document_storage as storage
        return storage.documents_for(cid, DOC_MODULE, contract_id)
    except Exception as e:
        logger.warning("contract documents unavailable for %s: %s", contract_id, e)
        return []


async def _store_upload(request: Request, cid: str, contract_id: str, upload, kind: str = "contract"):
    """Validate + persist one uploaded file; returns (doc_dict|None, error|None)."""
    if not upload or not getattr(upload, "filename", ""):
        return None, "No file selected"
    content = await upload.read()
    ok, err = validate_upload(upload.filename, content, allowed_exts=CONTRACT_FILE_EXTS)
    if not ok:
        return None, err
    try:
        import document_storage as storage
        doc = storage.store_document(
            cid, DOC_MODULE, contract_id, upload.filename, content,
            content_type=getattr(upload, "content_type", None),
            uploaded_by=_actor(request), tags=[kind],
            ip=(request.client.host if request.client else ""),
        )
        return doc, None
    except Exception as e:
        logger.error("contract %s upload failed: %s", contract_id, e)
        return None, f"Upload failed: {e}"


@router.get("/", name="contract_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(stats=contract_store.get_stats(cid),
               expiring=contract_store.get_expiring(cid, 60),
               contracts=contract_store.get_contracts(cid)[:10])
    return templates.TemplateResponse("contracts/dashboard.html", ctx)


# Static paths MUST be registered before /{contract_id}

@router.get("/list", name="contract_list")
async def contract_list(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    status = request.query_params.get("status") or None
    party_type = request.query_params.get("party_type") or None
    ctx = template_context(request)
    ctx.update(contracts=contract_store.get_contracts(cid, status, party_type),
               status_filter=status or "", party_type_filter=party_type or "",
               list_title="Contracts")
    return templates.TemplateResponse("contracts/list.html", ctx)


@router.get("/new", name="contract_new_get")
async def new_contract_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("contracts/form.html", {**template_context(request), "contract": {}})


@router.post("/new", name="contract_new_post")
async def new_contract_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    upload = form.get("contract_file")
    data = {k: v for k, v in form.items() if k != "contract_file"}
    data["created_by"] = _actor(request)
    c = contract_store.create_contract(cid, data)
    if c:
        contract_store.add_event(c["id"], "created", "Contract created", _actor(request))
        # Optional signed copy attached at creation time
        if upload is not None and getattr(upload, "filename", ""):
            doc, err = await _store_upload(request, cid, c["id"], upload)
            if doc:
                contract_store.add_event(c["id"], "document", f"Uploaded {doc['filename']}", _actor(request))
                flash(request, "Contract created and file attached", "success")
            else:
                flash(request, f"Contract created, but the file was not saved: {err}", "error")
        else:
            flash(request, "Contract created", "success")
        return RedirectResponse(f"/contract/{c['id']}", status_code=303)
    flash(request, "Failed to create contract", "error")
    return RedirectResponse("/contract/new", status_code=303)


@router.get("/expiring", name="contract_expiring")
async def contract_expiring(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(contracts=contract_store.get_expiring(cid, 60),
               status_filter="", party_type_filter="",
               list_title="Contracts expiring within 60 days")
    return templates.TemplateResponse("contracts/list.html", ctx)


@router.get("/{contract_id}", name="contract_detail")
async def contract_detail(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    c = contract_store.get_contract(contract_id, cid)
    if not c:
        flash(request, "Contract not found", "error")
        return RedirectResponse("/contract/", status_code=303)
    documents = _contract_documents(cid, contract_id)
    # The most recent PDF is embedded for quick reading
    preview_doc = next((d for d in documents
                        if (d.get("content_type") or "").lower() == "application/pdf"
                        or (d.get("filename") or "").lower().endswith(".pdf")), None)
    ctx = template_context(request)
    ctx.update(contract=c, events=contract_store.get_events(contract_id),
               documents=documents, preview_doc=preview_doc)
    return templates.TemplateResponse("contracts/detail.html", ctx)


# ── Contract files ────────────────────────────────────────────────

@router.post("/{contract_id}/upload", name="contract_upload")
async def contract_upload(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    if not contract_store.get_contract(contract_id, cid):
        flash(request, "Contract not found", "error")
        return RedirectResponse("/contract/", status_code=303)
    form = await request.form()
    kind = (form.get("kind") or "contract").strip()[:40]
    doc, err = await _store_upload(request, cid, contract_id, form.get("file"), kind)
    if doc:
        contract_store.add_event(contract_id, "document",
                                 f"Uploaded {doc['filename']} ({kind})", _actor(request))
        flash(request, "File uploaded" + (" (stored locally — Nextcloud unavailable)" if doc.get("fallback_error") else ""),
              "success")
    else:
        flash(request, err or "Upload failed", "error")
    return RedirectResponse(f"/contract/{contract_id}#files", status_code=303)


@router.get("/{contract_id}/file/{doc_id}", name="contract_file")
async def contract_file(contract_id: str, doc_id: str, request: Request, user=Depends(login_required)):
    """Stream the file. Default is inline (view in browser); ?download=1 forces a download."""
    cid = current_company(request)
    try:
        import document_storage as storage
        doc = storage.get_document(doc_id, cid)
        if doc.get("module") != DOC_MODULE or doc.get("entity_id") != contract_id or doc.get("deleted_at"):
            raise storage.DocumentNotFound("Document not found")
        data, filename, ctype = storage.open_document(doc_id, cid, by=_actor(request),
                                                      ip=(request.client.host if request.client else ""))
        inline = request.query_params.get("download") != "1"
        headers = {
            "Content-Disposition": storage.content_disposition(filename, inline=inline),
            "Content-Length": str(len(data)),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        return Response(content=data, media_type=ctype, headers=headers)
    except Exception as e:
        logger.warning("contract file %s/%s: %s", contract_id, doc_id, e)
        flash(request, f"Could not open file: {e}", "error")
        return RedirectResponse(f"/contract/{contract_id}#files", status_code=303)


@router.post("/{contract_id}/file/{doc_id}/delete", name="contract_file_delete")
async def contract_file_delete(contract_id: str, doc_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    try:
        import document_storage as storage
        doc = storage.get_document(doc_id, cid)
        if doc.get("module") != DOC_MODULE or doc.get("entity_id") != contract_id:
            raise storage.DocumentNotFound("Document not found")
        if storage.delete_document(doc_id, by=_actor(request), company_id=cid):
            contract_store.add_event(contract_id, "document", f"Removed {doc['filename']}", _actor(request))
            flash(request, "File removed", "success")
        else:
            flash(request, "Could not remove file", "error")
    except Exception as e:
        flash(request, f"Could not remove file: {e}", "error")
    return RedirectResponse(f"/contract/{contract_id}#files", status_code=303)


@router.post("/{contract_id}/edit", name="contract_edit")
async def contract_edit(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if contract_store.update_contract(contract_id, cid, data):
        contract_store.add_event(contract_id, "amended", "Contract details amended", _actor(request))
        flash(request, "Contract updated", "success")
    else:
        flash(request, "Failed to update contract", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)


@router.post("/{contract_id}/status", name="contract_status")
async def contract_status(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    new_status = form.get("status", "")
    if new_status not in VALID_STATUSES:
        flash(request, "Invalid status", "error")
        return RedirectResponse(f"/contract/{contract_id}", status_code=303)
    if contract_store.set_status(contract_id, cid, new_status):
        contract_store.add_event(contract_id, STATUS_EVENTS.get(new_status, "note"),
                                 f"Status changed to {new_status}", _actor(request))
        flash(request, f"Contract marked {new_status}", "success")
    else:
        flash(request, "Failed to update status", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)


@router.post("/{contract_id}/renew", name="contract_renew")
async def contract_renew(contract_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    new = contract_store.renew_contract(contract_id, cid, _actor(request))
    if new:
        flash(request, "Contract renewed — review the new draft", "success")
        return RedirectResponse(f"/contract/{new['id']}", status_code=303)
    flash(request, "Failed to renew contract", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)


@router.post("/{contract_id}/note", name="contract_note")
async def contract_note(contract_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    note = (form.get("note") or "").strip()
    if note:
        contract_store.add_event(contract_id, "note", note, _actor(request))
        flash(request, "Note added", "success")
    else:
        flash(request, "Note cannot be empty", "error")
    return RedirectResponse(f"/contract/{contract_id}", status_code=303)
