from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, StreamingResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import csv
import io
import logging
logger = logging.getLogger(__name__)

from siem_data_store import siem_store

router = APIRouter(prefix="/siem", tags=["siem"])


@router.on_event("startup")
async def _startup():
    # Re-ensure SIEM tables (the singleton already runs this on import, but
    # this protects against a restart where the DB wasn't yet reachable).
    try:
        siem_store._ensure_tables_exist()
    except Exception as e:
        logger.warning("SIEM startup ensure_tables failed: %s", e)


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

    # Aggregate per-IP stats from SIEM events
    siem_events = siem_store.get_all_events(limit=2000)
    ip_data: dict = {}

    def _ensure(ip: str) -> dict:
        if ip not in ip_data:
            ip_data[ip] = {
                "ip_address": ip, "total_uploads": 0, "successful": 0,
                "failed": 0, "total_records": 0, "_modules": set(),
                "first_seen": "", "last_seen": "", "_devices": set(),
                "last_user": "",
            }
        return ip_data[ip]

    def _parse_device(ua: str) -> str:
        ua_l = ua.lower()
        if "iphone" in ua_l or ("android" in ua_l and "mobile" in ua_l):
            return "Mobile Phone"
        if "ipad" in ua_l or "tablet" in ua_l:
            return "Tablet"
        if "android" in ua_l:
            return "Android Device"
        if "windows" in ua_l:
            return "Windows PC"
        if "macintosh" in ua_l or "mac os x" in ua_l:
            return "Mac"
        if "linux" in ua_l:
            return "Linux"
        return "Desktop" if ua else "Unknown"

    def _update_times(entry: dict, ts: str):
        if not ts:
            return
        if not entry["first_seen"] or ts < entry["first_seen"]:
            entry["first_seen"] = ts
        if ts > entry["last_seen"]:
            entry["last_seen"] = ts

    for e in siem_events:
        ip = e.get("ip_address") or "unknown"
        d = _ensure(ip)
        d["total_uploads"] += 1
        if e.get("status") == "success":
            d["successful"] += 1
        else:
            d["failed"] += 1
        d["total_records"] += int(e.get("records_imported") or 0)
        m = e.get("module", "")
        if m:
            d["_modules"].add(m)
        ua = e.get("user_agent", "")
        if ua and ua != "unknown":
            d["_devices"].add(_parse_device(ua))
        if e.get("username"):
            d["last_user"] = e["username"]
        elif e.get("user"):
            d["last_user"] = e["user"]
        _update_times(d, e.get("timestamp", ""))

    # Also pull from login_history to capture IPs from login events
    try:
        from auth_data_store import auth_store as _auth_store
        login_history = _auth_store.get_login_history(limit=1000)
        for entry in login_history:
            ip = entry.get("ip_address") or "unknown"
            d = _ensure(ip)
            d["_modules"].add("auth/login")
            ua = entry.get("user_agent", "")
            if ua:
                d["_devices"].add(_parse_device(ua))
                # Also derive device_name if stored
                stored_device = entry.get("device_name", "")
                if stored_device and stored_device != "Unknown":
                    d["_devices"].add(stored_device)
            if entry.get("username"):
                d["last_user"] = entry["username"]
            _update_times(d, entry.get("timestamp", ""))
    except Exception as _e:
        logger.warning("ip_tracker: could not load login_history: %s", _e)

    result = []
    for ip_addr, d in ip_data.items():
        result.append({
            "ip_address": ip_addr,
            "total_uploads": d["total_uploads"],
            "successful": d["successful"],
            "failed": d["failed"],
            "total_records": d["total_records"],
            "modules_used": len(d["_modules"]),
            "device_name": ", ".join(sorted(d["_devices"])) if d["_devices"] else "Unknown",
            "first_seen": d["first_seen"][:16] if d["first_seen"] else "—",
            "last_seen": d["last_seen"][:16] if d["last_seen"] else "—",
            "last_user": d["last_user"] or "—",
        })
    result.sort(key=lambda x: x["last_seen"], reverse=True)

    ctx.update(ip_summary=result)
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


@router.post("/alerts/{alert_id}/acknowledge", name="siem_acknowledge_alert")
async def acknowledge_alert(alert_id: str, request: Request, user=Depends(login_required)):
    """Mark a SIEM alert as acknowledged."""
    ok = siem_store.acknowledge_alert(alert_id)
    flash(request, "Alert acknowledged" if ok else "Alert not found", "success" if ok else "danger")
    referer = request.headers.get("referer") or "/siem/alerts"
    return RedirectResponse(referer, status_code=302)


@router.get("/export", name="siem_export_events")
async def export_events(request: Request, user=Depends(login_required)):
    """Stream a CSV export of all SIEM events (most recent first)."""
    rows = siem_store.get_all_events(limit=10000)
    buf = io.StringIO()
    fields = [
        "timestamp", "ip_address", "username", "module", "endpoint",
        "http_method", "status", "filename", "file_size_bytes",
        "records_imported", "user_agent", "referer", "details",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fields})
    buf.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="siem_events.csv"'}
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)


@router.get("/api/stats", name="siem_api_stats")
async def api_stats(request: Request, user=Depends(login_required)):
    """JSON endpoint for dashboards / external monitoring."""
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "stats": siem_store.get_dashboard_stats(),
        "alert_counts": siem_store.get_alert_counts(),
    })
