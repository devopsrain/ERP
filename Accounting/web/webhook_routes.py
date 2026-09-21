"""
Webhooks & API Keys — routes.

  /webhooks/                      dashboard
  /webhooks/docs                  integrator documentation
  /webhooks/endpoints[...]        endpoint CRUD, toggle, test, rotate secret
  /webhooks/deliveries[...]       delivery log, detail, retry
  /webhooks/api-keys[...]         API key create (one-time reveal) / revoke
  /webhooks/inbound-secrets[...]  per-source secrets for the inbound receiver
  /webhooks/inbound/{source}      PUBLIC generic receiver (logs, verifies, 202)

Everything except /inbound/ is admin-only (session auth enforced centrally in
app.py; ``admin_required`` adds the privilege check).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from api_keys import (SCOPES, generate_api_key, generate_salt, hash_api_key,
                      key_display_prefix, normalise_scopes)
from deps import admin_required, current_company, flash, template_context
from template_engine import templates
from webhook_data_store import (MAX_ATTEMPTS, STANDARD_EVENTS, attempt_delivery, emit,
                                generate_endpoint_secret, is_url_allowed,
                                verify_inbound_signature, webhook_store)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

DELIVERY_STATUSES = ("pending", "failed", "success", "dead")
INBOUND_BODY_LIMIT = 256 * 1024

# Fan bus events out to webhooks as soon as the module is loaded.
try:
    import webhook_event_handlers  # noqa: F401
except Exception as _bridge_err:  # pragma: no cover
    logger.warning("webhook bus bridge not loaded: %s", _bridge_err)


def _actor(request: Request) -> str:
    return request.session.get("username", "") or ""


def _parse_events_form(form) -> list:
    """Checkbox list + free-text patterns → de-duplicated list."""
    picked = [e for e in form.getlist("events")] if hasattr(form, "getlist") else []
    custom = (form.get("custom_events") or "").replace(";", ",").replace("\n", ",")
    picked += [p.strip() for p in custom.split(",")]
    out: list = []
    for p in picked:
        p = (p or "").strip().lower()
        if p and p not in out:
            out.append(p)
    return out


def _endpoint_form_ctx(request: Request, endpoint: dict, errors=None) -> dict:
    ctx = template_context(request)
    ctx.update(endpoint=endpoint or {}, standard_events=STANDARD_EVENTS,
               errors=errors or [], is_edit=bool((endpoint or {}).get("id")))
    return ctx


# ── Dashboard / docs (static paths first) ────────────────────────────────────

@router.get("/", name="webhook_dashboard")
async def dashboard(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(stats=webhook_store.get_stats(cid),
               failures=webhook_store.recent_failures(cid, 10),
               endpoints=webhook_store.list_endpoints(cid)[:5],
               inbound=webhook_store.list_inbound(cid, 10),
               inbound_secrets=webhook_store.list_inbound_secrets(cid),
               max_attempts=MAX_ATTEMPTS)
    return templates.TemplateResponse("webhooks/dashboard.html", ctx)


@router.get("/docs", name="webhook_docs")
async def docs(request: Request, user=Depends(admin_required)):
    ctx = template_context(request)
    base = str(request.base_url).rstrip("/") if request else ""
    ctx.update(standard_events=STANDARD_EVENTS, scopes=SCOPES, base_url=base, max_attempts=MAX_ATTEMPTS)
    return templates.TemplateResponse("webhooks/docs.html", ctx)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/endpoints", name="webhook_endpoints")
async def endpoints(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(endpoints=webhook_store.list_endpoints(cid))
    return templates.TemplateResponse("webhooks/endpoints.html", ctx)


@router.get("/endpoints/new", name="webhook_endpoint_new_get")
async def endpoint_new_get(request: Request, user=Depends(admin_required)):
    return templates.TemplateResponse("webhooks/endpoint_form.html", _endpoint_form_ctx(request, {}))


@router.post("/endpoints/new", name="webhook_endpoint_new_post")
async def endpoint_new_post(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    form = await request.form()
    data = {
        "url": (form.get("url") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "events": _parse_events_form(form),
        "is_active": form.get("is_active") in ("on", "1", "true"),
        "created_by": _actor(request),
    }
    errors = []
    if not data["url"]:
        errors.append("URL is required")
    else:
        ok, reason = is_url_allowed(data["url"])
        if not ok:
            errors.append(reason)
    if not data["events"]:
        errors.append("Subscribe to at least one event (or use *)")
    if errors:
        return templates.TemplateResponse("webhooks/endpoint_form.html", _endpoint_form_ctx(request, data, errors))
    ep = webhook_store.create_endpoint(cid, data)
    if ep:
        flash(request, "Endpoint created — copy the signing secret into your receiver", "success")
        return RedirectResponse(f"/webhooks/endpoints/{ep['id']}/edit", status_code=303)
    flash(request, "Failed to create endpoint", "error")
    return RedirectResponse("/webhooks/endpoints", status_code=303)


@router.get("/endpoints/{endpoint_id}/edit", name="webhook_endpoint_edit_get")
async def endpoint_edit_get(endpoint_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ep = webhook_store.get_endpoint(endpoint_id, cid)
    if not ep:
        flash(request, "Endpoint not found", "error")
        return RedirectResponse("/webhooks/endpoints", status_code=303)
    ctx = _endpoint_form_ctx(request, ep)
    ctx.update(recent=webhook_store.list_deliveries(cid, endpoint_id=endpoint_id, limit=10))
    return templates.TemplateResponse("webhooks/endpoint_form.html", ctx)


@router.post("/endpoints/{endpoint_id}/edit", name="webhook_endpoint_edit_post")
async def endpoint_edit_post(endpoint_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ep = webhook_store.get_endpoint(endpoint_id, cid)
    if not ep:
        flash(request, "Endpoint not found", "error")
        return RedirectResponse("/webhooks/endpoints", status_code=303)
    form = await request.form()
    data = {
        "url": (form.get("url") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "events": _parse_events_form(form),
        "is_active": form.get("is_active") in ("on", "1", "true"),
    }
    errors = []
    if not data["url"]:
        errors.append("URL is required")
    else:
        ok, reason = is_url_allowed(data["url"])
        if not ok:
            errors.append(reason)
    if not data["events"]:
        errors.append("Subscribe to at least one event (or use *)")
    if errors:
        merged = {**ep, **data}
        return templates.TemplateResponse("webhooks/endpoint_form.html", _endpoint_form_ctx(request, merged, errors))
    if webhook_store.update_endpoint(endpoint_id, cid, data):
        flash(request, "Endpoint updated", "success")
    else:
        flash(request, "Failed to update endpoint", "error")
    return RedirectResponse(f"/webhooks/endpoints/{endpoint_id}/edit", status_code=303)


@router.post("/endpoints/{endpoint_id}/toggle", name="webhook_endpoint_toggle")
async def endpoint_toggle(endpoint_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ep = webhook_store.get_endpoint(endpoint_id, cid)
    if not ep:
        flash(request, "Endpoint not found", "error")
    elif webhook_store.set_endpoint_active(endpoint_id, cid, not ep["is_active"]):
        flash(request, f"Endpoint {'paused' if ep['is_active'] else 'activated'}", "success")
    else:
        flash(request, "Failed to update endpoint", "error")
    return RedirectResponse("/webhooks/endpoints", status_code=303)


@router.post("/endpoints/{endpoint_id}/rotate-secret", name="webhook_endpoint_rotate_secret")
async def endpoint_rotate_secret(endpoint_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    if webhook_store.rotate_endpoint_secret(endpoint_id, cid):
        flash(request, "Signing secret rotated — update your receiver before the next event", "success")
    else:
        flash(request, "Failed to rotate secret", "error")
    return RedirectResponse(f"/webhooks/endpoints/{endpoint_id}/edit", status_code=303)


@router.post("/endpoints/{endpoint_id}/delete", name="webhook_endpoint_delete")
async def endpoint_delete(endpoint_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    if webhook_store.delete_endpoint(endpoint_id, cid):
        flash(request, "Endpoint and its delivery history deleted", "success")
    else:
        flash(request, "Failed to delete endpoint", "error")
    return RedirectResponse("/webhooks/endpoints", status_code=303)


@router.post("/endpoints/{endpoint_id}/test", name="webhook_endpoint_test")
async def endpoint_test(endpoint_id: str, request: Request, user=Depends(admin_required)):
    """Send a synchronous ``webhook.test`` event to just this endpoint."""
    cid = current_company(request)
    ep = webhook_store.get_endpoint(endpoint_id, cid)
    if not ep:
        flash(request, "Endpoint not found", "error")
        return RedirectResponse("/webhooks/endpoints", status_code=303)
    payload = {"message": "EBMS test event", "endpoint_id": endpoint_id,
               "triggered_by": _actor(request), "at": datetime.utcnow().isoformat() + "Z",
               "event_id": f"test-{datetime.utcnow().timestamp()}"}
    row = webhook_store.create_delivery(endpoint_id, cid, "webhook.test", payload)
    if not row:
        flash(request, "Could not queue test delivery", "error")
        return RedirectResponse(f"/webhooks/endpoints/{endpoint_id}/edit", status_code=303)
    result = attempt_delivery(row["id"]) or row
    if result.get("status") == "success":
        flash(request, f"Test delivered — HTTP {result.get('last_status_code')}", "success")
    else:
        flash(request, f"Test failed: {result.get('last_error') or 'no response'}", "error")
    return RedirectResponse(f"/webhooks/deliveries/{row['id']}", status_code=303)


# ── Deliveries ───────────────────────────────────────────────────────────────

@router.get("/deliveries", name="webhook_deliveries")
async def deliveries(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    q = request.query_params
    status = q.get("status") or None
    if status and status not in DELIVERY_STATUSES:
        status = None
    endpoint_id = q.get("endpoint_id") or None
    event = q.get("event") or None
    ctx = template_context(request)
    ctx.update(deliveries=webhook_store.list_deliveries(cid, status, endpoint_id, event, limit=200),
               endpoints=webhook_store.list_endpoints(cid),
               events_seen=webhook_store.list_events_seen(cid),
               statuses=DELIVERY_STATUSES,
               status_filter=status or "", endpoint_filter=endpoint_id or "", event_filter=event or "")
    return templates.TemplateResponse("webhooks/deliveries.html", ctx)


@router.get("/deliveries/{delivery_id}", name="webhook_delivery_detail")
async def delivery_detail(delivery_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    d = webhook_store.get_delivery(delivery_id, cid)
    if not d:
        flash(request, "Delivery not found", "error")
        return RedirectResponse("/webhooks/deliveries", status_code=303)
    d.pop("endpoint_secret", None)
    ctx = template_context(request)
    ctx.update(delivery=d,
               payload_json=json.dumps(d.get("payload") or {}, indent=2, default=str, ensure_ascii=False),
               attempts=list(reversed(d.get("attempt_log") or [])),
               max_attempts=MAX_ATTEMPTS)
    return templates.TemplateResponse("webhooks/delivery_detail.html", ctx)


@router.post("/deliveries/{delivery_id}/retry", name="webhook_delivery_retry")
async def delivery_retry(delivery_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    if not webhook_store.requeue_delivery(delivery_id, cid):
        flash(request, "Delivery cannot be retried (already delivered or not found)", "error")
        return RedirectResponse(f"/webhooks/deliveries/{delivery_id}", status_code=303)
    result = attempt_delivery(delivery_id)
    if result and result.get("status") == "success":
        flash(request, f"Redelivered — HTTP {result.get('last_status_code')}", "success")
    else:
        flash(request, f"Retry failed: {(result or {}).get('last_error') or 'no response'}", "error")
    return RedirectResponse(f"/webhooks/deliveries/{delivery_id}", status_code=303)


# ── API keys ─────────────────────────────────────────────────────────────────

@router.get("/api-keys", name="webhook_api_keys")
async def api_keys_page(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    ctx = template_context(request)
    ctx.update(keys=webhook_store.list_api_keys(cid), scopes=SCOPES, now=datetime.utcnow())
    return templates.TemplateResponse("webhooks/api_keys.html", ctx)


@router.post("/api-keys/new", name="webhook_api_key_create")
async def api_key_create(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    form = await request.form()
    name = (form.get("name") or "").strip()
    scopes = normalise_scopes(form.getlist("scopes") if hasattr(form, "getlist") else form.get("scopes"))
    scopes += normalise_scopes(form.get("custom_scopes"))
    scopes = list(dict.fromkeys(scopes)) or ["read"]
    expires_at = None
    days_raw = (form.get("expires_days") or "").strip()
    if days_raw:
        try:
            days = int(days_raw)
            if days > 0:
                expires_at = datetime.utcnow() + timedelta(days=days)
        except ValueError:
            pass
    if not name:
        flash(request, "Give the key a name so you can recognise it later", "error")
        return RedirectResponse("/webhooks/api-keys", status_code=303)

    plaintext = generate_api_key()
    salt = generate_salt()
    row = webhook_store.create_api_key(cid, name, key_display_prefix(plaintext), hash_api_key(plaintext, salt),
                                       salt, scopes, _actor(request), expires_at)
    if not row:
        flash(request, "Failed to create API key", "error")
        return RedirectResponse("/webhooks/api-keys", status_code=303)
    # Rendered once, directly from the POST — the plaintext is never stored or flashed.
    ctx = template_context(request)
    ctx.update(key=row, plaintext=plaintext, base_url=str(request.base_url).rstrip("/"))
    resp = templates.TemplateResponse("webhooks/api_key_created.html", ctx)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/api-keys/{key_id}/revoke", name="webhook_api_key_revoke")
async def api_key_revoke(key_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    if webhook_store.revoke_api_key(key_id, cid):
        flash(request, "API key revoked", "success")
    else:
        flash(request, "Key not found or already revoked", "error")
    return RedirectResponse("/webhooks/api-keys", status_code=303)


# ── Inbound secrets (management) ─────────────────────────────────────────────

@router.post("/inbound-secrets", name="webhook_inbound_secret_set")
async def inbound_secret_set(request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    form = await request.form()
    source = (form.get("source") or "").strip().lower()
    secret = (form.get("secret") or "").strip() or generate_endpoint_secret()
    if not source or not all(c.isalnum() or c in "-_." for c in source):
        flash(request, "Source must be a short slug (letters, digits, - _ .)", "error")
    elif webhook_store.set_inbound_secret(cid, source, secret):
        flash(request, f"Inbound secret saved for '{source}'", "success")
    else:
        flash(request, "Failed to save inbound secret", "error")
    return RedirectResponse("/webhooks/", status_code=303)


@router.post("/inbound-secrets/{secret_id}/delete", name="webhook_inbound_secret_delete")
async def inbound_secret_delete(secret_id: str, request: Request, user=Depends(admin_required)):
    cid = current_company(request)
    if webhook_store.delete_inbound_secret(secret_id, cid):
        flash(request, "Inbound secret removed", "success")
    else:
        flash(request, "Inbound secret not found", "error")
    return RedirectResponse("/webhooks/", status_code=303)


# ── Inbound receiver (PUBLIC, CSRF-exempt — see app.py) ──────────────────────

@router.api_route("/inbound/{source}", methods=["POST", "PUT"], name="webhook_inbound")
async def inbound(source: str, request: Request):
    """
    Generic provider callback receiver. Accepts any content-type, logs the
    raw body + headers, verifies an optional ``X-Signature`` against the
    per-source secret(s), and ALWAYS answers 202 (never 500) so providers
    do not retry-storm us. Downstream modules read webhook_inbound_log.
    """
    log_id, verified, company_id = None, False, None
    try:
        source = (source or "unknown").strip().lower()[:100]
        try:
            raw = await request.body()
        except Exception:
            raw = b""
        body = raw[:INBOUND_BODY_LIMIT].decode("utf-8", errors="replace")
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("cookie", "authorization")}
        company_id = (request.query_params.get("company_id") or request.headers.get("X-EBMS-Company") or "").strip() or None

        signature = (request.headers.get("X-Signature") or request.headers.get("X-EBMS-Signature")
                     or request.headers.get("X-Hub-Signature-256") or "")
        candidates = webhook_store.inbound_secrets_for_source(source, company_id)
        if signature and candidates:
            for cand in candidates:
                if verify_inbound_signature(cand.get("secret") or "", body, signature):
                    verified = True
                    company_id = company_id or cand.get("company_id")
                    break
        elif candidates and not signature:
            logger.warning("inbound webhook source=%s arrived without signature", source)
        log_id = webhook_store.log_inbound(company_id, source, headers, body, verified)
        logger.info("inbound webhook source=%s bytes=%d verified=%s id=%s", source, len(raw), verified, log_id)
    except Exception as exc:  # never 500 towards a provider
        logger.error("inbound webhook handler error source=%s err=%s", source, exc, exc_info=True)
    return JSONResponse({"status": "accepted", "id": log_id, "verified": verified}, status_code=202)
