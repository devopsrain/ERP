"""
Communication Platform Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required
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
    cid = request.session.get("current_company_id", "default")
    comm_store.touch_last_seen(request.session.get("user_id", ""))
    ctx = template_context(request)
    ctx.update(
        channels=comm_store.get_channels(cid),
        stats=comm_store.get_channel_stats(cid),
    )
    return templates.TemplateResponse("communication/dashboard.html", ctx)


@router.get("/channel/{channel_id}", name="comm_channel")
async def channel_view(channel_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    comm_store.touch_last_seen(request.session.get("user_id", ""))
    messages = comm_store.get_messages(channel_id)
    reactions = comm_store.get_reactions(channel_id)
    pinned = comm_store.get_pinned(channel_id)
    channels = comm_store.get_channels(cid)
    active_channel = next((c for c in channels if c["id"] == channel_id), None)
    # Parse @mentions
    for msg in messages:
        msg["content_html"] = re.sub(
            r"@(\w+)",
            r'<span class="badge bg-primary">@\1</span>',
            msg.get("content", "")
        )
        msg["reactions"] = reactions.get(msg["id"], [])
    ctx = template_context(request)
    ctx.update(
        channels=channels, active_channel=active_channel,
        messages=messages, pinned=pinned, channel_id=channel_id
    )
    return templates.TemplateResponse("communication/channel.html", ctx)


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


@router.post("/channel/create", name="comm_create_channel")
async def create_channel(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
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
    cid = request.session.get("current_company_id", "default")
    comm_store.delete_channel(channel_id, cid)
    flash(request, "Channel archived", "success")
    return RedirectResponse("/comm/", status_code=303)
