"""
Writes all 16 FastAPI-converted route files to web/.
Run once:  python write_fastapi_routes.py
"""
import os, textwrap

WEB = os.path.join(os.path.dirname(__file__), "web")

def write(filename, content):
    path = os.path.join(WEB, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    print(f"  wrote {filename}")

# ─────────────────────────────────────────────────────────────────────────────
# ROUTE CONVERSION HELPERS  (pasted at top of every converted route file)
# ─────────────────────────────────────────────────────────────────────────────
_HDR = '''\
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)
'''

# =============================================================================
# auth_routes.py
# =============================================================================
write("auth_routes.py", _HDR + '''
from auth_data_store import auth_store, PRIVILEGE_LEVELS, PRIVILEGE_DESCRIPTIONS, MIN_PASSWORD_LENGTH
from extensions import limiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", name="auth_login")
async def login_get(request: Request):
    if request.session.get("logged_in"):
        return RedirectResponse("/auth/portal", status_code=302)
    ctx = template_context(request)
    return templates.TemplateResponse("auth/login.html", ctx)


@router.post("/login", name="auth_login_post")
async def login_post(request: Request):
    if request.session.get("logged_in"):
        return RedirectResponse("/auth/portal", status_code=302)
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    if not username or not password:
        flash(request, "Username and password are required", "error")
        return templates.TemplateResponse("auth/login.html", template_context(request))
    user = auth_store.authenticate(username, password)
    if user:
        auth_store.set_session(user, request.session)
        if request.headers.get("HX-Request"):
            return RedirectResponse("/auth/portal", status_code=303)
        flash(request, f"Welcome back, {user.get('full_name', user['username'])}!", "success")
        return RedirectResponse("/auth/portal", status_code=303)
    flash(request, "Invalid credentials or account locked", "error")
    return templates.TemplateResponse("auth/login.html", template_context(request))


@router.get("/logout", name="auth_logout")
async def logout(request: Request):
    username = request.session.get("username", "unknown")
    auth_store.clear_session(request.session)
    flash(request, "You have been logged out.", "info")
    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(request, module="auth", endpoint="/auth/logout",
                                    filename="", status="success", user=username,
                                    details="User logged out")
    except Exception:
        pass
    return RedirectResponse("/auth/login", status_code=302)


@router.get("/access-denied", name="auth_access_denied")
async def access_denied(request: Request):
    return templates.TemplateResponse("auth/access_denied.html", template_context(request))


@router.get("/register", name="auth_register_get")
async def register_get(request: Request):
    return templates.TemplateResponse("auth/register.html", template_context(request))


@router.post("/register", name="auth_register")
async def register_post(request: Request):
    form = await request.form()
    username  = form.get("username", "").strip()
    password  = form.get("password", "")
    confirm   = form.get("confirm_password", "")
    full_name = form.get("full_name", "").strip()
    email     = form.get("email", "").strip()
    phone     = form.get("phone", "").strip()
    errors = []
    if not username or len(username) < 3:
        errors.append("Username must be at least 3 characters")
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password != confirm:
        errors.append("Passwords do not match")
    if not full_name:
        errors.append("Full name is required")
    if not email:
        errors.append("Email is required")
    if errors:
        flash(request, "; ".join(errors), "error")
        return templates.TemplateResponse("auth/register.html", template_context(request))
    result = auth_store.create_user(username=username, password=password,
                                    full_name=full_name, email=email, phone=phone,
                                    privilege_level="viewer")
    if result["success"]:
        flash(request, "Account created successfully! Please login.", "success")
        return RedirectResponse("/auth/login", status_code=303)
    flash(request, result["error"], "error")
    return templates.TemplateResponse("auth/register.html", template_context(request))


@router.get("/portal", name="auth_portal")
async def portal(request: Request, user=Depends(login_required)):
    stats = auth_store.get_auth_stats()
    ctx = template_context(request)
    ctx.update(user=user, stats=stats,
               privilege_levels=PRIVILEGE_LEVELS,
               privilege_descriptions=PRIVILEGE_DESCRIPTIONS)
    return templates.TemplateResponse("auth/portal.html", ctx)


@router.get("/users", name="auth_user_management")
async def user_management(request: Request, user=Depends(admin_required)):
    from auth_data_store import auth_store
    users = auth_store.get_all_users()
    stats = auth_store.get_auth_stats()
    login_history = auth_store.get_login_history(limit=50)
    ctx = template_context(request)
    ctx.update(users=users, stats=stats, login_history=login_history,
               privilege_levels=PRIVILEGE_LEVELS,
               privilege_descriptions=PRIVILEGE_DESCRIPTIONS)
    return templates.TemplateResponse("auth/users.html", ctx)


@router.post("/users/create", name="auth_create_user")
async def create_user(request: Request, user=Depends(admin_required)):
    data = await request.json()
    result = auth_store.create_user(
        username=data.get("username", "").strip(),
        password=data.get("password", ""),
        full_name=data.get("full_name", "").strip(),
        email=data.get("email", "").strip(),
        phone=data.get("phone", "").strip(),
        privilege_level=data.get("privilege_level", "viewer"),
    )
    if result["success"]:
        return {"success": True, "message": "User created"}
    raise HTTPException(status_code=400, detail=result["error"])


@router.post("/users/{user_id}/update", name="auth_update_user")
async def update_user(user_id: str, request: Request, user=Depends(admin_required)):
    data = await request.json()
    allowed = ["full_name", "email", "phone", "privilege_level", "is_active"]
    updates = {k: data[k] for k in allowed if k in data}
    result = auth_store.update_user(user_id, updates)
    return {"success": result}


@router.post("/users/{user_id}/reset-password", name="auth_reset_password")
async def reset_password(user_id: str, request: Request, user=Depends(admin_required)):
    data = await request.json()
    result = auth_store.reset_password(user_id, data.get("new_password", ""))
    return {"success": result}


@router.post("/users/{user_id}/toggle-active", name="auth_toggle_active")
async def toggle_active(user_id: str, request: Request, user=Depends(admin_required)):
    result = auth_store.toggle_user_active(user_id)
    return {"success": result}


@router.post("/users/{user_id}/delete", name="auth_delete_user")
async def delete_user(user_id: str, request: Request, user=Depends(super_admin_required)):
    if user_id == request.session.get("user_id"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    result = auth_store.delete_user(user_id)
    return {"success": result}


@router.post("/change-password", name="auth_change_password")
async def change_password(request: Request, user=Depends(login_required)):
    form = await request.form()
    current  = form.get("current_password", "")
    new_pwd  = form.get("new_password", "")
    confirm  = form.get("confirm_password", "")
    if new_pwd != confirm:
        flash(request, "New passwords do not match", "error")
        return RedirectResponse("/auth/portal", status_code=303)
    result = auth_store.change_password(request.session.get("user_id"), current, new_pwd)
    if result["success"]:
        flash(request, "Password changed successfully", "success")
    else:
        flash(request, result.get("error", "Failed"), "error")
    return RedirectResponse("/auth/portal", status_code=303)


@router.get("/api/login-history", name="auth_api_login_history")
async def api_login_history(request: Request, user=Depends(admin_required)):
    return auth_store.get_login_history(limit=100)


@router.get("/api/stats", name="auth_api_stats")
async def api_stats(request: Request, user=Depends(admin_required)):
    return auth_store.get_auth_stats()


@router.get("/api/tokens", name="auth_list_tokens")
async def list_tokens(request: Request, user=Depends(login_required)):
    return auth_store.get_user_tokens(request.session.get("user_id"))


@router.post("/api/tokens", name="auth_create_token")
async def create_token(request: Request, user=Depends(login_required)):
    data = await request.json()
    result = auth_store.create_api_token(
        request.session.get("user_id"),
        label=data.get("label", "API Token"),
        expires_days=data.get("expires_days"),
    )
    return result


@router.delete("/api/tokens/{token_id}", name="auth_revoke_token")
async def revoke_token(token_id: str, request: Request, user=Depends(login_required)):
    result = auth_store.revoke_token(token_id, owner_id=request.session.get("user_id"))
    return {"success": result}
''')

# =============================================================================
# sales_routes.py
# =============================================================================
write("sales_routes.py", _HDR + '''
router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/", name="sales_landing")
@router.get("", name="sales_landing_noslash")
async def landing(request: Request):
    return templates.TemplateResponse("sales/index.html", template_context(request))


@router.post("/contact", name="sales_contact")
async def contact(request: Request):
    form = await request.form()
    name    = form.get("name", "").strip()
    email   = form.get("email", "").strip()
    company = form.get("company", "").strip()
    tier    = form.get("tier", "").strip()
    message = form.get("message", "").strip()
    if not name or not email:
        flash(request, "Please provide your name and email.", "danger")
        return RedirectResponse("/sales/#contact", status_code=303)
    try:
        from sales_data_store import sales_store
        sales_store.save_contact({
            "name": name, "email": email, "company": company,
            "tier": tier, "message": message,
            "ip_address": request.client.host if request.client else "",
        })
    except Exception as e:
        logger.error("Could not save sales contact: %s", e)
    flash(request, "Thank you! We will be in touch.", "success")
    return RedirectResponse("/sales/#contact", status_code=303)
''')

# =============================================================================
# provider_admin_routes.py
# =============================================================================
write("provider_admin_routes.py", _HDR + '''
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
''')

# =============================================================================
# siem_routes.py
# =============================================================================
write("siem_routes.py", _HDR + '''
from siem_data_store import siem_store

router = APIRouter(prefix="/siem", tags=["siem"])


@router.get("/", name="siem_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(
        stats=siem_store.get_dashboard_stats(),
        alerts=siem_store.get_alerts(acknowledged=False, limit=10),
        alert_counts=siem_store.get_alert_counts(),
        recent_events=siem_store.get_all_events(limit=15),
    )
    return templates.TemplateResponse("siem/dashboard.html", ctx)


@router.get("/events", name="siem_event_log")
async def event_log(request: Request, user=Depends(login_required)):
    ip_f      = request.query_params.get("ip", "")
    module_f  = request.query_params.get("module", "")
    status_f  = request.query_params.get("status", "")
    start_d   = request.query_params.get("start_date", "")
    end_d     = request.query_params.get("end_date", "")
    if start_d or end_d:
        events = siem_store.get_events_by_date_range(start_d, end_d)
    elif ip_f:
        events = siem_store.get_events_by_ip(ip_f)
    elif module_f:
        events = siem_store.get_events_by_module(module_f)
    elif status_f:
        events = siem_store.get_events_by_status(status_f)
    else:
        events = siem_store.get_all_events(limit=500)
    if ip_f and (start_d or end_d):
        events = [e for e in events if e.get("ip_address") == ip_f]
    if module_f and events:
        events = [e for e in events if e.get("module") == module_f]
    if status_f and events:
        events = [e for e in events if e.get("status") == status_f]
    ctx = template_context(request)
    ctx.update(events=events, ip_filter=ip_f, module_filter=module_f,
               status_filter=status_f, start_date=start_d, end_date=end_d)
    return templates.TemplateResponse("siem/events.html", ctx)


@router.get("/events/{event_id}", name="siem_event_detail")
async def event_detail(event_id: str, request: Request, user=Depends(login_required)):
    event = siem_store.get_event_by_id(event_id)
    if not event:
        flash(request, "Event not found", "danger")
        return RedirectResponse("/siem/events", status_code=302)
    ctx = template_context(request)
    ctx.update(event=event)
    return templates.TemplateResponse("siem/event_detail.html", ctx)


@router.get("/ips", name="siem_ip_tracker")
async def ip_tracker(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(ip_summary=siem_store.get_ip_summary())
    return templates.TemplateResponse("siem/ip_tracker.html", ctx)


@router.get("/alerts", name="siem_alerts")
async def alerts(request: Request, user=Depends(login_required)):
    show = request.query_params.get("show", "unacknowledged")
    if show == "all":
        alert_list = siem_store.get_alerts(acknowledged=None)
    elif show == "acknowledged":
        alert_list = siem_store.get_alerts(acknowledged=True)
    else:
        alert_list = siem_store.get_alerts(acknowledged=False)
    ctx = template_context(request)
    ctx.update(alerts=alert_list, alert_counts=siem_store.get_alert_counts(), show=show)
    return templates.TemplateResponse("siem/alerts.html", ctx)
''')

# =============================================================================
# version_routes.py
# =============================================================================
write("version_routes.py", _HDR + '''
router = APIRouter(prefix="/version", tags=["version"])

def _mgr():
    from version_data_store import version_manager
    return version_manager


@router.get("/", name="version_dashboard")
@router.get("/dashboard", name="version_dashboard_alt")
async def dashboard(request: Request, user=Depends(login_required)):
    mgr = _mgr()
    ctx = template_context(request)
    ctx.update(versions=mgr.list_versions(), current_version=mgr.get_current_version(),
               changelog=mgr.get_changelog())
    return templates.TemplateResponse("version/dashboard.html", ctx)


@router.get("/create", name="version_create_version_get")
async def create_version_get(request: Request, user=Depends(admin_required)):
    mgr = _mgr()
    current = mgr.get_current_version()
    parts = current.split(".")
    try:
        suggested = f"{parts[0]}.{int(parts[1]) + 1}.0"
    except (IndexError, ValueError):
        suggested = "1.1.0"
    ctx = template_context(request)
    ctx.update(current_version=current, suggested_version=suggested)
    return templates.TemplateResponse("version/create.html", ctx)


@router.post("/create", name="version_create_version")
async def create_version_post(request: Request, user=Depends(admin_required)):
    form = await request.form()
    version_str = form.get("version", "").strip()
    description = form.get("description", "").strip()
    create_snapshot = form.get("create_snapshot", "on") == "on"
    if not version_str:
        flash(request, "Version number is required.", "danger")
        return RedirectResponse("/version/create", status_code=303)
    result = _mgr().create_version(
        version=version_str, description=description,
        released_by=request.session.get("username", "admin"),
        create_snapshot=create_snapshot,
    )
    if result.get("success"):
        flash(request, f"Version {version_str} released!", "success")
        return RedirectResponse("/version/", status_code=303)
    flash(request, f"Failed: {result.get('error')}", "danger")
    return RedirectResponse("/version/create", status_code=303)


@router.get("/rollback/{version}", name="version_rollback_get")
async def rollback_get(version: str, request: Request, user=Depends(admin_required)):
    target = _mgr().get_version(version)
    if not target:
        flash(request, f"Version {version} not found.", "danger")
        return RedirectResponse("/version/", status_code=302)
    ctx = template_context(request)
    ctx.update(target=target)
    return templates.TemplateResponse("version/rollback.html", ctx)


@router.post("/rollback/{version}", name="version_rollback")
async def rollback_post(version: str, request: Request, user=Depends(admin_required)):
    result = _mgr().rollback_to_version(
        version, performed_by=request.session.get("username", "admin")
    )
    if result.get("success"):
        flash(request, f"Rolled back to v{version}. {result.get('restored_files', 0)} files restored.", "success")
    else:
        flash(request, f"Rollback failed: {result.get('error')}", "danger")
    return RedirectResponse("/version/", status_code=303)
''')

# =============================================================================
# backup_routes.py
# =============================================================================
write("backup_routes.py", _HDR + '''
from backup_data_store import backup_engine, backup_scheduler
from fastapi.responses import FileResponse as _FR
import os

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/dashboard", name="backup_dashboard")
async def dashboard(request: Request, user=Depends(require_auth("operator"))):
    ctx = template_context(request)
    ctx.update(
        stats=backup_engine.get_stats(),
        backups=backup_engine.list_backups(),
        log=backup_engine.get_backup_log(limit=20),
        scheduler_running=backup_scheduler.is_running,
        next_run=backup_scheduler.next_run,
    )
    return templates.TemplateResponse("backup/dashboard.html", ctx)


@router.post("/create", name="backup_create_backup")
async def create_backup(request: Request, user=Depends(admin_required)):
    form = await request.form()
    label = form.get("label", "").strip() or None
    username = request.session.get("username", "unknown")
    result = backup_engine.create_backup(label=label, triggered_by=f"manual:{username}")
    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(request, module="backup", endpoint="/backup/create",
                                    filename=result.get("archive_name", ""),
                                    status="success" if result.get("success") else "failed",
                                    user=username)
    except Exception:
        pass
    if result.get("success"):
        flash(request, f"Backup created: {result['archive_name']}", "success")
    else:
        flash(request, f"Backup failed: {result.get('error', 'Unknown')}", "error")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.get("/details/{archive_name}", name="backup_backup_details")
async def backup_details(archive_name: str, request: Request, user=Depends(require_auth("operator"))):
    details = backup_engine.get_backup_details(archive_name)
    if not details:
        flash(request, "Backup not found", "error")
        return RedirectResponse("/backup/dashboard", status_code=302)
    ctx = template_context(request)
    ctx.update(details=details)
    return templates.TemplateResponse("backup/details.html", ctx)


@router.get("/download/{archive_name}", name="backup_download_backup")
async def download_backup(archive_name: str, request: Request, user=Depends(admin_required)):
    archive_path = os.path.join(backup_engine.backup_dir, archive_name)
    if not os.path.exists(archive_path) or not archive_name.endswith(".zip"):
        flash(request, "Archive not found", "error")
        return RedirectResponse("/backup/dashboard", status_code=302)
    return _FR(archive_path, filename=archive_name, media_type="application/zip")


@router.post("/restore/{archive_name}", name="backup_restore_backup")
async def restore_backup(archive_name: str, request: Request, user=Depends(super_admin_required)):
    form = await request.form()
    confirm = form.get("confirm") == "yes"
    result = backup_engine.restore_backup(archive_name, confirm=confirm)
    if result.get("success"):
        flash(request, f"Restored {result['restored_files']} files from {archive_name}.", "success")
    else:
        flash(request, f"Restore failed: {result.get('error', 'Unknown')}", "error")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.post("/delete/{archive_name}", name="backup_delete_backup")
async def delete_backup(archive_name: str, request: Request, user=Depends(admin_required)):
    if backup_engine.delete_backup(archive_name):
        flash(request, f"Deleted {archive_name}", "success")
    else:
        flash(request, "Archive not found", "error")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.post("/purge", name="backup_purge_old")
async def purge_old(request: Request, user=Depends(admin_required)):
    form = await request.form()
    keep = int(form.get("keep_count", 10))
    result = backup_engine.purge_old_backups(keep_count=keep)
    flash(request, f"Purged {result.get('deleted', 0)} old backups.", "success")
    return RedirectResponse("/backup/dashboard", status_code=303)
''')

# =============================================================================
# bid_routes.py
# =============================================================================
write("bid_routes.py", _HDR + '''
import os
from bid_data_store import bid_store

ALLOWED_EXT = {
    "pdf","doc","docx","xls","xlsx","ppt","pptx",
    "txt","csv","zip","rar","7z","jpg","jpeg","png","gif","bmp","svg",
}

def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

router = APIRouter(prefix="/bid", tags=["bid"])


@router.get("/", name="bid_dashboard")
@router.get("/dashboard", name="bid_dashboard_alt")
async def dashboard(request: Request, user=Depends(login_required)):
    stats = bid_store.get_summary_stats()
    bids  = bid_store.get_all_bids()
    bids.reverse()
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
        "case_handler_email","notes",
    ]}
    data["status"]     = data.get("status") or "Draft"
    data["bid_amount"] = float(form.get("bid_amount", 0) or 0)
    data["reminder_days_before"] = int(form.get("reminder_days_before", 3) or 3)
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
    doc_groups = {t: [] for t in ["original_bid","technical","financial","supporting","other"]}
    for doc in bid.get("documents", []):
        dt = doc.get("doc_type", "other")
        doc_groups.get(dt, doc_groups["other"]).append(doc)
    ctx = template_context(request)
    ctx.update(bid=bid, doc_groups=doc_groups)
    return templates.TemplateResponse("bid/view_bid.html", ctx)


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
        "case_handler_name","case_handler_email","notes",
    ]})
    bid["bid_amount"] = float(form.get("bid_amount", 0) or 0)
    bid["reminder_days_before"] = int(form.get("reminder_days_before", 3) or 3)
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
    _file = form.get("file")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    if not _allowed(_file.filename):  # type: ignore[union-attr]
        flash(request, "File type not allowed", "error")
        return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)
    doc_id = bid_store.save_document(
        bid_id, _file,  # type: ignore[arg-type]
        form.get("doc_type", "other"),
        form.get("description", "").strip(),
        form.get("uploaded_by", "").strip(),
    )
    if doc_id:
        flash(request, f"Document uploaded!", "success")
    else:
        flash(request, "Error uploading document", "error")
    return RedirectResponse(f"/bid/view/{bid_id}", status_code=303)


@router.get("/download/{bid_id}/{doc_id}", name="bid_download_document")
async def download_document(bid_id: str, doc_id: str, request: Request, user=Depends(login_required)):
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
''')

# =============================================================================
# cpo_routes.py
# =============================================================================
write("cpo_routes.py", _HDR + '''
import pandas as pd
from datetime import datetime
from cpo_data_store import CPODataStore
from siem_data_store import siem_store

router = APIRouter(prefix="/cpo", tags=["cpo"])
cpo_store = CPODataStore(data_dir="data")


@router.get("/", name="cpo_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    recent = cpo_store.get_all_cpos()[-10:]
    recent.reverse()
    history = cpo_store.get_import_history()[-5:]
    history.reverse()
    ctx.update(summary=cpo_store.get_summary(), recent_cpos=recent, import_history=history)
    return templates.TemplateResponse("cpo/dashboard.html", ctx)


@router.get("/import", name="cpo_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("cpo/import.html", template_context(request))


@router.post("/import", name="cpo_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("file")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/cpo/import", status_code=303)
    if not _file.filename.lower().endswith((".xlsx", ".xls")):  # type: ignore[union-attr]
        flash(request, "Please upload an Excel file", "error")
        return RedirectResponse("/cpo/import", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        import io
        df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        if df.empty:
            flash(request, "The file contains no data", "error")
            return RedirectResponse("/cpo/import", status_code=303)
        result = cpo_store.import_from_dataframe(df, _file.filename)
        siem_store.log_upload_event(request, module="cpo", endpoint="/cpo/import",
                                    filename=_file.filename,
                                    records_imported=result.get("imported", 0),
                                    status="success")
        ctx = template_context(request)
        ctx.update(result=result, filename=_file.filename)
        return templates.TemplateResponse("cpo/import_result.html", ctx)
    except Exception as e:
        flash(request, f"Error reading file: {e}", "error")
        return RedirectResponse("/cpo/import", status_code=303)


@router.get("/list", name="cpo_cpo_list")
async def cpo_list(request: Request, user=Depends(login_required)):
    records = cpo_store.get_all_cpos()
    records.reverse()
    ctx = template_context(request)
    ctx.update(records=records, summary=cpo_store.get_summary())
    return templates.TemplateResponse("cpo/cpo_list.html", ctx)


@router.get("/add", name="cpo_add_cpo_get")
async def add_cpo_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("cpo/add_cpo.html", {**template_context(request), "record": {}})


@router.post("/add", name="cpo_add_cpo")
async def add_cpo_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    is_returned   = form.get("is_returned", "No")
    returned_date = form.get("returned_date", "").strip()
    if is_returned == "Yes" and not returned_date:
        flash(request, "Returned date is required when CPO is marked as returned", "error")
        return templates.TemplateResponse("cpo/add_cpo.html",
                                          {**template_context(request), "record": dict(form)})
    record = {
        "name":          form.get("name", "").strip(),
        "date":          form.get("date", datetime.now().strftime("%Y-%m-%d")),
        "amount":        float(form.get("amount", 0) or 0),
        "bid_name":      form.get("bid_name", "").strip(),
        "is_returned":   is_returned,
        "returned_date": returned_date if is_returned == "Yes" else "",
    }
    if not record["name"]:
        flash(request, "Name is required", "error")
        return templates.TemplateResponse("cpo/add_cpo.html", {**template_context(request), "record": record})
    if cpo_store.save_cpo(record):
        flash(request, "CPO record added!", "success")
        return RedirectResponse("/cpo/list", status_code=303)
    flash(request, "Error saving CPO record", "error")
    return templates.TemplateResponse("cpo/add_cpo.html", {**template_context(request), "record": record})


@router.get("/edit/{cpo_id}", name="cpo_edit_cpo_get")
async def edit_cpo_get(cpo_id: str, request: Request, user=Depends(login_required)):
    record = cpo_store.get_cpo_by_id(cpo_id)
    if not record:
        flash(request, "CPO record not found", "error")
        return RedirectResponse("/cpo/list", status_code=302)
    return templates.TemplateResponse("cpo/edit_cpo.html", {**template_context(request), "record": record})


@router.post("/edit/{cpo_id}", name="cpo_edit_cpo")
async def edit_cpo_post(cpo_id: str, request: Request, user=Depends(login_required)):
    record = cpo_store.get_cpo_by_id(cpo_id)
    if not record:
        flash(request, "CPO record not found", "error")
        return RedirectResponse("/cpo/list", status_code=302)
    form = await request.form()
    is_returned   = form.get("is_returned", "No")
    returned_date = form.get("returned_date", "").strip()
    if is_returned == "Yes" and not returned_date:
        flash(request, "Returned date is required", "error")
        return templates.TemplateResponse("cpo/edit_cpo.html", {**template_context(request), "record": record})
    updates = {
        "name":          form.get("name", "").strip(),
        "date":          form.get("date", ""),
        "amount":        float(form.get("amount", 0) or 0),
        "bid_name":      form.get("bid_name", "").strip(),
        "is_returned":   is_returned,
        "returned_date": returned_date if is_returned == "Yes" else "",
    }
    if cpo_store.update_cpo(cpo_id, updates):
        flash(request, "CPO updated!", "success")
        return RedirectResponse("/cpo/list", status_code=303)
    flash(request, "Error updating CPO", "error")
    record.update(updates)
    return templates.TemplateResponse("cpo/edit_cpo.html", {**template_context(request), "record": record})


@router.post("/delete/{cpo_id}", name="cpo_delete_cpo")
async def delete_cpo(cpo_id: str, request: Request, user=Depends(login_required)):
    cpo_store.delete_cpo(cpo_id)
    flash(request, "CPO deleted", "success")
    return RedirectResponse("/cpo/list", status_code=303)


@router.get("/export", name="cpo_export_excel")
async def export_excel(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    filepath = cpo_store.export_to_excel()
    if filepath:
        fname = f"cpo_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    flash(request, "Export failed", "error")
    return RedirectResponse("/cpo/list", status_code=302)
''')

# =============================================================================
# journal_entry_routes.py
# =============================================================================
write("journal_entry_routes.py", _HDR + '''
import tempfile
import os
from datetime import datetime

from journal_entry_data_store import JournalEntryDataStore

router = APIRouter(prefix="/journal", tags=["journal"])
journal_store = JournalEntryDataStore()


@router.get("/", name="journal_entries_journal_list")
async def journal_list(request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    start_date = request.query_params.get("start_date")
    end_date   = request.query_params.get("end_date")
    from datetime import datetime as _dt
    start_obj = _dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end_obj   = _dt.strptime(end_date,   "%Y-%m-%d").date() if end_date   else None
    df = journal_store.read_journal_entries(company_id, start_obj, end_obj)
    ctx = template_context(request)
    ctx.update(
        entries=df.to_dict("records") if not df.empty else [],
        total_entries=len(df),
        total_debits=df["total_debit"].sum() if not df.empty else 0,
        total_credits=df["total_credit"].sum() if not df.empty else 0,
        filters={"company_id": company_id, "start_date": start_date, "end_date": end_date},
    )
    return templates.TemplateResponse("journal_entries/list.html", ctx)


@router.get("/view/{entry_id}", name="journal_entries_view_entry")
async def view_entry(entry_id: str, request: Request, user=Depends(login_required)):
    df = journal_store.read_journal_entries()
    entry_df = df[df["entry_id"] == entry_id]
    if entry_df.empty:
        flash(request, "Journal entry not found", "error")
        return RedirectResponse("/journal/", status_code=302)
    lines_df = journal_store.read_entry_lines(entry_id)
    ctx = template_context(request)
    ctx.update(entry=entry_df.iloc[0].to_dict(), lines=lines_df.to_dict("records"))
    return templates.TemplateResponse("journal_entries/view.html", ctx)


@router.get("/add", name="journal_entries_add_entry_get")
async def add_entry_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("journal_entries/add.html", template_context(request))


@router.post("/add", name="journal_entries_add_entry")
async def add_entry_post(request: Request, user=Depends(login_required)):
    try:
        data = await request.json()
        from datetime import datetime as _dt
        entry_data = {
            "company_id":       data.get("company_id", "default"),
            "entry_date":       _dt.strptime(data.get("entry_date"), "%Y-%m-%d").date(),
            "description":      data.get("description"),
            "reference_number": data.get("reference_number", ""),
        }
        lines_data, total_debit, total_credit = [], 0.0, 0.0
        for line in data.get("lines", []):
            d = float(line.get("debit_amount", 0))
            c = float(line.get("credit_amount", 0))
            lines_data.append({
                "account_code":  line.get("account_code"),
                "account_name":  line.get("account_name", ""),
                "description":   line.get("description", entry_data["description"]),
                "debit_amount":  d,
                "credit_amount": c,
            })
            total_debit  += d
            total_credit += c
        entry_data["total_debit"]  = total_debit
        entry_data["total_credit"] = total_credit
        if abs(total_debit - total_credit) > 0.01:
            return {"success": False, "error": "Debits must equal credits"}
        entry_id = journal_store.save_journal_entry(entry_data, lines_data)
        return {"success": True, "entry_id": entry_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/export/excel", name="journal_entries_export_excel")
async def export_excel(request: Request, company_id: str = None, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = journal_store.export_to_excel(company_id)
        fname = f"journal_entries_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Export failed: {e}", "error")
        return RedirectResponse("/journal/", status_code=302)


@router.get("/import/excel", name="journal_entries_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("journal_entries/import_excel.html", template_context(request))


@router.post("/import/excel", name="journal_entries_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("excel_file")
    company_id = form.get("company_id", "default")
    if not _file or not _file.filename:  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/journal/import/excel", status_code=303)
    if not _file.filename.lower().endswith((".xlsx", ".xls")):  # type: ignore[union-attr]
        flash(request, "Please upload a valid Excel file", "error")
        return RedirectResponse("/journal/import/excel", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = journal_store.import_from_excel(tmp_path, company_id)
        os.unlink(tmp_path)
        if result["success"]:
            flash(request, f"Imported {result['imported_count']} entries!", "success")
        else:
            flash(request, "Import failed.", "error")
    except Exception as e:
        flash(request, f"Import failed: {e}", "error")
    return RedirectResponse("/journal/", status_code=303)


@router.get("/download/sample", name="journal_entries_download_sample")
async def download_sample(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = journal_store.create_sample_excel_file()
        return _FR(filepath, filename="journal_entries_sample_data.xlsx",
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Sample download failed: {e}", "error")
        return RedirectResponse("/journal/", status_code=302)
''')

# =============================================================================
# chart_of_accounts_routes.py
# =============================================================================
write("chart_of_accounts_routes.py", _HDR + '''
import tempfile
import os
from datetime import datetime

from chart_of_accounts_data_store import ChartOfAccountsDataStore

router = APIRouter(prefix="/accounts", tags=["accounts"])
accounts_store = ChartOfAccountsDataStore()


@router.get("/", name="accounts_accounts_list")
async def accounts_list(request: Request, user=Depends(login_required)):
    company_id   = request.query_params.get("company_id", "default")
    account_type = request.query_params.get("account_type")
    df = accounts_store.read_all_accounts(company_id)
    if account_type and not df.empty:
        df = df[df["account_type"] == account_type]
    accounts_by_type, type_totals = {}, {}
    if not df.empty:
        for at in df["account_type"].unique():
            subset = df[df["account_type"] == at]
            accounts_by_type[at] = subset.to_dict("records")
            type_totals[at] = float(subset["current_balance"].sum())
    ctx = template_context(request)
    ctx.update(accounts_by_type=accounts_by_type, type_totals=type_totals,
               total_accounts=len(df), filters={"company_id": company_id, "account_type": account_type})
    return templates.TemplateResponse("accounts/list.html", ctx)


@router.get("/dashboard", name="accounts_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    df = accounts_store.read_all_accounts(company_id)
    stats = {
        "total_accounts":    len(df),
        "active_accounts":   len(df[df["is_active"]]) if not df.empty else 0,
        "asset_accounts":    len(df[df["account_type"] == "Asset"]) if not df.empty else 0,
        "liability_accounts":len(df[df["account_type"] == "Liability"]) if not df.empty else 0,
        "equity_accounts":   len(df[df["account_type"] == "Equity"]) if not df.empty else 0,
        "revenue_accounts":  len(df[df["account_type"] == "Revenue"]) if not df.empty else 0,
        "expense_accounts":  len(df[df["account_type"] == "Expense"]) if not df.empty else 0,
    }
    balance_summary = {}
    for at in ["Asset", "Liability", "Equity", "Revenue", "Expense"]:
        subset = df[df["account_type"] == at] if not df.empty else df
        balance_summary[at] = float(subset["current_balance"].sum()) if not subset.empty else 0
    recent = df.sort_values("created_date", ascending=False).head(10).to_dict("records") if not df.empty else []
    ctx = template_context(request)
    ctx.update(stats=stats, balance_summary=balance_summary, recent_accounts=recent, company_id=company_id)
    return templates.TemplateResponse("accounts/dashboard.html", ctx)


@router.get("/view/{account_code}", name="accounts_view_account")
async def view_account(account_code: str, request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    account = accounts_store.get_account_by_code(account_code, company_id)
    if not account:
        flash(request, "Account not found", "error")
        return RedirectResponse("/accounts/", status_code=302)
    return templates.TemplateResponse("accounts/view.html", {**template_context(request), "account": account})


@router.get("/add", name="accounts_add_account_get")
async def add_account_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("accounts/add.html", template_context(request))


@router.post("/add", name="accounts_add_account")
async def add_account_post(request: Request, user=Depends(login_required)):
    data = await request.json()
    account_data = {
        "account_code":    data.get("account_code"),
        "account_name":    data.get("account_name"),
        "account_type":    data.get("account_type"),
        "account_subtype": data.get("account_subtype", ""),
        "parent_account":  data.get("parent_account", ""),
        "description":     data.get("description", ""),
        "normal_balance":  data.get("normal_balance", "Debit"),
        "current_balance": float(data.get("current_balance", 0)),
        "company_id":      data.get("company_id", "default"),
        "is_active":       True,
    }
    if accounts_store.save_account(account_data):
        return {"success": True, "account_code": account_data["account_code"]}
    raise HTTPException(status_code=400, detail="Failed to save account")


@router.get("/edit/{account_code}", name="accounts_edit_account_get")
async def edit_account_get(account_code: str, request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    account = accounts_store.get_account_by_code(account_code, company_id)
    if not account:
        flash(request, "Account not found", "error")
        return RedirectResponse("/accounts/", status_code=302)
    return templates.TemplateResponse("accounts/edit.html", {**template_context(request), "account": account})


@router.post("/edit/{account_code}", name="accounts_edit_account")
async def edit_account_post(account_code: str, request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    account = accounts_store.get_account_by_code(account_code, company_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    data = await request.json()
    account.update({
        "account_name":    data.get("account_name", account["account_name"]),
        "account_type":    data.get("account_type", account["account_type"]),
        "current_balance": float(data.get("current_balance", account["current_balance"])),
    })
    if accounts_store.save_account(account):
        return {"success": True}
    raise HTTPException(status_code=400, detail="Failed to update account")


@router.get("/export/excel", name="accounts_export_excel")
async def export_excel(request: Request, company_id: str = None, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    try:
        filepath = accounts_store.export_to_excel(company_id)
        fname = f"chart_of_accounts_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        flash(request, f"Export failed: {e}", "error")
        return RedirectResponse("/accounts/", status_code=302)


@router.get("/import/excel", name="accounts_import_excel_get")
async def import_excel_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("accounts/import_excel.html", template_context(request))


@router.post("/import/excel", name="accounts_import_excel")
async def import_excel_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    _file = form.get("excel_file")
    company_id = form.get("company_id", "default")
    if not _file or not getattr(_file, "filename", None):  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/accounts/import/excel", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = accounts_store.import_from_excel(tmp_path, company_id)
        os.unlink(tmp_path)
        if result["success"]:
            flash(request, f"Imported {result['imported_count']} accounts!", "success")
        else:
            flash(request, "Import failed.", "error")
    except Exception as e:
        flash(request, f"Import failed: {e}", "error")
    return RedirectResponse("/accounts/", status_code=303)


@router.get("/trial-balance", name="accounts_trial_balance")
async def trial_balance(request: Request, user=Depends(login_required)):
    company_id = request.query_params.get("company_id", "default")
    df = accounts_store.read_all_accounts(company_id)
    ctx = template_context(request)
    if df.empty:
        ctx.update(accounts=[], totals={"debit": 0, "credit": 0})
        return templates.TemplateResponse("accounts/trial_balance.html", ctx)
    active = df[df["is_active"] & (df["current_balance"] != 0)]
    tb_accounts, total_debits, total_credits = [], 0.0, 0.0
    for _, row in active.iterrows():
        balance = float(row["current_balance"])
        if row["normal_balance"] == "Debit":
            d = max(balance, 0); c = max(-balance, 0)
        else:
            c = max(balance, 0); d = max(-balance, 0)
        tb_accounts.append({**row.to_dict(), "debit_balance": d, "credit_balance": c})
        total_debits  += d
        total_credits += c
    ctx.update(accounts=tb_accounts, totals={"debit": total_debits, "credit": total_credits})
    return templates.TemplateResponse("accounts/trial_balance.html", ctx)
''')

print("Batch 1 done (auth, sales, provider, siem, version, backup, bid, cpo, journal, accounts)")
