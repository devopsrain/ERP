"""
Seamless UX Routes — global search, notifications, session touch, health,
recently-viewed, command palette source. All read-only quick endpoints.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from deps import login_required, template_context, current_company
from template_engine import templates
from db import get_conn
from notifications_data_store import notifications_store
import time, logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["seamless"])

SESSION_WINDOW_SEC = 30 * 60   # 30-minute idle window
WARN_BEFORE_SEC    = 2 * 60    # warn 2 min before expiry


@router.on_event("startup")
async def _startup():
    notifications_store.ensure_schema()


# ── Sliding session touch ─────────────────────────────────────────────────────
@router.post("/api/session/touch", name="session_touch")
async def session_touch(request: Request):
    """Refresh login_time so active users stay logged in."""
    if request.session.get("logged_in"):
        request.session["login_time"] = int(time.time())
        remaining = SESSION_WINDOW_SEC
        return JSONResponse({"ok": True, "remaining": remaining})
    return JSONResponse({"ok": False}, status_code=401)


@router.get("/api/session/status", name="session_status")
async def session_status(request: Request):
    if not request.session.get("logged_in"):
        return JSONResponse({"logged_in": False})
    login_time = request.session.get("login_time", 0)
    elapsed = int(time.time() - login_time)
    remaining = max(0, SESSION_WINDOW_SEC - elapsed)
    return JSONResponse({
        "logged_in": True,
        "remaining": remaining,
        "warn_at": WARN_BEFORE_SEC,
        "username": request.session.get("username", "")
    })


# ── Global Search ─────────────────────────────────────────────────────────────
@router.get("/api/search", name="global_search")
async def global_search(request: Request, q: str = "", user=Depends(login_required)):
    q = (q or "").strip()
    if len(q) < 2:
        return JSONResponse({"results": []})
    cid = current_company(request)
    pattern = f"%{q}%"
    results = []

    queries = [
        # (label, icon, table, columns, link_template)
        ("Vendor",   "people",    "proc_vendors",
            ["name", "category", "contact_person"],
            "/procurement/vendors"),
        ("Project",  "kanban",    "pm_projects",
            ["name", "client_name"],
            "/project/{id}"),
        ("PO",       "receipt",   "proc_purchase_orders",
            ["po_number", "notes"],
            "/procurement/po/{id}"),
        ("PR",       "file-earmark-text", "proc_purchase_requisitions",
            ["item_description", "justification"],
            "/procurement/pr"),
        ("Booking",  "calendar-event", "ems_bookings",
            ["event_name", "client_name"],
            "/ems/bookings/{id}"),
        ("Venue",    "building",  "ems_venues",
            ["name", "description"],
            "/ems/venues"),
        ("Channel",  "chat-dots", "comm_channels",
            ["name"],
            "/comm/channel/{id}"),
    ]

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for label, icon, table, cols, link in queries:
                    where = " OR ".join(f"{c} ILIKE %s" for c in cols)
                    select_cols = "id, " + ", ".join(cols)
                    try:
                        cur.execute(
                            f"SELECT {select_cols} FROM {table} "
                            f"WHERE company_id=%s AND ({where}) LIMIT 5",
                            [cid] + [pattern] * len(cols)
                        )
                        for row in cur.fetchall():
                            row_d = dict(row)
                            title = row_d.get(cols[0]) or ""
                            subtitle = row_d.get(cols[1]) if len(cols) > 1 else ""
                            results.append({
                                "type": label,
                                "icon": icon,
                                "title": title,
                                "subtitle": subtitle or "",
                                "link": link.replace("{id}", str(row_d.get("id", ""))),
                            })
                    except Exception as e:
                        logger.debug("search skip %s: %s", table, e)
                        continue
    except Exception as e:
        logger.error("global_search: %s", e)

    return JSONResponse({"results": results[:30]})


# ── Notifications ─────────────────────────────────────────────────────────────
@router.get("/api/notifications", name="api_notifications")
async def api_notifications(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    uid = request.session.get("user_id", "")
    return JSONResponse({
        "items": notifications_store.get_recent(cid, uid, 20),
        "unread": notifications_store.unread_count(cid, uid),
    })


@router.post("/api/notifications/{notification_id}/read", name="api_notifications_read")
async def api_notifications_read(notification_id: str, request: Request, user=Depends(login_required)):
    uid = request.session.get("user_id", "")
    notifications_store.mark_read(notification_id, uid)
    return JSONResponse({"ok": True})


@router.post("/api/notifications/read-all", name="api_notifications_read_all")
async def api_notifications_read_all(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    uid = request.session.get("user_id", "")
    notifications_store.mark_all_read(cid, uid)
    return JSONResponse({"ok": True})


# ── Recently Viewed (session-stored, no DB) ───────────────────────────────────
@router.post("/api/recent/track", name="api_recent_track")
async def api_recent_track(request: Request, user=Depends(login_required)):
    """Body: {title, link, icon} — tracks last 10 visited entities in session."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    recent = request.session.get("recent_views", [])
    entry = {
        "title": (data.get("title") or "")[:80],
        "link":  data.get("link", ""),
        "icon":  data.get("icon", "clock-history"),
    }
    if not entry["title"] or not entry["link"]:
        return JSONResponse({"ok": False})
    # Dedupe and cap at 10
    recent = [r for r in recent if r.get("link") != entry["link"]]
    recent.insert(0, entry)
    request.session["recent_views"] = recent[:10]
    return JSONResponse({"ok": True})


