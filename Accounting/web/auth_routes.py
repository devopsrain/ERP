from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

from async_auth_data_store import async_auth_store
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
@limiter.limit("10/minute")   # Middleware 2: max 10 login attempts per minute per IP
async def login_post(request: Request):
    if request.session.get("logged_in"):
        return RedirectResponse("/auth/portal", status_code=302)
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    if not username or not password:
        flash(request, "Username and password are required", "error")
        return templates.TemplateResponse("auth/login.html", template_context(request))

    # Use the async version of the authentication logic
    user = await async_auth_store.validate_credentials(username, password)
    ip_address = request.client.host if request.client else "unknown"
    company_id = request.session.get("current_company_id", "default")

    if user:
        await async_auth_store.log_login_event(username, ip_address, True, company_id)
        auth_store.set_session(user, request.session) # Session setting can remain sync for now
        if request.headers.get("HX-Request"):
            return RedirectResponse("/auth/portal", status_code=303)
        flash(request, f"Welcome back, {user.get('full_name', user['username'])}!", "success")
        return RedirectResponse("/auth/portal", status_code=303)

    await async_auth_store.log_login_event(username, ip_address, False, company_id)
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
