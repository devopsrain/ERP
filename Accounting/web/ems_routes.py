"""
Event Management System Routes — Venues, Bookings, Services
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required
from template_engine import templates
from ems_data_store import ems_store
from notifications_data_store import notifications_store
from datetime import datetime
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
    ctx.update(venues=ems_store.get_venues(cid), services=ems_store.get_services(cid), booking={})
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
