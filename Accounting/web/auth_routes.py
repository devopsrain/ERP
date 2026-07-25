from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

from async_auth_data_store import async_auth_store
from auth_data_store import (
    auth_store, PRIVILEGE_LEVELS, PRIVILEGE_DESCRIPTIONS,
    MIN_PASSWORD_LENGTH, PASSWORD_MAX_AGE_DAYS, validate_password,
)
from extensions import limiter
from db import run_sync

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", name="auth_login")
async def login_get(request: Request):
    if request.session.get("logged_in"):
        next_url = request.query_params.get("next", "/auth/portal")
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/auth/portal"
        return RedirectResponse(next_url, status_code=302)
    ctx = template_context(request)
    # Pass next URL to template so form can include it
    ctx["next_url"] = request.query_params.get("next", "")
    return templates.TemplateResponse("auth/login.html", ctx)


@router.post("/login", name="auth_login_post")
@limiter.limit("10/minute")   # Middleware 2: max 10 login attempts per minute per IP
async def login_post(request: Request):
    form = await request.form()
    next_url = form.get("next", "") or request.query_params.get("next", "/auth/portal")
    # Validate next_url is a safe relative path
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/auth/portal"
    
    if request.session.get("logged_in"):
        return RedirectResponse(next_url, status_code=302)
    
    username = form.get("username", "").strip()
    password = form.get("password", "")
    if not username or not password:
        flash(request, "Username and password are required", "error")
        ctx = template_context(request)
        ctx["next_url"] = next_url if next_url != "/auth/portal" else ""
        return templates.TemplateResponse("auth/login.html", ctx)

    # Use the async version of the authentication logic
    user = await async_auth_store.validate_credentials(username, password)
    ip_address = request.client.host if request.client else "unknown"
    company_id = request.session.get("current_company_id", "default")

    if user:
        await async_auth_store.log_login_event(username, ip_address, True, company_id)
        auth_store.set_session(user, request.session) # Session setting can remain sync for now
        # Password expiry check (AICC 6.5.2): warn, but do not block login
        try:
            if await run_sync(auth_store.is_password_expired, user["user_id"]):
                request.session["password_expired"] = True
                flash(request,
                      f"Your password is older than {PASSWORD_MAX_AGE_DAYS} days. "
                      "Please change it soon via the Change Password menu.",
                      "warning")
        except Exception as e:
            logger.warning("Password expiry check failed: %s", e)
        if request.headers.get("HX-Request"):
            return RedirectResponse(next_url, status_code=303)
        flash(request, f"Welcome back, {user.get('full_name', user['username'])}!", "success")
        return RedirectResponse(next_url, status_code=303)

    await async_auth_store.log_login_event(username, ip_address, False, company_id)
    # AICC 6.7: failed login against an admin/super_admin account raises a
    # severity-'high' SIEM alert (best-effort, never blocks the response)
    try:
        await run_sync(auth_store.alert_failed_admin_login, username, request)
    except Exception as e:
        logger.warning("Failed-admin-login alert failed: %s", e)
    flash(request, "Invalid credentials or account locked", "error")
    ctx = template_context(request)
    ctx["next_url"] = next_url if next_url != "/auth/portal" else ""
    return templates.TemplateResponse("auth/login.html", ctx)


@router.get("/logout", name="auth_logout")
async def logout(request: Request):
    username = request.session.get("username", "unknown")
    auth_store.clear_session(request.session)
    flash(request, "You have been logged out.", "info")
    try:
        from siem_data_store import siem_store
        await run_sync(
            siem_store.log_upload_event,
            request,
            module="auth",
            endpoint="/auth/logout",
            filename="",
            status="success",
            user=username,
            details="User logged out",
        )
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
    ok_pw, pw_error = validate_password(password)
    if not ok_pw:
        errors.append(pw_error)
    if password != confirm:
        errors.append("Passwords do not match")
    if not full_name:
        errors.append("Full name is required")
    if not email:
        errors.append("Email is required")
    if errors:
        flash(request, "; ".join(errors), "error")
        return templates.TemplateResponse("auth/register.html", template_context(request))
    result = await async_auth_store.create_user(
        username=username,
        password=password,
        full_name=full_name,
        email=email,
        phone=phone,
        privilege_level="viewer",
    )
    if result["success"]:
        flash(request, "Account created successfully! Please login.", "success")
        return RedirectResponse("/auth/login", status_code=303)
    flash(request, result["error"], "error")
    return templates.TemplateResponse("auth/register.html", template_context(request))


