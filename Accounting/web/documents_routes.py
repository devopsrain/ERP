"""
Documents module routes — Nextcloud (WebDAV) document backend.

Prefix: /documents      Route names: documents_*

Pages: dashboard, browse (with Nextcloud sync/import), upload, search,
settings (admin), folders, detail (download / share / delete / new version).

A Nextcloud outage must never 500 a page: every call into the storage layer
catches NextcloudError / DocumentStorageError, flashes and degrades.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

import document_storage as storage
from db import run_sync
from deps import (admin_required, current_company, flash, login_required,
                  template_context, validate_upload)
from document_storage import DocumentNotFound, DocumentStorageError, content_disposition
from documents_data_store import doc_store
from nextcloud_client import NextcloudError, get_client
from template_engine import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Known modules (value, label). Free-form values matching _MODULE_RE are also accepted
# so other modules can attach files under their own key without touching this list.
MODULES = [
    ("contracts", "Contracts"),
    ("letters", "Letters"),
    ("projects", "Projects"),
    ("procurement", "Procurement"),
    ("communication", "Communication"),
    ("hr", "HR / Employees"),
    ("finance", "Finance"),
    ("bids", "Bids"),
    ("general", "General"),
]
MODULE_LABELS = dict(MODULES)
_MODULE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_ADMIN_LEVELS = ("admin", "super_admin")


# ── helpers ──────────────────────────────────────────────────────────────────

def _actor(request: Request) -> str:
    return request.session.get("username", "") or ""


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _clean_module(raw) -> str:
    value = (raw or "").strip().lower().replace(" ", "_")
    return value if _MODULE_RE.match(value) else "general"


def _clean_entity(raw) -> str:
    return storage.safe_segment(raw, "_")


def _parse_tags(raw) -> list[str]:
    seen, out = set(), []
    for t in re.split(r"[,;\n]", raw or ""):
        t = t.strip()[:40]
        if t and t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out[:20]


def _ctx(request: Request, **extra) -> dict:
    ctx = template_context(request)
    ctx.update(
        modules=MODULES, module_labels=MODULE_LABELS,
        backend=storage.backend_name(),
        nextcloud_configured=get_client().is_configured(),
        is_admin=request.session.get("privilege_level") in _ADMIN_LEVELS,
    )
    ctx.update(extra)
    return ctx


def _back_to_browse(module=None, entity_id=None) -> str:
    url = "/documents/browse"
    if module:
        url += f"?module={module}"
        if entity_id:
            url += f"&entity_id={entity_id}"
    return url


# ── dashboard ────────────────────────────────────────────────────────────────

@router.get("/", name="documents_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    health = await run_sync(get_client().health)          # never raises
    stats = doc_store.get_stats(cid)
    ctx = _ctx(request, health=health, stats=stats,
               recent=doc_store.recent_uploads(cid, 10),
               audit=doc_store.recent_audit(cid, 10))
    return templates.TemplateResponse("documents/dashboard.html", ctx)


# ── static paths (MUST come before /{doc_id}) ────────────────────────────────

@router.get("/browse", name="documents_browse")
async def browse(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    module = (request.query_params.get("module") or "").strip() or None
    entity_id = (request.query_params.get("entity_id") or "").strip() or None
    view = dict(module=module, entity_id=entity_id, modules_summary=[], entities=[],
                documents=[], remote_entries=[], remote_error=None, remote_path="",
                remote_web_url="")
    if not module:
        view["modules_summary"] = doc_store.module_summary(cid)
    elif not entity_id:
        view["entities"] = doc_store.entity_summary(cid, module)
    else:
        view["documents"] = doc_store.documents_for(cid, module, entity_id)

    client = get_client()
    if client.is_configured():
        sub = "/".join(p for p in (module, entity_id) if p)
        view["remote_path"] = f"{client.root}/{storage.company_root(cid)}" + (f"/{sub}" if sub else "")
        try:
            entries = await run_sync(storage.list_remote, cid, sub)
            known = doc_store.known_remote_paths(cid)
            for e in entries:
                e["in_ebms"] = e["rel_path"] in known
            view["remote_entries"] = entries
            view["remote_web_url"] = client.web_url("/".join([storage.company_root(cid), sub]))
        except NextcloudError as e:
            view["remote_error"] = str(e)
    return templates.TemplateResponse("documents/browse.html", _ctx(request, **view))


@router.post("/import", name="documents_import")
async def import_remote(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    rel_path = (form.get("rel_path") or "").strip()
    module = _clean_module(form.get("module"))
    entity_id = _clean_entity(form.get("entity_id"))
    if not rel_path:
        flash(request, "No file selected for import", "error")
        return RedirectResponse(_back_to_browse(), status_code=303)
    try:
        doc = await run_sync(storage.import_remote_file, cid, rel_path, module, entity_id,
                             _actor(request), _ip(request))
        flash(request, f"Imported '{doc['filename']}' into EBMS", "success")
        return RedirectResponse(f"/documents/{doc['id']}", status_code=303)
    except (NextcloudError, DocumentStorageError) as e:
        flash(request, f"Import failed: {e}", "error")
    return RedirectResponse(_back_to_browse(module, entity_id), status_code=303)


@router.get("/upload", name="documents_upload_get")
async def upload_get(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _ctx(request,
               preset_module=(request.query_params.get("module") or "").strip(),
               preset_entity=(request.query_params.get("entity_id") or "").strip(),
               folders=doc_store.list_folders(cid),
               max_upload_mb=storage.MAX_UPLOAD_MB)
    return templates.TemplateResponse("documents/upload.html", ctx)


@router.post("/upload", name="documents_upload_post")
async def upload_post(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    module = _clean_module(form.get("module"))
    entity_id = _clean_entity(form.get("entity_id"))
    tags = _parse_tags(form.get("tags"))
    upload = form.get("file")
    back = f"/documents/upload?module={module}&entity_id={entity_id}"
    if upload is None or not getattr(upload, "filename", ""):
        flash(request, "Choose a file to upload", "error")
        return RedirectResponse(back, status_code=303)
    content = await upload.read()
    if len(content) > storage.MAX_UPLOAD_BYTES:
        flash(request, f"File too large (max {storage.MAX_UPLOAD_MB} MB)", "error")
        return RedirectResponse(back, status_code=303)
    ok, err = validate_upload(upload.filename, content)
    if not ok:
        flash(request, err, "error")
        return RedirectResponse(back, status_code=303)
    try:
        doc = await run_sync(storage.store_document, cid, module, entity_id, upload.filename,
                             content, upload.content_type, _actor(request), tags, None, _ip(request))
    except DocumentStorageError as e:
        flash(request, f"Upload failed: {e}", "error")
        return RedirectResponse(back, status_code=303)
    except NextcloudError as e:  # store_document falls back to local, but be safe
        flash(request, f"Upload failed: {e}", "error")
        return RedirectResponse(back, status_code=303)
    if doc.get("fallback_error"):
        flash(request, f"Nextcloud was unreachable — '{doc['filename']}' was stored locally instead "
                       f"({doc['fallback_error']})", "warning")
    else:
        flash(request, f"Uploaded '{doc['filename']}' ({doc['backend']})", "success")
    return RedirectResponse(f"/documents/{doc['id']}", status_code=303)


@router.get("/search", name="documents_search")
async def search(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    qp = request.query_params
    filters = dict(q=(qp.get("q") or "").strip(), module=(qp.get("module") or "").strip(),
                   tag=(qp.get("tag") or "").strip(), date_from=(qp.get("date_from") or "").strip(),
                   date_to=(qp.get("date_to") or "").strip(), backend=(qp.get("backend") or "").strip())
    searched = any(filters.values())
    results = doc_store.search_documents(cid, **{k: (v or None) for k, v in filters.items()}) \
        if searched else []
    ctx = _ctx(request, filters=filters, results=results, searched=searched)
    return templates.TemplateResponse("documents/search.html", ctx)


@router.get("/settings", name="documents_settings")
async def settings(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    health = await run_sync(get_client().health)
    ctx = _ctx(request, config=storage.describe_config(), health=health,
               stats=doc_store.get_stats(cid), company_root=storage.company_root(cid))
    return templates.TemplateResponse("documents/settings.html", ctx)


@router.post("/settings/test", name="documents_settings_test")
async def settings_test(request: Request, user=Depends(admin_required)):
    health = await run_sync(get_client().health)
    if health.get("ok"):
        root = "exists" if health.get("root_exists") else "missing — use 'Create folder structure'"
        flash(request, f"Connected to Nextcloud {health.get('version') or ''} in "
                       f"{health.get('latency_ms')} ms — root folder {root}", "success")
    else:
        flash(request, f"Connection failed: {health.get('error')}", "error")
    return RedirectResponse("/documents/settings", status_code=303)


@router.post("/settings/create-folders", name="documents_settings_create_folders")
async def settings_create_folders(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    try:
        created = await run_sync(storage.create_folder_structure, cid, [m for m, _ in MODULES])
        flash(request, f"Folder structure ready ({created} folder(s) created)", "success")
    except NextcloudError as e:
        flash(request, f"Could not create folders: {e}", "error")
    return RedirectResponse("/documents/settings", status_code=303)


@router.get("/folders", name="documents_folders")
async def folders(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _ctx(request, folders=doc_store.list_folders(cid),
               company_root=storage.company_root(cid))
    return templates.TemplateResponse("documents/folders.html", ctx)


@router.post("/folders", name="documents_folders_create")
async def folders_create(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    name = (form.get("name") or "").strip()[:80]
    module = (form.get("module") or "").strip() or None
    if module:
        module = _clean_module(module)
    if not name:
        flash(request, "Folder name is required", "error")
        return RedirectResponse("/documents/folders", status_code=303)
    path = (form.get("path") or "").strip().strip("/")
    if not path:
        path = "/".join(p for p in (storage.company_root(cid), module, storage.safe_segment(name)) if p)
    folder = doc_store.create_folder(cid, name, module, path, _actor(request))
    if not folder:
        flash(request, "Failed to create folder", "error")
        return RedirectResponse("/documents/folders", status_code=303)
    if get_client().is_configured():
        try:
            await run_sync(get_client().mkdirs, path)
            flash(request, f"Folder '{name}' created in EBMS and Nextcloud", "success")
        except NextcloudError as e:
            flash(request, f"Folder '{name}' saved, but Nextcloud folder was not created: {e}", "warning")
    else:
        flash(request, f"Folder '{name}' created", "success")
    return RedirectResponse("/documents/folders", status_code=303)


@router.post("/folders/{folder_id}/delete", name="documents_folders_delete")
async def folders_delete(folder_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    if doc_store.delete_folder(folder_id, cid):
        flash(request, "Folder removed from EBMS (files in Nextcloud are untouched)", "success")
    else:
        flash(request, "Folder not found", "error")
    return RedirectResponse("/documents/folders", status_code=303)


# ── per-document ─────────────────────────────────────────────────────────────

@router.get("/{doc_id}", name="documents_detail")
async def detail(doc_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    doc = doc_store.get_document(doc_id, cid)
    if not doc:
        flash(request, "Document not found", "error")
        return RedirectResponse("/documents/", status_code=303)
    ctx = _ctx(request, doc=doc,
               versions=doc_store.get_versions(doc_id, cid),
               shares=doc_store.get_share_links(doc_id),
               audit=doc_store.audit_for(doc_id, 25),
               remote_web_url=(get_client().web_url(doc["remote_path"].rsplit("/", 1)[0])
                               if doc.get("remote_path") and get_client().is_configured() else ""))
    return templates.TemplateResponse("documents/detail.html", ctx)


@router.get("/{doc_id}/download", name="documents_download")
async def download(doc_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    inline = request.query_params.get("inline") == "1"
    try:
        data, filename, ctype = await run_sync(storage.open_document, doc_id, cid,
                                               _actor(request), _ip(request))
    except DocumentNotFound as e:
        flash(request, str(e), "error")
        return RedirectResponse("/documents/", status_code=303)
    except (NextcloudError, DocumentStorageError) as e:
        flash(request, f"Download failed: {e}", "error")
        return RedirectResponse(f"/documents/{doc_id}", status_code=303)
    headers = {
        "Content-Disposition": content_disposition(filename, inline=inline),
        "Content-Length": str(len(data)),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return Response(content=data, media_type=ctype, headers=headers)


@router.post("/{doc_id}/share", name="documents_share")
async def share(doc_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    try:
        days = int(form.get("expire_days") or 7)
    except ValueError:
        days = 7
    days = min(max(days, 0), 365) or None
    password = (form.get("password") or "").strip() or None
    try:
        link = await run_sync(storage.make_share_link, doc_id, days, _actor(request), cid,
                              password, _ip(request))
        flash(request, f"Share link created: {link['url']}", "success")
    except DocumentNotFound:
        flash(request, "Document not found", "error")
        return RedirectResponse("/documents/", status_code=303)
    except (NextcloudError, DocumentStorageError) as e:
        flash(request, f"Could not create share link: {e}", "error")
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/{doc_id}/delete", name="documents_delete")
async def delete(doc_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    try:
        if storage.delete_document(doc_id, _actor(request), cid, _ip(request)):
            flash(request, "Document deleted (file kept in storage for audit)", "success")
        else:
            flash(request, "Document was already deleted", "warning")
    except DocumentNotFound:
        flash(request, "Document not found", "error")
        return RedirectResponse("/documents/", status_code=303)
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/{doc_id}/version", name="documents_version")
async def new_version(doc_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    prev = doc_store.get_document(doc_id, cid)
    if not prev:
        flash(request, "Document not found", "error")
        return RedirectResponse("/documents/", status_code=303)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        flash(request, "Choose a file for the new version", "error")
        return RedirectResponse(f"/documents/{doc_id}", status_code=303)
    content = await upload.read()
    if len(content) > storage.MAX_UPLOAD_BYTES:
        flash(request, f"File too large (max {storage.MAX_UPLOAD_MB} MB)", "error")
        return RedirectResponse(f"/documents/{doc_id}", status_code=303)
    ok, err = validate_upload(upload.filename, content)
    if not ok:
        flash(request, err, "error")
        return RedirectResponse(f"/documents/{doc_id}", status_code=303)
    tags_raw = form.get("tags")
    tags = _parse_tags(tags_raw) if (tags_raw or "").strip() else None
    try:
        doc = await run_sync(storage.store_document, cid, prev["module"], prev["entity_id"],
                             upload.filename, content, upload.content_type, _actor(request),
                             tags, doc_id, _ip(request))
    except (NextcloudError, DocumentStorageError) as e:
        flash(request, f"Version upload failed: {e}", "error")
        return RedirectResponse(f"/documents/{doc_id}", status_code=303)
    if doc.get("fallback_error"):
        flash(request, f"Nextcloud was unreachable — version {doc['version']} stored locally", "warning")
    else:
        flash(request, f"Version {doc['version']} uploaded", "success")
    return RedirectResponse(f"/documents/{doc['id']}", status_code=303)
