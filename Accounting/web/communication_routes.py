"""
Communication Platform Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required, validate_upload, current_company
from template_engine import templates
from communication_data_store import comm_store
import logging, re

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/comm", tags=["communication"])


@router.on_event("startup")
async def _startup():
    comm_store.ensure_schema()


@router.get("/", name="comm_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    comm_store.touch_last_seen(request.session.get("user_id", ""))
    ctx = template_context(request)
    ctx.update(
        channels=comm_store.get_channels(cid),
        stats=comm_store.get_channel_stats(cid),
    )
    return templates.TemplateResponse("communication/dashboard.html", ctx)


# NOTE: /channel/new MUST be registered before /channel/{channel_id}, or the
# path parameter route swallows it (channel_id="new").
@router.get("/channel/new", name="comm_new_channel_form")
async def new_channel_form(request: Request, user=Depends(login_required)):
    """Non-modal fallback form for creating a channel (works even if modal JS fails)."""
    # Make sure tables exist before showing the form (idempotent).
    try:
        comm_store.ensure_schema()
    except Exception as e:
        logger.warning("ensure_schema in new_channel_form: %s", e)
    ctx = template_context(request)
    return templates.TemplateResponse("communication/new_channel.html", ctx)


@router.get("/search", name="comm_search")
async def search(request: Request, user=Depends(login_required)):
    """Search messages and shared file names across the company's channels."""
    cid = current_company(request)
    q = request.query_params.get("q", "").strip()
    results = comm_store.search_messages(cid, q) if q else []
    files = comm_store.get_files_for_messages([m["id"] for m in results if m.get("type") == "file"])
    for m in results:
        m["file"] = files.get(m["id"])
    ctx = template_context(request)
    ctx.update(q=q, results=results, channels=comm_store.get_channels(cid))
    return templates.TemplateResponse("communication/search.html", ctx)


