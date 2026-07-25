"""
Event Management System Routes — Venues, Bookings, Services
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required
from template_engine import templates
from ems_data_store import ems_store
from notifications_data_store import notifications_store
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ems", tags=["ems"])


@router.on_event("startup")
async def _startup():
    ems_store.ensure_schema()


def _parse_dt(s: str):
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            pass
    return None


@router.get("/", name="ems_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    now = datetime.now()
    ctx = template_context(request)
    ctx.update(
        stats=ems_store.get_stats(cid),
        venues=ems_store.get_venues(cid),
        bookings=ems_store.get_bookings(cid)[:20],
        occupancy=ems_store.get_occupancy(cid, now.year, now.month),
        upcoming_events=ems_store.get_upcoming_events(cid, days=14),
        today_occupancy=ems_store.get_today_occupancy(cid),
        revenue_projection=ems_store.get_revenue_projection(cid),
    )
    return templates.TemplateResponse("ems/dashboard.html", ctx)


# ── Venues ────────────────────────────────────────────────────────────────────

@router.get("/venues", name="ems_venues")
async def venues(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["venues"] = ems_store.get_venues(cid)
    return templates.TemplateResponse("ems/venues.html", ctx)


@router.get("/venues/new", name="ems_new_venue_get")
async def new_venue_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("ems/venue_form.html", {**template_context(request), "venue": {}, "action": "create"})


@router.post("/venues/new", name="ems_new_venue_post")
async def new_venue_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    v = ems_store.create_venue(cid, data)
    if v:
        flash(request, f"Venue '{v['name']}' created", "success")
    return RedirectResponse("/ems/venues", status_code=303)


@router.post("/venues/{venue_id}/edit", name="ems_edit_venue")
async def edit_venue(venue_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    ems_store.update_venue(venue_id, cid, data)
    flash(request, "Venue updated", "success")
    return RedirectResponse("/ems/venues", status_code=303)


@router.post("/venues/{venue_id}/delete", name="ems_delete_venue")
async def delete_venue(venue_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ems_store.delete_venue(venue_id, cid)
    flash(request, "Venue deactivated", "success")
    return RedirectResponse("/ems/venues", status_code=303)


@router.get("/venues/{venue_id}/availability", name="ems_check_availability")
async def check_availability(venue_id: str, request: Request, user=Depends(login_required)):
    params = request.query_params
    setup_start  = _parse_dt(params.get("setup_start", ""))
    teardown_end = _parse_dt(params.get("teardown_end", ""))
    if not setup_start or not teardown_end:
        return JSONResponse({"available": False, "error": "Invalid dates"})
    result = ems_store.check_availability(venue_id, setup_start, teardown_end)
    return JSONResponse(result)


# ── Bookings ──────────────────────────────────────────────────────────────────

@router.get("/bookings", name="ems_bookings")
async def bookings(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["bookings"] = ems_store.get_bookings(cid)
    return templates.TemplateResponse("ems/bookings.html", ctx)


@router.get("/bookings/new", name="ems_new_booking_get")
async def new_booking_get(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx.update(venues=ems_store.get_venues(cid), services=ems_store.get_services(cid),
               clients=ems_store.get_clients(cid), booking={})
    return templates.TemplateResponse("ems/booking_form.html", ctx)


@router.post("/bookings/new", name="ems_new_booking_post")
async def new_booking_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = request.session.get("username", "")
    # Parse datetime fields
    for field in ("setup_start", "event_start", "event_end", "teardown_end"):
        data[field] = _parse_dt(data.get(field, ""))
    if not all(data[f] for f in ("setup_start", "event_start", "event_end", "teardown_end")):
        flash(request, "All time fields are required", "error")
        return RedirectResponse("/ems/bookings/new", status_code=303)
    service_ids  = form.getlist("service_id")
    service_qtys = [float(q or 1) for q in form.getlist("service_qty")]
    result = ems_store.create_booking(cid, data, service_ids, service_qtys)
    if result["ok"]:
        notifications_store.broadcast(
            cid, f"New booking: {data['event_name']}",
            message=f"Confirmed by {request.session.get('username','')}",
            link=f"/ems/bookings/{result['booking']['id']}",
            icon="calendar-check", category="success"
        )
        flash(request, "Booking confirmed!", "success")
        return RedirectResponse(f"/ems/bookings/{result['booking']['id']}", status_code=303)
    flash(request, result.get("error", "Booking failed"), "error")
    return RedirectResponse("/ems/bookings/new", status_code=303)


@router.get("/bookings/{booking_id}", name="ems_booking_detail")
async def booking_detail(booking_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    bk = ems_store.get_booking(booking_id, cid)
    if not bk:
        flash(request, "Booking not found", "error")
        return RedirectResponse("/ems/bookings", status_code=303)
    ctx = template_context(request)
    ctx["booking"] = bk
    return templates.TemplateResponse("ems/booking_detail.html", ctx)


@router.get("/bookings/{booking_id}/quotation", name="ems_booking_quotation")
async def booking_quotation(booking_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    bk = ems_store.get_booking(booking_id, cid)
    if not bk:
        flash(request, "Booking not found", "error")
        return RedirectResponse("/ems/bookings", status_code=303)
    ctx = template_context(request)
    venue = ems_store.get_venue(bk["venue_id"], cid)
    client = ems_store.get_client(bk.get("client_id") or "", cid) if bk.get("client_id") else None
    ctx.update(booking=bk, venue=venue, client=client, today=datetime.now())
    return templates.TemplateResponse("ems/quotation.html", ctx)


@router.post("/bookings/{booking_id}/confirm", name="ems_confirm_booking")
async def confirm_booking(booking_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ems_store.update_booking_status(booking_id, cid, "confirmed")
    flash(request, "Booking confirmed", "success")
    return RedirectResponse(f"/ems/bookings/{booking_id}", status_code=303)


@router.post("/bookings/{booking_id}/cancel", name="ems_cancel_booking")
async def cancel_booking(booking_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ems_store.update_booking_status(booking_id, cid, "cancelled")
    flash(request, "Booking cancelled", "success")
    return RedirectResponse("/ems/bookings", status_code=303)


# ── Services ─────────────────────────────────────────────────────────────────

@router.get("/services", name="ems_services")
async def services(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["services"] = ems_store.get_services(cid)
    return templates.TemplateResponse("ems/services.html", ctx)


@router.post("/services/new", name="ems_new_service")
async def new_service(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    ems_store.create_service(cid, data)
    flash(request, "Service item added", "success")
    return RedirectResponse("/ems/services", status_code=303)


# ── Clients ──────────────────────────────────────────────────────────────────
# NOTE: static paths (/clients, /clients/new) MUST be registered before /clients/{client_id}

@router.get("/clients", name="ems_clients")
async def clients(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["clients"] = ems_store.get_clients(cid)
    return templates.TemplateResponse("ems/clients.html", ctx)


@router.get("/clients/new", name="ems_new_client_get")
async def new_client_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("ems/client_form.html", {**template_context(request), "client": {}, "action": "create"})


@router.post("/clients/new", name="ems_new_client_post")
async def new_client_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if not data.get("name", "").strip():
        flash(request, "Client name is required", "error")
        return RedirectResponse("/ems/clients/new", status_code=303)
    c = ems_store.create_client(cid, data)
    if c:
        flash(request, f"Client '{c['name']}' created", "success")
        return RedirectResponse(f"/ems/clients/{c['id']}", status_code=303)
    flash(request, "Could not create client", "error")
    return RedirectResponse("/ems/clients", status_code=303)


@router.get("/clients/{client_id}", name="ems_client_detail")
async def client_detail(client_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    client = ems_store.get_client(client_id, cid)
    if not client:
        flash(request, "Client not found", "error")
        return RedirectResponse("/ems/clients", status_code=303)
    ctx = template_context(request)
    ctx.update(client=client,
               client_bookings=ems_store.get_client_bookings(cid, client_id, client["name"]))
    return templates.TemplateResponse("ems/client_detail.html", ctx)


@router.post("/clients/{client_id}/edit", name="ems_edit_client")
async def edit_client(client_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if not data.get("name", "").strip():
        flash(request, "Client name is required", "error")
    elif ems_store.update_client(client_id, cid, data):
        flash(request, "Client updated", "success")
    else:
        flash(request, "Update failed", "error")
    return RedirectResponse(f"/ems/clients/{client_id}", status_code=303)


# ── Reports ──────────────────────────────────────────────────────────────────

def _parse_date(s: str, default):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return default


@router.get("/reports", name="ems_reports")
async def reports(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    now = datetime.now()
    start = _parse_date(request.query_params.get("start", ""), date(now.year, 1, 1))
    end = _parse_date(request.query_params.get("end", ""), date(now.year, 12, 31))
    if end < start:
        start, end = end, start
    ctx = template_context(request)
    ctx["report"] = ems_store.get_reports(cid, start, end)
    return templates.TemplateResponse("ems/reports.html", ctx)


# ── Visitors ─────────────────────────────────────────────────────────────────

@router.get("/visitors", name="ems_visitors")
async def visitors(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["visitors"] = ems_store.get_visitors(cid)
    return templates.TemplateResponse("ems/visitors.html", ctx)


@router.post("/visitors/new", name="ems_new_visitor")
async def new_visitor(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if not data.get("name", "").strip():
        flash(request, "Visitor name is required", "error")
    elif ems_store.register_visitor(cid, data):
        flash(request, "Visitor checked in", "success")
    else:
        flash(request, "Could not register visitor", "error")
    return RedirectResponse("/ems/visitors", status_code=303)


@router.post("/visitors/{visitor_id}/checkout", name="ems_checkout_visitor")
async def checkout_visitor(visitor_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ems_store.checkout_visitor(visitor_id, cid)
    flash(request, "Visitor checked out", "success")
    return RedirectResponse("/ems/visitors", status_code=303)


# ── Appointments ─────────────────────────────────────────────────────────────

@router.get("/appointments", name="ems_appointments")
async def appointments(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx["appointments"] = ems_store.get_appointments(cid)
    return templates.TemplateResponse("ems/appointments.html", ctx)


@router.post("/appointments/new", name="ems_new_appointment")
async def new_appointment(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["scheduled_at"] = _parse_dt(data.get("scheduled_at", ""))
    if not data.get("visitor_name", "").strip() or not data["scheduled_at"]:
        flash(request, "Visitor name and scheduled time are required", "error")
    elif ems_store.create_appointment(cid, data):
        flash(request, "Appointment created", "success")
    else:
        flash(request, "Could not create appointment", "error")
    return RedirectResponse("/ems/appointments", status_code=303)


@router.post("/appointments/{appointment_id}/status", name="ems_appointment_status")
async def appointment_status(appointment_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    status = form.get("status", "")
    if ems_store.update_appointment_status(appointment_id, cid, status):
        flash(request, f"Appointment marked {status}", "success")
    else:
        flash(request, "Invalid status", "error")
    return RedirectResponse("/ems/appointments", status_code=303)