@router.get("/api/recent", name="api_recent")
async def api_recent(request: Request, user=Depends(login_required)):
    return JSONResponse({"items": request.session.get("recent_views", [])})


# ── Health check ──────────────────────────────────────────────────────────────
@router.get("/health/details", name="health_details")
async def health_details(request: Request):
    """Detailed health page — DB up, tables count, last error, etc."""
    health = {
        "app": "ok",
        "db": "unknown",
        "tables": 0,
        "session_active": bool(request.session.get("logged_in")),
        "version": "1.0",
    }
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
                health["db"] = "ok"
                cur.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema='public'")
                health["tables"] = int(cur.fetchone()["n"] or 0)
    except Exception as e:
        health["db"] = "error"
        health["db_error"] = str(e)[:200]

    if "text/html" in request.headers.get("accept", ""):
        ctx = template_context(request)
        ctx["health"] = health
        return templates.TemplateResponse("health.html", ctx)
    return JSONResponse(health)


# ── Command palette catalog ───────────────────────────────────────────────────
@router.get("/api/commands", name="api_commands")
async def api_commands(request: Request, user=Depends(login_required)):
    """Static list of navigation commands for Ctrl+K palette."""
    commands = [
        {"title": "Dashboard", "link": "/auth/portal", "icon": "speedometer2", "category": "Navigate"},
        {"title": "Communication", "link": "/comm/", "icon": "chat-dots", "category": "Navigate"},
        {"title": "Project Management", "link": "/project/", "icon": "kanban", "category": "Navigate"},
        {"title": "Procurement", "link": "/procurement/", "icon": "cart-check", "category": "Navigate"},
        {"title": "Event Management", "link": "/ems/", "icon": "calendar-event", "category": "Navigate"},
        {"title": "Forecasting", "link": "/forecast/", "icon": "graph-up-arrow", "category": "Navigate"},
        {"title": "Cash Flow Forecast", "link": "/forecast/cashflow", "icon": "cash-stack", "category": "Navigate"},
        {"title": "Revenue Forecast", "link": "/forecast/revenue", "icon": "bar-chart-line", "category": "Navigate"},
        {"title": "Project EVM Forecast", "link": "/forecast/projects", "icon": "kanban", "category": "Navigate"},
        {"title": "Vendors", "link": "/procurement/vendors", "icon": "people", "category": "Navigate"},
        {"title": "Tenders", "link": "/procurement/tenders", "icon": "megaphone", "category": "Navigate"},
        {"title": "Inventory", "link": "/inventory/", "icon": "boxes", "category": "Navigate"},
        {"title": "Payroll", "link": "/payroll/", "icon": "cash-stack", "category": "Navigate"},
        {"title": "VAT Portal", "link": "/vat/", "icon": "receipt", "category": "Navigate"},
        {"title": "Multi-Company Settings", "link": "/company/dashboard", "icon": "diagram-3", "category": "Settings"},
        {"title": "SIEM / Security", "link": "/siem/", "icon": "shield-check", "category": "Settings"},
        {"title": "New Purchase Requisition", "link": "/procurement/pr/new", "icon": "plus-circle", "category": "Action"},
        {"title": "New Purchase Order", "link": "/procurement/po/new", "icon": "plus-circle", "category": "Action"},
        {"title": "New Vendor", "link": "/procurement/vendors/new", "icon": "plus-circle", "category": "Action"},
        {"title": "New Booking", "link": "/ems/bookings/new", "icon": "plus-circle", "category": "Action"},
        {"title": "New Venue", "link": "/ems/venues/new", "icon": "plus-circle", "category": "Action"},
        {"title": "New Project", "link": "/project/new", "icon": "plus-circle", "category": "Action"},
        {"title": "Logout", "link": "/auth/logout", "icon": "box-arrow-right", "category": "Account"},
        {"title": "System Health", "link": "/health/details", "icon": "heart-pulse", "category": "Account"},
    ]
    return JSONResponse({"commands": commands})
