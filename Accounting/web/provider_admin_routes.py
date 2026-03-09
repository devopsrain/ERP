from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import os
from extensions import cache
from tenant_data_store import tenant_store, SUBSCRIPTION_TIERS

PROVIDER_ADMIN_PASSWORD = os.environ.get("PROVIDER_ADMIN_PASSWORD", "provider2026!")

router = APIRouter(prefix="/provider", tags=["provider"])


def _require_provider(request: Request):
    if not request.session.get("is_provider_admin"):
        raise HTTPException(status_code=302, headers={"Location": "/provider/login"})


@router.get("/login", name="provider_admin_provider_login")
async def provider_login_get(request: Request):
    return templates.TemplateResponse("provider/login.html", template_context(request))


@router.post("/login", name="provider_admin_provider_login_post")
async def provider_login_post(request: Request):
    form = await request.form()
    if form.get("password", "") == PROVIDER_ADMIN_PASSWORD:
        request.session["is_provider_admin"] = True
        flash(request, "Welcome, Provider Admin.", "success")
        return RedirectResponse("/provider/", status_code=303)
    flash(request, "Invalid provider password.", "danger")
    return templates.TemplateResponse("provider/login.html", template_context(request))


@router.get("/logout", name="provider_admin_provider_logout")
async def provider_logout(request: Request):
    request.session.pop("is_provider_admin", None)
    flash(request, "Logged out of provider admin.", "info")
    return RedirectResponse("/provider/login", status_code=302)


@router.get("/", name="provider_admin_provider_dashboard")
@router.get("/dashboard", name="provider_admin_provider_dashboard_alt")
async def provider_dashboard(request: Request):
    _require_provider(request)
    stats   = tenant_store.get_platform_stats()
    tenants = tenant_store.get_all_tenants()
    ctx = template_context(request)
    ctx.update(stats=stats, tenants=tenants, tiers=SUBSCRIPTION_TIERS)
    return templates.TemplateResponse("provider/dashboard.html", ctx)


@router.get("/tenants/create", name="provider_admin_create_tenant_get")
async def create_tenant_get(request: Request):
    _require_provider(request)
    ctx = template_context(request)
    ctx.update(tiers=SUBSCRIPTION_TIERS)
    return templates.TemplateResponse("provider/create_tenant.html", ctx)


@router.post("/tenants/create", name="provider_admin_create_tenant")
async def create_tenant_post(request: Request):
    _require_provider(request)
    form = await request.form()
    data = {k: form.get(k, "").strip() for k in [
        "company_name", "registration_number", "tin_number",
        "address", "email", "phone", "business_type", "notes",
    ]}
    data["city"] = form.get("city", "Addis Ababa").strip()
    data["subscription_tier"] = form.get("subscription_tier", "starter")
    if not data["company_name"]:
        flash(request, "Company name is required.", "danger")
        ctx = template_context(request)
        ctx.update(tiers=SUBSCRIPTION_TIERS)
        return templates.TemplateResponse("provider/create_tenant.html", ctx)
    tenant = tenant_store.create_tenant(data, created_by="provider_admin")
    flash(request, f"Tenant created. Key: {tenant['license_key']}", "success")
    return RedirectResponse("/provider/", status_code=303)


@router.get("/tenants/{company_id}", name="provider_admin_view_tenant")
async def view_tenant(company_id: str, request: Request):
    _require_provider(request)
    tenant = cache.get(f"tenant:{company_id}") or tenant_store.get_tenant(company_id)
    if not tenant:
        flash(request, "Tenant not found.", "danger")
        return RedirectResponse("/provider/", status_code=302)
    all_modules = sorted({m for t in SUBSCRIPTION_TIERS.values() for m in t["modules"]})
    ctx = template_context(request)
    ctx.update(
        tenant=tenant,
        licenses=tenant_store.get_company_licenses(company_id),
        enabled_modules=tenant_store.get_enabled_modules(company_id),
        audit_log=tenant_store.get_audit_log(company_id, limit=50),
        tier=SUBSCRIPTION_TIERS.get(tenant.get("subscription_tier", "starter"), {}),
        tiers=SUBSCRIPTION_TIERS,
        all_modules=all_modules,
    )
    return templates.TemplateResponse("provider/view_tenant.html", ctx)


@router.post("/tenants/{company_id}/change-tier", name="provider_admin_change_tier")
async def change_tier(company_id: str, request: Request):
    _require_provider(request)
    form = await request.form()
    new_tier = form.get("subscription_tier", "")
    if new_tier not in SUBSCRIPTION_TIERS:
        flash(request, "Invalid tier.", "danger")
    elif tenant_store.change_subscription_tier(company_id, new_tier, "provider_admin"):
        flash(request, f"Tier changed to {SUBSCRIPTION_TIERS[new_tier]['display_name']}.", "success")
        cache.delete(f"tenant:{company_id}")
    else:
        flash(request, "Failed to change tier.", "danger")
    return RedirectResponse(f"/provider/tenants/{company_id}", status_code=303)


@router.post("/tenants/{company_id}/toggle-module", name="provider_admin_provider_api_toggle_module")
async def toggle_module(company_id: str, request: Request):
    _require_provider(request)
    data = await request.json()
    module = data.get("module")
    enabled = data.get("enabled", False)
    result = tenant_store.set_module_license(company_id, module, enabled, "provider_admin")
    cache.delete(f"tenant:{company_id}")
    return {"success": result}


@router.get("/api/tenants", name="provider_admin_provider_api_tenants")
async def api_tenants(request: Request):
    _require_provider(request)
    return tenant_store.get_all_tenants()