@router.get("/portal", name="auth_portal")
async def portal(request: Request):
    import time
    SESSION_WINDOW = 30 * 60  # 30 minutes in seconds

    logged_in = request.session.get("logged_in")
    login_time = request.session.get("login_time", 0)
    session_fresh = (time.time() - login_time) <= SESSION_WINDOW

    if not logged_in or not session_fresh:
        # Clear stale session so next login starts clean
        if logged_in and not session_fresh:
            request.session.clear()
        return RedirectResponse("/sales/", status_code=302)

    user = auth_store.get_current_user(request.session)
    stats = await async_auth_store.get_auth_stats()
    ctx = template_context(request)
    ctx.update(user=user, stats=stats,
               privilege_levels=PRIVILEGE_LEVELS,
               privilege_descriptions=PRIVILEGE_DESCRIPTIONS)
    return templates.TemplateResponse("auth/portal.html", ctx)


@router.get("/users", name="auth_user_management")
async def user_management(request: Request, user=Depends(admin_required)):
    users = await async_auth_store.get_all_users()
    stats = await async_auth_store.get_auth_stats()
    login_history = await async_auth_store.get_login_history(limit=50)
    ctx = template_context(request)
    ctx.update(users=users, stats=stats, login_history=login_history,
               privilege_levels=PRIVILEGE_LEVELS,
               privilege_descriptions=PRIVILEGE_DESCRIPTIONS)
    return templates.TemplateResponse("auth/users.html", ctx)


def _admin_alert(request: Request, action: str, target: str, details: str = ""):
    """Fire-and-forget SIEM alert + audit log for a sensitive admin action."""
    actor = request.session.get("username", "unknown")
    try:
        auth_store.log_admin_action(action, actor, target, request=request, details=details)
    except Exception as e:
        logger.warning("Admin action alert failed (%s): %s", action, e)


@router.post("/users/create", name="auth_create_user")
async def create_user(request: Request, user=Depends(admin_required)):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    ok_pw, pw_error = validate_password(password)
    if not ok_pw:
        # Return JSON the users.html modal understands (d.error)
        return {"success": False, "error": pw_error}
    result = await async_auth_store.create_user(
        username=username,
        password=password,
        full_name=data.get("full_name", "").strip(),
        email=data.get("email", "").strip(),
        phone=data.get("phone", "").strip(),
        privilege_level=data.get("privilege_level", "viewer"),
    )
    if result["success"]:
        await run_sync(_admin_alert, request, "user_created", username,
                       f"privilege_level={data.get('privilege_level', 'viewer')}")
        return {"success": True, "message": "User created"}
    return {"success": False, "error": result["error"]}


@router.post("/users/{user_id}/update", name="auth_update_user")
async def update_user(user_id: str, request: Request, user=Depends(admin_required)):
    data = await request.json()
    allowed = ["full_name", "email", "phone", "privilege_level", "is_active"]
    updates = {k: data[k] for k in allowed if k in data}
    result = await async_auth_store.update_user(user_id, **updates)
    if result and ("privilege_level" in updates or "is_active" in updates):
        changed = ", ".join(f"{k}={updates[k]}" for k in ("privilege_level", "is_active") if k in updates)
        await run_sync(_admin_alert, request, "role_change", user_id, changed)
    return {"success": result}


@router.post("/users/{user_id}/reset-password", name="auth_reset_password")
async def reset_password(user_id: str, request: Request, user=Depends(admin_required)):
    data = await request.json()
    # Accept both "new_password" (documented) and "password" (users.html modal)
    new_password = data.get("new_password") or data.get("password") or ""
    ok_pw, pw_error = validate_password(new_password)
    if not ok_pw:
        return {"success": False, "error": pw_error}
    result = await async_auth_store.reset_password(user_id, new_password)
    # reset_password now returns {'success': bool, 'error': str}
    if isinstance(result, dict):
        success, error = result.get("success", False), result.get("error", "")
    else:
        success, error = bool(result), ""
    if success:
        await run_sync(_admin_alert, request, "admin_password_reset", user_id)
        return {"success": True}
    return {"success": False, "error": error or "Password reset failed"}


