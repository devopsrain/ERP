"""
Telegram Bot Routes  (prefix /telegram, route names telegram_*)

  GET  /telegram/                 dashboard — config status, webhook info, counts
  POST /telegram/set-webhook      admin: register TELEGRAM_PUBLIC_BASE_URL + /telegram/webhook
  POST /telegram/remove-webhook   admin
  GET  /telegram/link             generate a link code, see / unlink my chats
  POST /telegram/link/generate
  POST /telegram/link/{chat_id}/unlink
  POST /telegram/link/{chat_id}/test
  GET  /telegram/subscriptions    per-chat topic toggles
  POST /telegram/subscriptions/{chat_id}
  GET  /telegram/log              admin: recent updates + outbox
  GET  /telegram/help             setup guide
  POST /telegram/webhook          PUBLIC (app.py exempts it from auth + CSRF) —
                                  secret-header verified, deduped, processed in
                                  a background task, always answers {"ok": true}

Note: the public/CSRF exemption in app.py is a prefix match on
"/telegram/webhook", so the admin actions deliberately live at
/set-webhook and /remove-webhook rather than under /webhook/.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from db import run_sync
from deps import admin_required, current_company, flash, login_required, template_context
from template_engine import templates
from telegram_data_store import TOPIC_LABELS, TOPICS, LINK_CODE_TTL_MINUTES, telegram_store
import telegram_bot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

_me_cache = {"at": 0.0, "value": None}


def _is_admin(user: dict) -> bool:
    try:
        from auth_data_store import PRIVILEGE_LEVELS
        return PRIVILEGE_LEVELS.get(user.get("privilege_level", "viewer"), 0) >= PRIVILEGE_LEVELS.get("admin", 50)
    except Exception:
        return False


def _bot_username() -> str:
    """@username of the bot (getMe), cached 10 minutes. '' when unconfigured."""
    if not telegram_bot.is_configured():
        return ""
    if time.time() - _me_cache["at"] < 600 and _me_cache["value"] is not None:
        return _me_cache["value"]
    res = telegram_bot._api_call("getMe", {}, timeout=6)
    name = (res.get("result") or {}).get("username", "") if res.get("ok") else ""
    _me_cache.update(at=time.time(), value=name)
    return name


def _webhook_info() -> dict | None:
    if not telegram_bot.is_configured():
        return None
    res = telegram_bot.get_webhook_info()
    return res.get("result") if res.get("ok") else {"error": res.get("description", "unavailable")}


# ── Dashboard ─────────────────────────────────────────────────────

@router.get("/", name="telegram_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    ctx = template_context(request)
    ctx.update(
        cfg=telegram_bot.config_status(),
        webhook_info=await run_sync(_webhook_info),
        stats=await run_sync(telegram_store.get_stats),
        my_links=await run_sync(telegram_store.links_for_user, username),
        is_admin=_is_admin(user),
        topics=TOPICS, topic_labels=TOPIC_LABELS,
    )
    return templates.TemplateResponse("telegram/dashboard.html", ctx)


@router.post("/set-webhook", name="telegram_set_webhook")
async def set_webhook(request: Request, user=Depends(admin_required)):
    if not telegram_bot.is_configured():
        flash(request, "TELEGRAM_BOT_TOKEN is not configured", "error")
        return RedirectResponse("/telegram/", status_code=303)
    res = await run_sync(telegram_bot.set_webhook)
    if res.get("ok"):
        flash(request, f"Webhook set to {telegram_bot.webhook_url()}", "success")
    else:
        flash(request, f"Set webhook failed: {res.get('description', 'unknown error')}", "error")
    return RedirectResponse("/telegram/", status_code=303)


@router.post("/remove-webhook", name="telegram_remove_webhook")
async def remove_webhook(request: Request, user=Depends(admin_required)):
    res = await run_sync(telegram_bot.delete_webhook)
    if res.get("ok"):
        flash(request, "Webhook removed", "success")
    else:
        flash(request, f"Remove webhook failed: {res.get('description', 'unknown error')}", "error")
    return RedirectResponse("/telegram/", status_code=303)


# ── Link page ─────────────────────────────────────────────────────

@router.get("/link", name="telegram_link")
async def link_page(request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    ctx = template_context(request)
    ctx.update(
        cfg=telegram_bot.config_status(),
        codes=await run_sync(telegram_store.active_codes_for, username),
        links=await run_sync(telegram_store.links_for_user, username),
        bot_username=await run_sync(_bot_username),
        ttl_minutes=LINK_CODE_TTL_MINUTES,
    )
    return templates.TemplateResponse("telegram/link.html", ctx)


@router.post("/link/generate", name="telegram_link_generate")
async def link_generate(request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    cid = current_company(request)
    row = await run_sync(telegram_store.create_link_code, username, cid)
    if row:
        flash(request, f"Code {row['code']} generated — send /link {row['code']} to the bot "
                       f"within {LINK_CODE_TTL_MINUTES} minutes", "success")
    else:
        flash(request, "Could not generate a code", "error")
    return RedirectResponse("/telegram/link", status_code=303)


@router.post("/link/{chat_id}/unlink", name="telegram_link_unlink")
async def link_unlink(chat_id: int, request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    owner = None if _is_admin(user) else username
    if await run_sync(telegram_store.unlink_chat, chat_id, owner):
        flash(request, "Chat unlinked", "success")
    else:
        flash(request, "Chat not found or not yours", "error")
    return RedirectResponse("/telegram/link", status_code=303)


@router.post("/link/{chat_id}/test", name="telegram_link_test")
async def link_test(chat_id: int, request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    link = await run_sync(telegram_store.get_link_by_chat, chat_id)
    if not link or (link["username"] != username and not _is_admin(user)):
        flash(request, "Chat not found or not yours", "error")
    elif not telegram_bot.is_configured():
        flash(request, "Bot is not configured", "error")
    else:
        text = f"🔔 Test message from EBMS for <b>{telegram_bot.esc(link['username'])}</b>."
        if await run_sync(telegram_store.enqueue, chat_id, text):
            flash(request, "Test message queued — it is delivered within a minute", "success")
        else:
            flash(request, "Could not queue the test message", "error")
    return RedirectResponse("/telegram/link", status_code=303)


# ── Subscriptions ─────────────────────────────────────────────────

def _links_with_topics(username: str) -> list:
    links = telegram_store.links_for_user(username)
    for l in links:
        l["topics"] = set(telegram_store.get_subscriptions(l["chat_id"]))
    return links


@router.get("/subscriptions", name="telegram_subscriptions")
async def subscriptions_page(request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    ctx = template_context(request)
    ctx.update(links=await run_sync(_links_with_topics, username),
               topics=TOPICS, topic_labels=TOPIC_LABELS)
    return templates.TemplateResponse("telegram/subscriptions.html", ctx)


@router.post("/subscriptions/{chat_id}", name="telegram_subscriptions_save")
async def subscriptions_save(chat_id: int, request: Request, user=Depends(login_required)):
    username = request.session.get("username", "")
    link = await run_sync(telegram_store.get_link_by_chat, chat_id)
    if not link or (link["username"] != username and not _is_admin(user)):
        flash(request, "Chat not found or not yours", "error")
        return RedirectResponse("/telegram/subscriptions", status_code=303)
    form = await request.form()
    wanted = [t for t in form.getlist("topics") if t in TOPICS]
    if await run_sync(telegram_store.set_subscriptions, chat_id, wanted):
        flash(request, "Subscriptions saved", "success")
    else:
        flash(request, "Could not save subscriptions", "error")
    return RedirectResponse("/telegram/subscriptions", status_code=303)


# ── Log & help ────────────────────────────────────────────────────

@router.get("/log", name="telegram_log")
async def log_page(request: Request, user=Depends(admin_required)):
    ctx = template_context(request)
    ctx.update(updates=await run_sync(telegram_store.recent_updates, 50),
               outbox=await run_sync(telegram_store.recent_outbox, 50),
               stats=await run_sync(telegram_store.get_stats))
    return templates.TemplateResponse("telegram/log.html", ctx)


@router.get("/help", name="telegram_help")
async def help_page(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(cfg=telegram_bot.config_status(), topics=TOPICS, topic_labels=TOPIC_LABELS,
               is_admin=_is_admin(user))
    return templates.TemplateResponse("telegram/help.html", ctx)


# ── Webhook (public) ──────────────────────────────────────────────

def _first_chat_and_text(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if msg:
        return (msg.get("chat") or {}).get("id"), msg.get("text") or ""
    cb = update.get("callback_query")
    if cb:
        return ((cb.get("message") or {}).get("chat") or {}).get("id"), f"[callback] {cb.get('data') or ''}"
    return None, ""


@router.post("/webhook", name="telegram_webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """Telegram → EBMS. Returns 200 {"ok": true} for every accepted call so
    Telegram never re-delivers; only a bad secret is refused (403)."""
    if not telegram_bot.verify_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        logger.warning("telegram webhook rejected: bad or missing secret header")
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    if not telegram_bot.is_configured():
        return JSONResponse({"ok": True, "ignored": "not configured"})
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True, "ignored": "bad json"})
    if not isinstance(update, dict):
        return JSONResponse({"ok": True, "ignored": "bad payload"})

    chat_id, text = _first_chat_and_text(update)
    try:
        is_new = await run_sync(telegram_store.log_update, update.get("update_id"), chat_id, text)
    except Exception as e:
        logger.error("telegram log_update failed: %s", e)
        is_new = True
    if not is_new:
        return JSONResponse({"ok": True, "duplicate": True})

    # Process after the response is sent — the request never waits on the Bot API.
    background_tasks.add_task(telegram_bot.handle_update, update)
    return JSONResponse({"ok": True})
