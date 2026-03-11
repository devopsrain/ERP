from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

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
    events = siem_store.get_all_events(limit=200)
    ip_summary = {}
    for e in events:
        ip = e.get("ip_address", "unknown")
        if ip not in ip_summary:
            ip_summary[ip] = {"ip": ip, "count": 0, "last_seen": e.get("timestamp", ""), "modules": set()}
        ip_summary[ip]["count"] += 1
        ip_summary[ip]["modules"].add(e.get("module", ""))
    for ip in ip_summary:
        ip_summary[ip]["modules"] = list(ip_summary[ip]["modules"])
    ctx.update(ip_summary=list(ip_summary.values()))
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
