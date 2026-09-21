"""
Customer & Supplier Portal — routes (prefix /portal).

Public   : /portal/login, /portal/logout, /portal/forgot, /portal/reset,
           /portal/accept-invite
Portal   : /portal/ (home), /portal/profile, /portal/tickets/...
Customer : /portal/customer/invoices, /orders, /projects
Supplier : /portal/supplier/orders, /invoices, /documents, /rfqs
Staff    : /portal/admin/...  (staff session REQUIRED via deps.login_required /
           admin_required — the global login middleware skips /portal/, so the
           dependency is the only gate; staff CSRF is verified explicitly too.)

Every portal response carries Cache-Control: no-store.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

import portal_auth as pa
from deps import (admin_required, current_company, login_required, make_url_for,
                  template_context, validate_upload)
from portal_data_store import (DOCUMENT_KINDS, DOCUMENT_STATUSES, RFQ_STATUSES,
                               TICKET_PRIORITIES, TICKET_STATUSES, USER_KINDS,
                               portal_store, upload_root)
from template_engine import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portal", tags=["portal"])

_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate, private", "Pragma": "no-cache"}
_UPLOAD_EXTS = ("pdf", "png", "jpg", "jpeg", "gif", "webp", "xlsx", "xls", "csv",
                "docx", "doc", "txt", "zip")
_UPLOAD_MAX_BYTES = int(os.environ.get("PORTAL_UPLOAD_MAX_MB", "10")) * 1024 * 1024


# ── formatting helpers (also imported by the template tests) ─────

def fmt_date(v) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y")
    if isinstance(v, date):
        return v.strftime("%d %b %Y")
    s = str(v)
    try:
        return datetime.fromisoformat(s[:19]).strftime("%d %b %Y")
    except ValueError:
        return s[:10]


def fmt_dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d %b %Y")
    s = str(v)
    try:
        return datetime.fromisoformat(s[:19]).strftime("%d %b %Y %H:%M")
    except ValueError:
        return s[:16]


def fmt_money(v, currency: str = "") -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return str(v)
    s = f"{n:,.2f}"
    return f"{s} {currency}".strip() if currency else s


def fmt_size(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


# ── response helpers ─────────────────────────────────────────────

def _ctx(request: Request, user: dict | None = None, **extra) -> dict:
    company_id = (user or {}).get("company_id") or "default"
    ctx = {
        "request": request,
        "url_for": make_url_for(request),
        "csrf_token": lambda: pa.get_csrf_token(request),
        "get_flashed_messages": lambda **k: pa.pop_flashes(request),
        "portal_user": user,
        "company_name": portal_store.company_name(company_id),
        "fmt_date": fmt_date, "fmt_dt": fmt_dt, "fmt_money": fmt_money, "fmt_size": fmt_size,
        "current_year": datetime.now().year,
    }
    ctx.update(extra)
    return ctx


def _render(request: Request, name: str, ctx: dict, status: int = 200):
    resp = templates.TemplateResponse(name, ctx, status_code=status)
    for k, v in _NO_STORE.items():
        resp.headers[k] = v
    return resp


def _redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status, headers=_NO_STORE)


def _admin_ctx(request: Request, **extra) -> dict:
    ctx = template_context(request)
    ctx.update(fmt_date=fmt_date, fmt_dt=fmt_dt, fmt_money=fmt_money, fmt_size=fmt_size)
    ctx.update(extra)
    return ctx


def _admin_render(request: Request, name: str, ctx: dict, status: int = 200):
    return _render(request, name, ctx, status)


def _staff_flash(request: Request, message: str, category: str = "info") -> None:
    from deps import flash
    flash(request, message, category)


def _staff_name(request: Request) -> str:
    return request.session.get("full_name") or request.session.get("username") or "staff"


def _f(form, key: str, default: str = "") -> str:
    v = form.get(key)
    if v is None:
        return default
    return str(v).strip()


# ── e-mail templates ─────────────────────────────────────────────

def _send_invite_email(request: Request, user: dict, token: str) -> tuple[bool, str]:
    link = f"{pa.external_base_url(request)}/portal/accept-invite?token={token}"
    company = portal_store.company_name(user.get("company_id") or "default") or "EBMS"
    html = (
        f"<p>Hello {user.get('full_name') or user.get('email')},</p>"
        f"<p>{company} has invited you to the {user.get('kind')} portal.</p>"
        f"<p><a href=\"{link}\">Set your password and activate your account</a></p>"
        f"<p>This link expires in {pa.INVITE_TOKEN_HOURS} hours. If you did not expect this "
        "invitation you can ignore this e-mail.</p>")
    try:
        from email_service import send_email
        ok = send_email(user["email"], f"{company}: your portal invitation", html, category="portal_invite")
    except Exception as e:
        logger.warning("portal invite e-mail failed: %s", e)
        ok = False
    return ok, link


def _send_reset_email(request: Request, user: dict, token: str) -> tuple[bool, str]:
    link = f"{pa.external_base_url(request)}/portal/reset?token={token}"
    company = portal_store.company_name(user.get("company_id") or "default") or "EBMS"
    html = (
        f"<p>Hello {user.get('full_name') or user.get('email')},</p>"
        f"<p>A password reset was requested for your {company} portal account.</p>"
        f"<p><a href=\"{link}\">Choose a new password</a> (valid for {pa.RESET_TOKEN_HOURS} hour).</p>"
        "<p>If you did not request this, ignore this e-mail — your password is unchanged.</p>")
    try:
        from email_service import send_email
        ok = send_email(user["email"], f"{company}: portal password reset", html, category="portal_reset")
    except Exception as e:
        logger.warning("portal reset e-mail failed: %s", e)
        ok = False
    return ok, link


# ═══════════════════════════════════════════════════════════════
#  PUBLIC — login / logout / forgot / reset / accept-invite
# ═══════════════════════════════════════════════════════════════

@router.get("/login", name="portal_login")
async def login_get(request: Request):
    if pa.current_portal_user(request):
        return _redirect("/portal/")
    return _render(request, "portal/login.html",
                   _ctx(request, next=pa.safe_next(request.query_params.get("next")), email=""))


@router.post("/login", name="portal_login_post")
async def login_post(request: Request):
    form = await request.form()
    pa.require_csrf(request, form)
    email = _f(form, "email").lower()
    password = form.get("password") or ""
    nxt = pa.safe_next(_f(form, "next"))
    ip = pa.client_ip(request)
    ip_key, email_key = pa.rate_limit_keys(ip, email)

    def fail(msg: str, status: int = 200):
        return _render(request, "portal/login.html", _ctx(request, next=nxt, email=email, error=msg), status)

    if pa.login_limiter.is_blocked(ip_key) or pa.login_limiter.is_blocked(email_key):
        wait = max(pa.login_limiter.retry_after(ip_key), pa.login_limiter.retry_after(email_key))
        portal_store.audit("default", None, f"login_rate_limited {email}", ip, pa.user_agent(request))
        return fail(f"Too many sign-in attempts. Please try again in {max(1, wait // 60)} minute(s).", 429)

    if not email or not password:
        pa.login_limiter.hit(ip_key)
        return fail("Please enter your e-mail and password.")

    candidates = [u for u in portal_store.users_by_email(email) if u.get("password_hash")]
    matched = None
    locked_hit = False
    for u in candidates:
        if not u.get("is_active"):
            continue
        if pa.is_locked(u.get("locked_until")):
            locked_hit = True
            continue
        if pa.verify_password(password, u["password_hash"]):
            matched = u
            break

    if not matched:
        pa.login_limiter.hit(ip_key)
        pa.login_limiter.hit(email_key)
        for u in candidates:
            if u.get("is_active") and not pa.is_locked(u.get("locked_until")):
                n, until = pa.next_failure_state(u.get("failed_attempts"))
                portal_store.record_login_failure(u["id"], n, until)
                portal_store.audit(u["company_id"], u["id"], "login_failed", ip, pa.user_agent(request))
        if locked_hit and not any(u.get("is_active") and not pa.is_locked(u.get("locked_until")) for u in candidates):
            return fail("This account is temporarily locked after too many failed attempts. "
                        "Try again later or reset your password.")
        return fail("Incorrect e-mail or password.")

    pa.login_limiter.reset(email_key)
    portal_store.record_login_success(matched["id"])
    pa.login_session(request, matched)
    portal_store.audit(matched["company_id"], matched["id"], "login", ip, pa.user_agent(request))
    return _redirect(nxt)


@router.post("/logout", name="portal_logout")
async def logout_post(request: Request):
    form = await request.form()
    pa.require_csrf(request, form)
    u = pa.current_portal_user(request)
    if u:
        portal_store.audit(u["company_id"], u["id"], "logout", pa.client_ip(request), pa.user_agent(request))
    pa.logout_session(request)
    pa.flash(request, "You have been signed out.", "success")
    return _redirect("/portal/login")


@router.get("/logout", name="portal_logout_get")
async def logout_get(request: Request):
    # GET keeps the identity (no CSRF); it just shows the login page.
    return _redirect("/portal/login")


@router.get("/forgot", name="portal_forgot")
async def forgot_get(request: Request):
    return _render(request, "portal/forgot.html", _ctx(request, sent=False, email=""))


@router.post("/forgot", name="portal_forgot_post")
async def forgot_post(request: Request):
    form = await request.form()
    pa.require_csrf(request, form)
    email = _f(form, "email").lower()
    ip = pa.client_ip(request)
    ip_key, email_key = pa.rate_limit_keys(ip, email)
    if pa.login_limiter.is_blocked(ip_key):
        return _render(request, "portal/forgot.html",
                       _ctx(request, sent=False, email=email,
                            error="Too many requests. Please try again later."), 429)
    pa.login_limiter.hit(ip_key)
    if email:
        for u in portal_store.users_by_email(email):
            if not u.get("is_active") or not u.get("password_hash"):
                continue
            token = pa.generate_token()
            if portal_store.set_reset_token(u["id"], token, pa.reset_expiry()):
                _send_reset_email(request, u, token)
                portal_store.audit(u["company_id"], u["id"], "reset_requested", ip, pa.user_agent(request))
    # Same answer whether or not the address exists (no account enumeration)
    return _render(request, "portal/forgot.html", _ctx(request, sent=True, email=email))


def _reset_user_or_none(token: str):
    u = portal_store.get_user_by_reset_token(token)
    if u and pa.token_is_valid(u.get("reset_expires_at")):
        return u
    return None


@router.get("/reset", name="portal_reset")
async def reset_get(request: Request):
    token = request.query_params.get("token", "")
    u = _reset_user_or_none(token)
    return _render(request, "portal/reset.html",
                   _ctx(request, token=token, valid=bool(u), email=(u or {}).get("email", "")))


@router.post("/reset", name="portal_reset_post")
async def reset_post(request: Request):
    form = await request.form()
    pa.require_csrf(request, form)
    token = _f(form, "token")
    u = _reset_user_or_none(token)
    if not u:
        return _render(request, "portal/reset.html", _ctx(request, token=token, valid=False, email=""))
    pw, confirm = form.get("password") or "", form.get("confirm") or ""
    ok, err = pa.validate_password(pw)
    if ok and pw != confirm:
        ok, err = False, "The two passwords do not match."
    if not ok:
        return _render(request, "portal/reset.html",
                       _ctx(request, token=token, valid=True, email=u["email"], error=err))
    portal_store.set_password(u["id"], pa.hash_password(pw))
    portal_store.audit(u["company_id"], u["id"], "password_reset", pa.client_ip(request), pa.user_agent(request))
    pa.logout_session(request)
    pa.flash(request, "Your password has been updated. Please sign in.", "success")
    return _redirect("/portal/login")


def _invite_user_or_none(token: str):
    u = portal_store.get_user_by_invite_token(token)
    if u and pa.token_is_valid(u.get("invite_expires_at")):
        return u
    return None


@router.get("/accept-invite", name="portal_accept_invite")
async def accept_invite_get(request: Request):
    token = request.query_params.get("token", "")
    u = _invite_user_or_none(token)
    ctx = _ctx(request, token=token, valid=bool(u), invite=u or {})
    if u:
        ctx["company_name"] = portal_store.company_name(u.get("company_id") or "default")
    return _render(request, "portal/accept_invite.html", ctx)


@router.post("/accept-invite", name="portal_accept_invite_post")
async def accept_invite_post(request: Request):
    form = await request.form()
    pa.require_csrf(request, form)
    token = _f(form, "token")
    u = _invite_user_or_none(token)
    if not u:
        return _render(request, "portal/accept_invite.html", _ctx(request, token=token, valid=False, invite={}))
    pw, confirm = form.get("password") or "", form.get("confirm") or ""
    ok, err = pa.validate_password(pw)
    if ok and pw != confirm:
        ok, err = False, "The two passwords do not match."
    if not ok:
        return _render(request, "portal/accept_invite.html",
                       _ctx(request, token=token, valid=True, invite=u, error=err))
    full_name = _f(form, "full_name") or u.get("full_name") or ""
    phone = _f(form, "phone") or u.get("phone") or ""
    portal_store.accept_invite(u["id"], pa.hash_password(pw))
    portal_store.update_profile(u["id"], full_name, phone)
    fresh = portal_store.get_user(u["id"]) or u
    pa.login_session(request, fresh)
    portal_store.audit(u["company_id"], u["id"], "invite_accepted", pa.client_ip(request), pa.user_agent(request))
    pa.flash(request, "Welcome! Your account is active.", "success")
    return _redirect("/portal/")


# ═══════════════════════════════════════════════════════════════
#  PORTAL — home / profile / tickets (both kinds)
# ═══════════════════════════════════════════════════════════════

@router.get("/", name="portal_home")
async def home(request: Request, user=Depends(pa.portal_user)):
    cid = user["company_id"]
    if user["kind"] == "supplier":
        data = portal_store.supplier_home(cid, user)
    else:
        data = portal_store.customer_home(cid, user)
    return _render(request, "portal/home.html", _ctx(request, user, **data))


@router.get("/profile", name="portal_profile")
async def profile_get(request: Request, user=Depends(pa.portal_user)):
    account = portal_store.get_user(user["id"], user["company_id"]) or user
    return _render(request, "portal/profile.html", _ctx(request, user, account=account))


@router.post("/profile", name="portal_profile_post")
async def profile_post(request: Request, user=Depends(pa.portal_user)):
    form = await request.form()
    pa.require_csrf(request, form)
    action = _f(form, "action")
    if action == "password":
        current, new, confirm = form.get("current") or "", form.get("password") or "", form.get("confirm") or ""
        if not pa.verify_password(current, portal_store.password_hash_for(user["id"]) or ""):
            pa.flash(request, "Current password is incorrect.", "error")
        else:
            ok, err = pa.validate_password(new)
            if ok and new != confirm:
                ok, err = False, "The two passwords do not match."
            if not ok:
                pa.flash(request, err, "error")
            else:
                portal_store.set_password(user["id"], pa.hash_password(new))
                portal_store.audit(user["company_id"], user["id"], "password_changed",
                                   pa.client_ip(request), pa.user_agent(request))
                pa.flash(request, "Password updated.", "success")
    else:
        full_name = _f(form, "full_name")
        if not full_name:
            pa.flash(request, "Name cannot be empty.", "error")
        else:
            portal_store.update_profile(user["id"], full_name, _f(form, "phone"))
            fresh = portal_store.get_user(user["id"], user["company_id"])
            if fresh:
                request.session[pa.SESSION_USER_KEY] = {**user, "full_name": fresh["full_name"]}
            pa.flash(request, "Profile updated.", "success")
    return _redirect("/portal/profile")


@router.get("/tickets", name="portal_tickets")
async def tickets_list(request: Request, user=Depends(pa.portal_user)):
    tickets = portal_store.user_tickets(user["company_id"], user["id"])
    return _render(request, "portal/customer_tickets.html", _ctx(request, user, tickets=tickets, ticket=None))


@router.get("/tickets/new", name="portal_ticket_new")
async def ticket_new_get(request: Request, user=Depends(pa.portal_user)):
    return _render(request, "portal/customer_ticket_new.html",
                   _ctx(request, user, priorities=TICKET_PRIORITIES, form={}))


@router.post("/tickets/new", name="portal_ticket_new_post")
async def ticket_new_post(request: Request, user=Depends(pa.portal_user)):
    form = await request.form()
    pa.require_csrf(request, form)
    data = {"subject": _f(form, "subject"), "body": _f(form, "body"), "priority": _f(form, "priority", "normal")}
    if not data["subject"] or not data["body"]:
        return _render(request, "portal/customer_ticket_new.html",
                       _ctx(request, user, priorities=TICKET_PRIORITIES, form=data,
                            error="Subject and message are required."))
    t = portal_store.create_ticket(user["company_id"], user["id"], data["subject"], data["body"], data["priority"])
    if not t:
        pa.flash(request, "Could not create the ticket. Please try again.", "error")
        return _redirect("/portal/tickets/new")
    portal_store.add_ticket_message(t["id"], "portal", user.get("full_name") or user["email"], data["body"])
    portal_store.audit(user["company_id"], user["id"], f"ticket_created {t['id']}",
                       pa.client_ip(request), pa.user_agent(request))
    pa.flash(request, "Ticket submitted. We will get back to you.", "success")
    return _redirect(f"/portal/tickets/{t['id']}")


@router.get("/tickets/{ticket_id}", name="portal_ticket_detail")
async def ticket_detail(ticket_id: str, request: Request, user=Depends(pa.portal_user)):
    t = portal_store.get_ticket(ticket_id, user["company_id"], portal_user_id=user["id"])
    if not t:
        pa.flash(request, "Ticket not found.", "error")
        return _redirect("/portal/tickets")
    tickets = portal_store.user_tickets(user["company_id"], user["id"])
    return _render(request, "portal/customer_tickets.html", _ctx(request, user, tickets=tickets, ticket=t))


@router.post("/tickets/{ticket_id}/reply", name="portal_ticket_reply")
async def ticket_reply(ticket_id: str, request: Request, user=Depends(pa.portal_user)):
    form = await request.form()
    pa.require_csrf(request, form)
    t = portal_store.get_ticket(ticket_id, user["company_id"], portal_user_id=user["id"])
    if not t:
        pa.flash(request, "Ticket not found.", "error")
        return _redirect("/portal/tickets")
    if portal_store.add_ticket_message(ticket_id, "portal", user.get("full_name") or user["email"],
                                       _f(form, "body"), reopen=True):
        pa.flash(request, "Reply sent.", "success")
    else:
        pa.flash(request, "Reply cannot be empty.", "error")
    return _redirect(f"/portal/tickets/{ticket_id}")


# ═══════════════════════════════════════════════════════════════
#  CUSTOMER
# ═══════════════════════════════════════════════════════════════

@router.get("/customer/invoices", name="portal_customer_invoices")
async def customer_invoices(request: Request, user=Depends(pa.portal_customer)):
    rows = portal_store.customer_invoices(user["company_id"], user["party_key"])
    return _render(request, "portal/customer_invoices.html",
                   _ctx(request, user, invoices=rows, totals=portal_store.totals(rows)))


@router.get("/customer/invoices/{income_id}", name="portal_customer_invoice_detail")
async def customer_invoice_detail(income_id: str, request: Request, user=Depends(pa.portal_customer)):
    inv = portal_store.customer_invoice(user["company_id"], user["party_key"], income_id)
    if not inv:
        pa.flash(request, "Invoice not found.", "error")
        return _redirect("/portal/customer/invoices")
    return _render(request, "portal/customer_invoice_detail.html", _ctx(request, user, invoice=inv))


@router.get("/customer/orders", name="portal_customer_orders")
async def customer_orders(request: Request, user=Depends(pa.portal_customer)):
    cid, pk = user["company_id"], user["party_key"]
    return _render(request, "portal/customer_orders.html",
                   _ctx(request, user, cpos=portal_store.customer_cpos(cid, pk),
                        bookings=portal_store.customer_bookings(cid, pk)))


@router.get("/customer/projects", name="portal_customer_projects")
async def customer_projects(request: Request, user=Depends(pa.portal_customer)):
    cid, pk = user["company_id"], user["party_key"]
    return _render(request, "portal/customer_projects.html",
                   _ctx(request, user, projects=portal_store.customer_projects(cid, pk),
                        contracts=portal_store.party_contracts(cid, pk, ("client", "other"))))


# ═══════════════════════════════════════════════════════════════
#  SUPPLIER
# ═══════════════════════════════════════════════════════════════

@router.get("/supplier/orders", name="portal_supplier_orders")
async def supplier_orders(request: Request, user=Depends(pa.portal_supplier)):
    cid, pk = user["company_id"], user["party_key"]
    return _render(request, "portal/supplier_orders.html",
                   _ctx(request, user, orders=portal_store.supplier_orders(cid, pk),
                        requisitions=portal_store.supplier_requisitions(cid, pk),
                        contracts=portal_store.party_contracts(cid, pk, ("vendor", "consultant", "other"))))


@router.get("/supplier/orders/{po_id}", name="portal_supplier_order_detail")
async def supplier_order_detail(po_id: str, request: Request, user=Depends(pa.portal_supplier)):
    po = portal_store.supplier_order(user["company_id"], user["party_key"], po_id)
    if not po:
        pa.flash(request, "Order not found.", "error")
        return _redirect("/portal/supplier/orders")
    return _render(request, "portal/supplier_order_detail.html", _ctx(request, user, order=po))


@router.get("/supplier/invoices", name="portal_supplier_invoices")
async def supplier_invoices(request: Request, user=Depends(pa.portal_supplier)):
    cid, pk = user["company_id"], user["party_key"]
    expenses = portal_store.supplier_expenses(cid, pk)
    return _render(request, "portal/supplier_invoices.html",
                   _ctx(request, user, expenses=expenses, totals=portal_store.totals(expenses),
                        proc_invoices=portal_store.supplier_proc_invoices(cid, pk)))


@router.get("/supplier/documents", name="portal_supplier_documents")
async def supplier_documents(request: Request, user=Depends(pa.portal_supplier)):
    return _render(request, "portal/supplier_documents.html",
                   _ctx(request, user, documents=portal_store.user_documents(user["company_id"], user["id"]),
                        kinds=DOCUMENT_KINDS, max_mb=_UPLOAD_MAX_BYTES // (1024 * 1024)))


async def _store_upload(request: Request, form, company_id: str, field: str = "file"):
    """Validate + persist an uploaded file. Returns (stored_path, filename, size) or raises ValueError."""
    f = form.get(field)
    filename = getattr(f, "filename", "") or ""
    if not f or not filename:
        raise ValueError("Please choose a file.")
    content = await f.read()
    ok, err = validate_upload(filename, content, allowed_exts=_UPLOAD_EXTS)
    if not ok:
        raise ValueError(err)
    if len(content) > _UPLOAD_MAX_BYTES:
        raise ValueError(f"File is larger than {_UPLOAD_MAX_BYTES // (1024 * 1024)} MB.")
    ext = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[1].lower()
    safe_company = "".join(ch for ch in company_id if ch.isalnum() or ch in "-_") or "default"
    folder = os.path.join(upload_root(), safe_company)
    os.makedirs(folder, exist_ok=True)
    stored = os.path.join(folder, f"{uuid.uuid4().hex}{ext}")
    with open(stored, "wb") as fh:
        fh.write(content)
    return stored, os.path.basename(filename.replace("\\", "/")), len(content)


@router.post("/supplier/documents", name="portal_supplier_documents_post")
async def supplier_documents_post(request: Request, user=Depends(pa.portal_supplier)):
    form = await request.form()
    pa.require_csrf(request, form)
    try:
        stored, filename, size = await _store_upload(request, form, user["company_id"])
    except ValueError as e:
        pa.flash(request, str(e), "error")
        return _redirect("/portal/supplier/documents")
    doc = portal_store.create_document(user["company_id"], user["id"], _f(form, "kind", "other"),
                                       filename, stored, size, _f(form, "related_ref"))
    if doc:
        portal_store.audit(user["company_id"], user["id"], f"document_uploaded {doc['id']}",
                           pa.client_ip(request), pa.user_agent(request))
        pa.flash(request, "Document uploaded. Our team will review it.", "success")
    else:
        pa.flash(request, "Upload failed. Please try again.", "error")
    return _redirect("/portal/supplier/documents")


@router.get("/supplier/documents/{doc_id}/download", name="portal_supplier_document_download")
async def supplier_document_download(doc_id: str, request: Request, user=Depends(pa.portal_supplier)):
    doc = portal_store.get_document(doc_id, user["company_id"], portal_user_id=user["id"])
    if not doc or not os.path.isfile(doc["stored_path"]):
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(doc["stored_path"], filename=doc["filename"], headers=_NO_STORE)


@router.get("/supplier/rfqs", name="portal_supplier_rfqs")
async def supplier_rfqs(request: Request, user=Depends(pa.portal_supplier)):
    return _render(request, "portal/supplier_rfqs.html",
                   _ctx(request, user, rfqs=portal_store.supplier_rfqs(user["company_id"], user["id"]),
                        now=datetime.now()))


@router.get("/supplier/rfqs/{rfq_id}", name="portal_supplier_rfq_respond")
async def supplier_rfq_get(rfq_id: str, request: Request, user=Depends(pa.portal_supplier)):
    rfq = portal_store.supplier_rfq(rfq_id, user["company_id"], user["id"])
    if not rfq:
        pa.flash(request, "Request for quotation not found.", "error")
        return _redirect("/portal/supplier/rfqs")
    return _render(request, "portal/supplier_rfq_respond.html",
                   _ctx(request, user, rfq=rfq, response=rfq.get("my_response") or {}, now=datetime.now()))


@router.post("/supplier/rfqs/{rfq_id}/respond", name="portal_supplier_rfq_respond_post")
async def supplier_rfq_post(rfq_id: str, request: Request, user=Depends(pa.portal_supplier)):
    form = await request.form()
    pa.require_csrf(request, form)
    attachment = None
    f = form.get("attachment")
    if f is not None and getattr(f, "filename", ""):
        try:
            attachment, _, _ = await _store_upload(request, form, user["company_id"], field="attachment")
        except ValueError as e:
            pa.flash(request, str(e), "error")
            return _redirect(f"/portal/supplier/rfqs/{rfq_id}")
    data = {k: _f(form, k) for k in ("amount", "currency", "delivery_days", "notes")}
    if portal_store.submit_rfq_response(rfq_id, user["company_id"], user["id"], data, attachment):
        portal_store.audit(user["company_id"], user["id"], f"rfq_responded {rfq_id}",
                           pa.client_ip(request), pa.user_agent(request))
        pa.flash(request, "Your quotation has been submitted.", "success")
    else:
        pa.flash(request, "Could not submit — check the amount, and that the RFQ is still open.", "error")
    return _redirect(f"/portal/supplier/rfqs/{rfq_id}")


@router.post("/supplier/rfqs/{rfq_id}/decline", name="portal_supplier_rfq_decline")
async def supplier_rfq_decline(rfq_id: str, request: Request, user=Depends(pa.portal_supplier)):
    form = await request.form()
    pa.require_csrf(request, form)
    if portal_store.decline_rfq(rfq_id, user["company_id"], user["id"]):
        pa.flash(request, "You have declined this request.", "success")
    else:
        pa.flash(request, "Could not decline this request.", "error")
    return _redirect("/portal/supplier/rfqs")


# ═══════════════════════════════════════════════════════════════
#  STAFF ADMIN  (/portal/admin/...) — staff auth enforced explicitly
# ═══════════════════════════════════════════════════════════════

@router.get("/admin", name="portal_admin_index")
async def admin_index(request: Request, staff=Depends(login_required)):
    return _redirect("/portal/admin/users")


# ── users ──

@router.get("/admin/users", name="portal_admin_users")
async def admin_users(request: Request, staff=Depends(admin_required)):
    cid = current_company(request)
    kind = request.query_params.get("kind") or ""
    ctx = _admin_ctx(request, users=portal_store.list_users(cid, kind or None), kind_filter=kind,
                     stats=portal_store.user_stats(cid), ticket_stats=portal_store.ticket_stats(cid),
                     document_stats=portal_store.document_stats(cid), audit=portal_store.recent_audit(cid, 20))
    return _admin_render(request, "portal_admin/users.html", ctx)


@router.get("/admin/users/new", name="portal_admin_user_new")
async def admin_user_new_get(request: Request, staff=Depends(admin_required)):
    kind = request.query_params.get("kind") if request.query_params.get("kind") in USER_KINDS else "customer"
    return _admin_render(request, "portal_admin/user_form.html",
                         _admin_ctx(request, user={"kind": kind, "is_active": True}, kinds=USER_KINDS, is_new=True))


@router.post("/admin/users/new", name="portal_admin_user_new_post")
async def admin_user_new_post(request: Request, staff=Depends(admin_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    cid = current_company(request)
    data = {k: _f(form, k) for k in ("kind", "email", "full_name", "org_name", "tin", "phone", "party_key")}
    if not data["email"] or "@" not in data["email"]:
        return _admin_render(request, "portal_admin/user_form.html",
                             _admin_ctx(request, user=data, kinds=USER_KINDS, is_new=True,
                                        error="A valid e-mail address is required."))
    if not data["party_key"]:
        data["party_key"] = data["org_name"] or data["full_name"]
    if portal_store.email_taken(cid, data["kind"] if data["kind"] in USER_KINDS else "customer", data["email"]):
        return _admin_render(request, "portal_admin/user_form.html",
                             _admin_ctx(request, user=data, kinds=USER_KINDS, is_new=True,
                                        error="A portal account of this kind already exists for that e-mail."))
    token = pa.generate_token()
    user = portal_store.create_invite(cid, data, token, pa.invite_expiry(), created_by=_staff_name(request))
    if not user:
        return _admin_render(request, "portal_admin/user_form.html",
                             _admin_ctx(request, user=data, kinds=USER_KINDS, is_new=True,
                                        error="Could not create the invitation."))
    sent, link = _send_invite_email(request, user, token)
    portal_store.audit(cid, user["id"], f"invited_by {_staff_name(request)}", pa.client_ip(request),
                       pa.user_agent(request))
    return _admin_render(request, "portal_admin/invite_sent.html",
                         _admin_ctx(request, user=user, link=link, email_sent=sent, mode="invite",
                                    expires_hours=pa.INVITE_TOKEN_HOURS))


def _admin_user_or_404(request: Request, user_id: str) -> dict:
    u = portal_store.get_user(user_id, current_company(request))
    if not u:
        raise HTTPException(status_code=404, detail="Portal user not found")
    return u


@router.get("/admin/users/{user_id}/edit", name="portal_admin_user_edit")
async def admin_user_edit_get(user_id: str, request: Request, staff=Depends(admin_required)):
    u = _admin_user_or_404(request, user_id)
    return _admin_render(request, "portal_admin/user_form.html",
                         _admin_ctx(request, user=u, kinds=USER_KINDS, is_new=False))


@router.post("/admin/users/{user_id}/edit", name="portal_admin_user_edit_post")
async def admin_user_edit_post(user_id: str, request: Request, staff=Depends(admin_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    cid = current_company(request)
    _admin_user_or_404(request, user_id)
    data = {k: _f(form, k) for k in ("kind", "email", "full_name", "org_name", "tin", "phone", "party_key")}
    data["is_active"] = bool(form.get("is_active"))
    if portal_store.update_user(user_id, cid, data):
        _staff_flash(request, "Portal user updated", "success")
    else:
        _staff_flash(request, "Could not update the portal user (duplicate e-mail?)", "error")
    return _redirect(f"/portal/admin/users/{user_id}/edit")


@router.post("/admin/users/{user_id}/toggle", name="portal_admin_user_toggle")
async def admin_user_toggle(user_id: str, request: Request, staff=Depends(admin_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    u = _admin_user_or_404(request, user_id)
    portal_store.set_active(user_id, u["company_id"], not u.get("is_active"))
    _staff_flash(request, f"Portal user {'deactivated' if u.get('is_active') else 'activated'}", "success")
    return _redirect("/portal/admin/users")


@router.post("/admin/users/{user_id}/unlock", name="portal_admin_user_unlock")
async def admin_user_unlock(user_id: str, request: Request, staff=Depends(admin_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    u = _admin_user_or_404(request, user_id)
    portal_store.unlock(user_id, u["company_id"])
    _staff_flash(request, "Account unlocked", "success")
    return _redirect("/portal/admin/users")


@router.post("/admin/users/{user_id}/reset", name="portal_admin_user_reset")
async def admin_user_reset(user_id: str, request: Request, staff=Depends(admin_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    u = _admin_user_or_404(request, user_id)
    token = pa.generate_token()
    if u.get("has_password"):
        portal_store.set_reset_token(user_id, token, pa.reset_expiry())
        sent, link = _send_reset_email(request, u, token)
        mode, hours = "reset", pa.RESET_TOKEN_HOURS
    else:
        portal_store.set_invite_token(user_id, u["company_id"], token, pa.invite_expiry())
        sent, link = _send_invite_email(request, u, token)
        mode, hours = "invite", pa.INVITE_TOKEN_HOURS
    portal_store.audit(u["company_id"], user_id, f"{mode}_link_issued_by {_staff_name(request)}",
                       pa.client_ip(request), pa.user_agent(request))
    return _admin_render(request, "portal_admin/invite_sent.html",
                         _admin_ctx(request, user=u, link=link, email_sent=sent, mode=mode, expires_hours=hours))


@router.post("/admin/users/{user_id}/delete", name="portal_admin_user_delete")
async def admin_user_delete(user_id: str, request: Request, staff=Depends(admin_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    u = _admin_user_or_404(request, user_id)
    portal_store.delete_user(user_id, u["company_id"])
    _staff_flash(request, "Portal user deleted", "success")
    return _redirect("/portal/admin/users")


# ── tickets ──

@router.get("/admin/tickets", name="portal_admin_tickets")
async def admin_tickets(request: Request, staff=Depends(login_required)):
    cid = current_company(request)
    status = request.query_params.get("status") or ""
    return _admin_render(request, "portal_admin/tickets.html",
                         _admin_ctx(request, tickets=portal_store.company_tickets(cid, status or None),
                                    status_filter=status, statuses=TICKET_STATUSES,
                                    stats=portal_store.ticket_stats(cid)))


@router.get("/admin/tickets/{ticket_id}", name="portal_admin_ticket_detail")
async def admin_ticket_detail(ticket_id: str, request: Request, staff=Depends(login_required)):
    t = portal_store.get_ticket(ticket_id, current_company(request))
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _admin_render(request, "portal_admin/ticket_detail.html",
                         _admin_ctx(request, ticket=t, statuses=TICKET_STATUSES))


@router.post("/admin/tickets/{ticket_id}/reply", name="portal_admin_ticket_reply")
async def admin_ticket_reply(ticket_id: str, request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    cid = current_company(request)
    t = portal_store.get_ticket(ticket_id, cid)
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if portal_store.add_ticket_message(ticket_id, "staff", _staff_name(request), _f(form, "body")):
        new_status = _f(form, "status")
        if new_status in TICKET_STATUSES and new_status != t.get("status"):
            portal_store.set_ticket_status(ticket_id, cid, new_status)
        elif t.get("status") == "open":
            portal_store.set_ticket_status(ticket_id, cid, "in_progress")
        _staff_flash(request, "Reply posted", "success")
    else:
        _staff_flash(request, "Reply cannot be empty", "error")
    return _redirect(f"/portal/admin/tickets/{ticket_id}")


@router.post("/admin/tickets/{ticket_id}/status", name="portal_admin_ticket_status")
async def admin_ticket_status(ticket_id: str, request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    if portal_store.set_ticket_status(ticket_id, current_company(request), _f(form, "status")):
        _staff_flash(request, "Ticket status updated", "success")
    else:
        _staff_flash(request, "Invalid status", "error")
    return _redirect(f"/portal/admin/tickets/{ticket_id}")


# ── RFQs ──

@router.get("/admin/rfqs", name="portal_admin_rfqs")
async def admin_rfqs(request: Request, staff=Depends(login_required)):
    cid = current_company(request)
    return _admin_render(request, "portal_admin/rfqs.html",
                         _admin_ctx(request, rfqs=portal_store.company_rfqs(cid)))


@router.get("/admin/rfqs/new", name="portal_admin_rfq_new")
async def admin_rfq_new_get(request: Request, staff=Depends(login_required)):
    return _admin_render(request, "portal_admin/rfq_form.html",
                         _admin_ctx(request, rfq={}, invitations=[], responses=[], suppliers=[],
                                    statuses=RFQ_STATUSES))


@router.post("/admin/rfqs/new", name="portal_admin_rfq_new_post")
async def admin_rfq_new_post(request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    cid = current_company(request)
    data = {k: _f(form, k) for k in ("title", "description", "due_at")}
    rfq = portal_store.create_rfq(cid, data, created_by=_staff_name(request))
    if not rfq:
        return _admin_render(request, "portal_admin/rfq_form.html",
                             _admin_ctx(request, rfq=data, invitations=[], responses=[], suppliers=[],
                                        statuses=RFQ_STATUSES, error="Title is required."))
    _staff_flash(request, "RFQ created — now invite suppliers", "success")
    return _redirect(f"/portal/admin/rfqs/{rfq['id']}")


def _admin_rfq_or_404(request: Request, rfq_id: str) -> dict:
    r = portal_store.get_rfq(rfq_id, current_company(request))
    if not r:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return r


@router.get("/admin/rfqs/{rfq_id}", name="portal_admin_rfq_detail")
async def admin_rfq_detail(rfq_id: str, request: Request, staff=Depends(login_required)):
    cid = current_company(request)
    rfq = _admin_rfq_or_404(request, rfq_id)
    invitations = portal_store.rfq_invitations(rfq_id)
    invited_ids = {i["portal_user_id"] for i in invitations}
    suppliers = [s for s in portal_store.list_users(cid, "supplier") if s["id"] not in invited_ids]
    return _admin_render(request, "portal_admin/rfq_form.html",
                         _admin_ctx(request, rfq=rfq, invitations=invitations,
                                    responses=portal_store.rfq_responses(rfq_id),
                                    suppliers=suppliers, statuses=RFQ_STATUSES))


@router.post("/admin/rfqs/{rfq_id}/edit", name="portal_admin_rfq_edit")
async def admin_rfq_edit(rfq_id: str, request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    _admin_rfq_or_404(request, rfq_id)
    data = {k: _f(form, k) for k in ("title", "description", "due_at")}
    if portal_store.update_rfq(rfq_id, current_company(request), data):
        _staff_flash(request, "RFQ updated", "success")
    else:
        _staff_flash(request, "Could not update RFQ", "error")
    return _redirect(f"/portal/admin/rfqs/{rfq_id}")


@router.post("/admin/rfqs/{rfq_id}/invite", name="portal_admin_rfq_invite")
async def admin_rfq_invite(rfq_id: str, request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    cid = current_company(request)
    rfq = _admin_rfq_or_404(request, rfq_id)
    ids = [v for v in form.getlist("supplier_ids") if v]
    n = portal_store.invite_suppliers(rfq_id, cid, ids)
    if n:
        base = pa.external_base_url(request)
        try:
            from email_service import send_email
            for uid in ids:
                s = portal_store.get_user(uid, cid)
                if s and s.get("kind") == "supplier":
                    send_email(s["email"], f"Request for quotation: {rfq['title']}",
                               f"<p>You have been invited to quote on <b>{rfq['title']}</b>.</p>"
                               f"<p><a href=\"{base}/portal/supplier/rfqs/{rfq_id}\">Open the request in the supplier portal</a></p>",
                               category="portal_rfq")
        except Exception as e:
            logger.warning("rfq invite e-mail failed: %s", e)
    _staff_flash(request, f"{n} supplier(s) invited", "success" if n else "warning")
    return _redirect(f"/portal/admin/rfqs/{rfq_id}")


@router.post("/admin/rfqs/{rfq_id}/status", name="portal_admin_rfq_status")
async def admin_rfq_status(rfq_id: str, request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    _admin_rfq_or_404(request, rfq_id)
    if portal_store.set_rfq_status(rfq_id, current_company(request), _f(form, "status")):
        _staff_flash(request, "RFQ status updated", "success")
    else:
        _staff_flash(request, "Invalid status", "error")
    return _redirect(f"/portal/admin/rfqs/{rfq_id}")


# ── documents ──

@router.get("/admin/documents", name="portal_admin_documents")
async def admin_documents(request: Request, staff=Depends(login_required)):
    cid = current_company(request)
    status = request.query_params.get("status") or ""
    return _admin_render(request, "portal_admin/documents.html",
                         _admin_ctx(request, documents=portal_store.company_documents(cid, status or None),
                                    status_filter=status, statuses=DOCUMENT_STATUSES,
                                    stats=portal_store.document_stats(cid)))


@router.post("/admin/documents/{doc_id}/review", name="portal_admin_document_review")
async def admin_document_review(doc_id: str, request: Request, staff=Depends(login_required)):
    form = await request.form()
    pa.require_staff_csrf(request, form)
    if portal_store.review_document(doc_id, current_company(request), _f(form, "status"), _f(form, "note")):
        _staff_flash(request, "Document reviewed", "success")
    else:
        _staff_flash(request, "Could not update the document", "error")
    return _redirect("/portal/admin/documents")


@router.get("/admin/documents/{doc_id}/download", name="portal_admin_document_download")
async def admin_document_download(doc_id: str, request: Request, staff=Depends(login_required)):
    doc = portal_store.get_document(doc_id, current_company(request))
    if not doc or not os.path.isfile(doc["stored_path"]):
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(doc["stored_path"], filename=doc["filename"], headers=_NO_STORE)


@router.get("/admin/rfqs/{rfq_id}/responses/{response_id}/attachment", name="portal_admin_rfq_attachment")
async def admin_rfq_attachment(rfq_id: str, response_id: str, request: Request, staff=Depends(login_required)):
    _admin_rfq_or_404(request, rfq_id)
    resp = next((r for r in portal_store.rfq_responses(rfq_id) if r["id"] == response_id), None)
    if not resp or not resp.get("attachment_path") or not os.path.isfile(resp["attachment_path"]):
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(resp["attachment_path"], filename=os.path.basename(resp["attachment_path"]),
                        headers=_NO_STORE)