@router.get("/channel/{channel_id}", name="comm_channel")
async def channel_view(channel_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    comm_store.touch_last_seen(request.session.get("user_id", ""))
    messages = comm_store.get_messages(channel_id)
    reactions = comm_store.get_reactions(channel_id)
    pinned = comm_store.get_pinned(channel_id)
    channels = comm_store.get_channels(cid)
    active_channel = next((c for c in channels if c["id"] == channel_id), None)
    files = comm_store.get_files_for_messages(
        [m["id"] for m in messages if m.get("type") == "file"])
    # Parse @mentions
    for msg in messages:
        msg["content_html"] = re.sub(
            r"@(\w+)",
            r'<span class="badge bg-primary">@\1</span>',
            msg.get("content", "")
        )
        msg["reactions"] = reactions.get(msg["id"], [])
        msg["file"] = files.get(msg["id"])
    ctx = template_context(request)
    ctx.update(
        channels=channels, active_channel=active_channel,
        messages=messages, pinned=pinned, channel_id=channel_id
    )
    return templates.TemplateResponse("communication/channel.html", ctx)


@router.post("/channel/create", name="comm_create_channel")
async def create_channel(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    form = await request.form()
    name = form.get("name", "").strip()
    ctype = form.get("type", "group")
    if not name:
        flash(request, "Channel name is required", "error")
        return RedirectResponse("/comm/channel/new", status_code=303)
    # Make absolutely sure tables exist (handles cold start / DB-URL only just set)
    try:
        comm_store.ensure_schema()
    except Exception as e:
        logger.warning("ensure_schema in create_channel: %s", e)
    created = comm_store.create_channel(cid, name, ctype, request.session.get("username", ""))
    if created:
        flash(request, f"Channel #{name} created", "success")
        return RedirectResponse("/comm/", status_code=303)
    flash(request, "Failed to create channel — check server logs (DATABASE_URL?)", "error")
    return RedirectResponse("/comm/channel/new", status_code=303)


@router.post("/channel/{channel_id}/message", name="comm_post_message")
async def post_message(channel_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    content = form.get("content", "").strip()
    parent_id = form.get("parent_id") or None
    if not content:
        return RedirectResponse(f"/comm/channel/{channel_id}", status_code=303)
    comm_store.post_message(
        channel_id=channel_id,
        sender_id=request.session.get("user_id", ""),
        sender_name=request.session.get("username", ""),
        content=content,
        parent_id=parent_id,
    )
    return RedirectResponse(f"/comm/channel/{channel_id}", status_code=303)


@router.post("/channel/{channel_id}/upload", name="comm_upload_file")
async def upload_file(channel_id: str, request: Request, user=Depends(login_required)):
    """Send a file / image / document / video into a channel."""
    import os
    import pathlib
    import uuid as _uuid
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        flash(request, "Choose a file to send", "error")
        return RedirectResponse(f"/comm/channel/{channel_id}", status_code=303)
    content = await upload.read()
    if len(content) > 50 * 1024 * 1024:
        flash(request, "File too large (max 50 MB)", "error")
        return RedirectResponse(f"/comm/channel/{channel_id}", status_code=303)
    # AICC 6.3: block dangerous executable/script extensions and empty files
    ok, upload_error = validate_upload(upload.filename, content)
    if not ok:
        flash(request, upload_error, "error")
        return RedirectResponse(f"/comm/channel/{channel_id}", status_code=303)
    safe_ext = pathlib.Path(upload.filename).suffix[:10]
    stored_name = f"{_uuid.uuid4().hex}{safe_ext}"
    base_dir = pathlib.Path(__file__).parent / "data" / "comm" / channel_id
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / stored_name
    path.write_bytes(content)
    msg = comm_store.save_file_message(
        channel_id=channel_id,
        sender_id=request.session.get("user_id", ""),
        sender_name=request.session.get("username", ""),
        original_name=upload.filename,
        stored_name=stored_name,
        storage_path=str(path),
        file_size=len(content),
        mime_type=upload.content_type or "application/octet-stream",
    )
    if not msg:
        try:
            os.remove(path)
        except OSError:
            pass
        flash(request, "Could not send file — check server logs", "error")
    return RedirectResponse(f"/comm/channel/{channel_id}", status_code=303)


@router.get("/file/{file_id}", name="comm_download_file")
async def download_file(file_id: str, request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse
    import os
    meta = comm_store.get_file(file_id)
    if not meta or not os.path.exists(meta.get("storage_path", "")):
        flash(request, "File not found", "error")
        return RedirectResponse("/comm/", status_code=303)
    return FileResponse(meta["storage_path"], filename=meta["original_name"],
                        media_type=meta.get("mime_type") or "application/octet-stream")


@router.post("/message/{message_id}/edit", name="comm_edit_message")
async def edit_message(message_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    new_content = form.get("content", "").strip()
    if not new_content:
        return JSONResponse({"ok": False, "error": "Content required"}, status_code=400)
    ok = comm_store.edit_message(message_id, request.session.get("user_id", ""), new_content)
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found or not your message"}, status_code=403)
    return JSONResponse({"ok": True})


@router.post("/message/{message_id}/delete", name="comm_delete_message")
async def delete_message(message_id: str, request: Request, user=Depends(login_required)):
    comm_store.soft_delete_message(message_id, request.session.get("user_id", ""))
    return JSONResponse({"ok": True})


@router.post("/message/{message_id}/pin", name="comm_pin_message")
async def pin_message(message_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    pinned = form.get("pinned", "true") == "true"
    comm_store.pin_message(message_id, pinned)
    return JSONResponse({"ok": True})


@router.post("/message/{message_id}/react", name="comm_react")
async def react(message_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    emoji = form.get("emoji", "")
    user_id = request.session.get("user_id", "")
    comm_store.add_reaction(message_id, user_id, emoji)
    return JSONResponse({"ok": True})


@router.post("/status", name="comm_set_status")
async def set_status(request: Request, user=Depends(login_required)):
    form = await request.form()
    comm_store.set_status(
        request.session.get("user_id", ""),
        form.get("status_text", ""),
        form.get("dnd", "false") == "true"
    )
    flash(request, "Status updated", "success")
    return RedirectResponse("/comm/", status_code=303)


@router.post("/channel/{channel_id}/delete", name="comm_delete_channel")
async def delete_channel(channel_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    comm_store.delete_channel(channel_id, cid)
    flash(request, "Channel archived", "success")
    return RedirectResponse("/comm/", status_code=303)