@router.post("/users/{user_id}/toggle-active", name="auth_toggle_active")
async def toggle_active(user_id: str, request: Request, user=Depends(admin_required)):
    result = await async_auth_store.toggle_user_active(user_id)
    if result:
        await run_sync(_admin_alert, request, "user_toggle_active", user_id)
    return {"success": result}


@router.post("/users/{user_id}/delete", name="auth_delete_user")
async def delete_user(user_id: str, request: Request, user=Depends(super_admin_required)):
    if user_id == request.session.get("user_id"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    result = await async_auth_store.delete_user(user_id)
    if result:
        await run_sync(_admin_alert, request, "user_deleted", user_id)
    return {"success": result}


@router.post("/change-password", name="auth_change_password")
async def change_password(request: Request, user=Depends(login_required)):
    # Accept both JSON (base.html modal) and classic form posts
    is_json = "application/json" in request.headers.get("content-type", "")
    if is_json:
        data = await request.json()
    else:
        data = await request.form()
    current  = data.get("current_password", "")
    new_pwd  = data.get("new_password", "")
    confirm  = data.get("confirm_password", "")

    if new_pwd != confirm:
        if is_json:
            return {"success": False, "error": "New passwords do not match"}
        flash(request, "New passwords do not match", "error")
        return RedirectResponse("/auth/portal", status_code=303)

    ok_pw, pw_error = validate_password(new_pwd)
    if not ok_pw:
        if is_json:
            return {"success": False, "error": pw_error}
        flash(request, pw_error, "error")
        return RedirectResponse("/auth/portal", status_code=303)

    result = await async_auth_store.change_password(request.session.get("user_id"), current, new_pwd)
    if result["success"]:
        request.session.pop("password_expired", None)
        if is_json:
            return {"success": True}
        flash(request, "Password changed successfully", "success")
    else:
        if is_json:
            return {"success": False, "error": result.get("error", "Failed")}
        flash(request, result.get("error", "Failed"), "error")
    return RedirectResponse("/auth/portal", status_code=303)


@router.get("/api/login-history", name="auth_api_login_history")
async def api_login_history(request: Request, user=Depends(admin_required)):
    return await async_auth_store.get_login_history(limit=100)


@router.get("/api/stats", name="auth_api_stats")
async def api_stats(request: Request, user=Depends(admin_required)):
    return await async_auth_store.get_auth_stats()


@router.get("/api/tokens", name="auth_list_tokens")
async def list_tokens(request: Request, user=Depends(login_required)):
    return await async_auth_store.get_user_tokens(request.session.get("user_id"))


@router.post("/api/tokens", name="auth_create_token")
async def create_token(request: Request, user=Depends(login_required)):
    data = await request.json()
    result = await async_auth_store.create_api_token(
        request.session.get("user_id"),
        label=data.get("label", "API Token"),
        expires_days=data.get("expires_days"),
    )
    return result


@router.delete("/api/tokens/{token_id}", name="auth_revoke_token")
async def revoke_token(token_id: str, request: Request, user=Depends(login_required)):
    result = await async_auth_store.revoke_token(token_id, owner_id=request.session.get("user_id"))
    return {"success": result}


# ── Password Reset / Forgot Password ──────────────────────────────

@router.get("/forgot-password", name="auth_forgot_password")
async def forgot_password_get(request: Request):
    """Display the forgot password form."""
    if request.session.get("logged_in"):
        return RedirectResponse("/auth/portal", status_code=302)
    ctx = template_context(request)
    return templates.TemplateResponse("auth/forgot_password.html", ctx)


@router.post("/forgot-password", name="auth_forgot_password_post")
@limiter.limit("3/hour")  # Prevent abuse: max 3 reset requests per hour per IP
async def forgot_password_post(request: Request):
    """Process forgot password request and send reset email."""
    form = await request.form()
    email = form.get("email", "").strip()
    
    if not email:
        flash(request, "Please enter your email address", "error")
        return templates.TemplateResponse("auth/forgot_password.html", template_context(request))
    
    # Always show success message (don't reveal if email exists)
    flash(request, 
          "If an account exists with this email, you will receive password reset instructions shortly.", 
          "success")
    
    # Generate token
    token = await async_auth_store.create_password_reset_token(email)
    
    if token:
        # Send email
        reset_link = f"{request.base_url}auth/reset-password?token={token}"
        _send_password_reset_email(email, reset_link)
    
    return RedirectResponse("/auth/login", status_code=303)


@router.get("/reset-password", name="auth_reset_password")
async def reset_password_get(request: Request):
    """Display password reset form."""
    token = request.query_params.get("token", "")
    if not token:
        flash(request, "Invalid or missing reset token", "error")
        return RedirectResponse("/auth/login", status_code=302)
    
    # Validate token
    user = await async_auth_store.validate_reset_token(token)
    if not user:
        flash(request, "Invalid or expired reset token", "error")
        return RedirectResponse("/auth/forgot-password", status_code=302)
    
    ctx = template_context(request)
    ctx["token"] = token
    ctx["email"] = user.get("email", "")
    return templates.TemplateResponse("auth/reset_password.html", ctx)


@router.post("/reset-password", name="auth_reset_password_post")
async def reset_password_post(request: Request):
    """Process password reset."""
    form = await request.form()
    token = form.get("token", "")
    new_password = form.get("new_password", "")
    confirm_password = form.get("confirm_password", "")
    
    if not token or not new_password:
        flash(request, "Missing required fields", "error")
        return RedirectResponse("/auth/forgot-password", status_code=302)
    
    if new_password != confirm_password:
        flash(request, "Passwords do not match", "error")
        ctx = template_context(request)
        ctx["token"] = token
        return templates.TemplateResponse("auth/reset_password.html", ctx)
    
    ok_pw, pw_error = validate_password(new_password)
    if not ok_pw:
        flash(request, pw_error, "error")
        ctx = template_context(request)
        ctx["token"] = token
        return templates.TemplateResponse("auth/reset_password.html", ctx)
    
    # Reset password
    success = await async_auth_store.reset_password_with_token(token, new_password)
    
    if success:
        flash(request, "Password reset successful! You can now login with your new password.", "success")
        return RedirectResponse("/auth/login", status_code=303)
    else:
        flash(request, "Invalid or expired reset token", "error")
        return RedirectResponse("/auth/forgot-password", status_code=302)


def _send_password_reset_email(to_email: str, reset_link: str):
    """
    Send password reset email.
    Uses SMTP if configured, logs to console otherwise.
    """
    import os
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    from_email = os.environ.get("SMTP_FROM", "noreply@devopsrain.com")
    
    subject = "Password Reset Request - Ethiopian Business Suite"
    
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9;">
            <h2 style="color: #1a237e;">Password Reset Request</h2>
            <p>You requested a password reset for your Ethiopian Business Suite account.</p>
            <p>Click the button below to reset your password:</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_link}" 
                   style="background-color: #1a237e; color: white; padding: 12px 30px; 
                          text-decoration: none; border-radius: 5px; display: inline-block;">
                    Reset Password
                </a>
            </div>
            <p style="color: #666; font-size: 14px;">
                Or copy and paste this link into your browser:<br>
                <a href="{reset_link}">{reset_link}</a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This link will expire in 1 hour.
            </p>
            <p style="color: #666; font-size: 14px;">
                If you didn't request this password reset, please ignore this email.
            </p>
            <hr style="border: 1px solid #ddd; margin: 30px 0;">
            <p style="color: #999; font-size: 12px; text-align: center;">
                DevOpsRain Technologies CC<br>
                Ethiopian Business Management System
            </p>
        </div>
    </body>
    </html>
    """
    
    text_body = f"""
Password Reset Request

You requested a password reset for your Ethiopian Business Suite account.

Click this link to reset your password:
{reset_link}

This link will expire in 1 hour.

If you didn't request this password reset, please ignore this email.

---
DevOpsRain Technologies CC
Ethiopian Business Management System
    """
    
    if smtp_host and smtp_user:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = to_email
            
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
            
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            
            logger.info("Password reset email sent to %s", to_email)
        except Exception as e:
            logger.error("Failed to send password reset email: %s", e)
            # Don't raise - we still show success message to user
    else:
        # Development mode - log to console
        logger.warning(
            "SMTP not configured. Password reset link for %s:\n%s", 
            to_email, reset_link
        )
        print(f"\n{'='*80}")
        print(f"PASSWORD RESET EMAIL (dev mode - SMTP not configured)")
        print(f"To: {to_email}")
        print(f"Reset Link: {reset_link}")
        print(f"{'='*80}\n")
