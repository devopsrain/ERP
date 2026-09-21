"""
Report Builder Routes — /reports
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from deps import current_company, flash, login_required, require_auth, template_context
from template_engine import templates
from reports_catalog import (CatalogError, DATE_PRESETS, SOURCES, catalog_json, source_json,
                             validate_definition)
from reports_data_store import report_store
from reports_engine import (MIME, OUTPUT_DIR, PREVIEW_ROWS, ReportResult, build_meta, chart_data,
                            format_cell, render, run_report, safe_filename, save_output)
from reports_jobs import FORMATS, FREQUENCIES, compute_next_run, describe_schedule, run_schedule
from reports_mailer import parse_recipients

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])
manager_required = require_auth("manager")


@router.on_event("startup")
async def _startup():
    report_store.ensure_schema()


def _user(request: Request) -> str:
    return request.session.get("username", "") or ""


def _ctx(request: Request, **kw) -> dict:
    ctx = template_context(request)
    ctx.update(kw)
    return ctx


def _empty_result() -> ReportResult:
    return ReportResult(columns=[], rows=[])


# ── Dashboard ────────────────────────────────────────────────────────

@router.get("/", name="reports_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    report_store.seed_templates(cid)
    ctx = _ctx(request, stats=report_store.get_stats(cid),
               reports=report_store.list_definitions(cid),
               schedules=report_store.list_schedules(cid)[:5],
               runs=report_store.list_runs(cid, limit=8),
               sources=SOURCES, describe_schedule=describe_schedule)
    return templates.TemplateResponse("reports/dashboard.html", ctx)


# ── Static paths (MUST come before /{report_id}) ─────────────────────

@router.get("/list", name="reports_list")
async def report_list(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    source = request.query_params.get("source") or None
    ctx = _ctx(request, reports=report_store.list_definitions(cid, source), sources=SOURCES,
               source_filter=source or "")
    return templates.TemplateResponse("reports/list.html", ctx)


@router.get("/builder", name="reports_builder_new")
async def builder_new(request: Request, user=Depends(login_required)):
    source = request.query_params.get("source") or ""
    defn = {"source_key": source if source in SOURCES else "", "columns": [], "filters": [], "group_by": [],
            "aggregates": [], "sort": [], "date_column": None, "date_preset": "all", "limit": 5000,
            "chart": None, "name": "", "description": ""}
    ctx = _ctx(request, report=None, definition_json=json.dumps(defn), date_presets=DATE_PRESETS)
    return templates.TemplateResponse("reports/builder.html", ctx)


@router.get("/builder/{report_id}", name="reports_builder_edit")
async def builder_edit(report_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    r = report_store.get_definition(report_id, cid)
    if not r:
        flash(request, "Report not found", "error")
        return RedirectResponse("/reports/", status_code=303)
    defn = {k: r.get(k) for k in ("id", "name", "description", "source_key", "columns", "filters", "group_by",
                                  "aggregates", "sort", "date_column", "date_preset", "limit", "chart", "is_shared")}
    ctx = _ctx(request, report=r, definition_json=json.dumps(defn, default=str), date_presets=DATE_PRESETS)
    return templates.TemplateResponse("reports/builder.html", ctx)


# ── JSON API ─────────────────────────────────────────────────────────

@router.get("/api/catalog", name="reports_api_catalog")
async def api_catalog(request: Request, user=Depends(login_required)):
    return JSONResponse(catalog_json(report_store.table_columns))


@router.get("/api/catalog/{source}", name="reports_api_catalog_source")
async def api_catalog_source(source: str, request: Request, user=Depends(login_required)):
    s = SOURCES.get(source)
    if not s:
        return JSONResponse({"error": "unknown source"}, status_code=404)
    return JSONResponse(source_json(s, report_store.table_columns(s.table)))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/api/preview", name="reports_api_preview")
async def api_preview(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    body = await _json_body(request)
    try:
        defn = validate_definition(body)
        result = run_report(defn, cid, limit=PREVIEW_ROWS)
    except CatalogError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error("report preview failed: %s", e)
        return JSONResponse({"error": "Preview failed — check the server log"}, status_code=500)
    payload = result.to_json(PREVIEW_ROWS)
    payload["chart"] = chart_data(result, defn.get("chart"))
    return JSONResponse(payload)


@router.post("/api/save", name="reports_api_save")
async def api_save(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    body = await _json_body(request)
    try:
        defn = validate_definition(body)
    except CatalogError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not defn.get("name"):
        return JSONResponse({"error": "Please give the report a name"}, status_code=400)
    defn["is_shared"] = bool(body.get("is_shared", True))
    report_id = (body.get("id") or "").strip() or None
    if report_id:
        existing = report_store.get_definition(report_id, cid)
        if not existing:
            return JSONResponse({"error": "Report not found"}, status_code=404)
        if not report_store.update_definition(report_id, cid, defn):
            return JSONResponse({"error": "Failed to save report"}, status_code=500)
        return JSONResponse({"id": report_id, "url": f"/reports/{report_id}"})
    defn["created_by"] = _user(request)
    created = report_store.create_definition(cid, defn)
    if not created:
        return JSONResponse({"error": "Failed to save report"}, status_code=500)
    return JSONResponse({"id": created["id"], "url": f"/reports/{created['id']}"})


# ── Schedules ────────────────────────────────────────────────────────

def _schedule_ctx(request: Request, cid: str, schedule: dict) -> dict:
    return _ctx(request, schedule=schedule, reports=report_store.list_definitions(cid),
                frequencies=FREQUENCIES, formats=FORMATS)


def _parse_schedule_form(form, cid: str) -> tuple:
    """Return (data, error)."""
    d = {k: (v if v != "" else None) for k, v in form.items()}
    report_id = d.get("report_id")
    if not report_id or not report_store.get_definition(report_id, cid):
        return None, "Please choose a report"
    freq = (d.get("frequency") or "daily").lower()
    if freq not in FREQUENCIES:
        return None, "Invalid frequency"
    fmt = (d.get("format") or "pdf").lower()
    if fmt not in FORMATS:
        return None, "Invalid format"
    recipients = parse_recipients(d.get("recipients"))
    if not recipients:
        return None, "At least one valid recipient email is required"
    try:
        hour, minute = int(d.get("hour") or 7), int(d.get("minute") or 0)
        weekday = int(d["weekday"]) if freq == "weekly" and d.get("weekday") is not None else None
        dom = int(d["day_of_month"]) if freq == "monthly" and d.get("day_of_month") is not None else None
    except ValueError:
        return None, "Invalid time settings"
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, "Hour must be 0-23 and minute 0-59"
    if freq == "weekly":
        weekday = 0 if weekday is None else weekday % 7
    if freq == "monthly":
        dom = min(max(dom or 1, 1), 31)
    return {"report_id": report_id, "frequency": freq, "hour": hour, "minute": minute, "weekday": weekday,
            "day_of_month": dom, "format": fmt, "recipients": ", ".join(recipients),
            "subject": (d.get("subject") or "").strip(), "is_active": d.get("is_active") in ("on", "1", "true", True)}, None


@router.get("/schedules", name="reports_schedules")
async def schedules(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    ctx = _ctx(request, schedules=report_store.list_schedules(cid), describe_schedule=describe_schedule)
    return templates.TemplateResponse("reports/schedules.html", ctx)


@router.get("/schedules/new", name="reports_schedule_new_get")
async def schedule_new_get(request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    pre = request.query_params.get("report_id") or ""
    schedule = {"report_id": pre, "frequency": "daily", "hour": 7, "minute": 0, "format": "pdf",
                "recipients": "", "subject": "", "is_active": True}
    return templates.TemplateResponse("reports/schedule_form.html", _schedule_ctx(request, cid, schedule))


@router.post("/schedules/new", name="reports_schedule_new_post")
async def schedule_new_post(request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    form = await request.form()
    data, err = _parse_schedule_form(form, cid)
    if err:
        flash(request, err, "error")
        return RedirectResponse("/reports/schedules/new", status_code=303)
    data["created_by"] = _user(request)
    nxt = compute_next_run(data["frequency"], data["hour"], data["minute"], data["weekday"], data["day_of_month"])
    if report_store.create_schedule(cid, data, nxt):
        flash(request, f"Schedule created — next run {nxt:%Y-%m-%d %H:%M}", "success")
    else:
        flash(request, "Failed to create schedule", "error")
    return RedirectResponse("/reports/schedules", status_code=303)


@router.get("/schedules/{schedule_id}/edit", name="reports_schedule_edit_get")
async def schedule_edit_get(schedule_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    s = report_store.get_schedule(schedule_id, cid)
    if not s:
        flash(request, "Schedule not found", "error")
        return RedirectResponse("/reports/schedules", status_code=303)
    return templates.TemplateResponse("reports/schedule_form.html", _schedule_ctx(request, cid, s))


@router.post("/schedules/{schedule_id}/edit", name="reports_schedule_edit_post")
async def schedule_edit_post(schedule_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    if not report_store.get_schedule(schedule_id, cid):
        flash(request, "Schedule not found", "error")
        return RedirectResponse("/reports/schedules", status_code=303)
    form = await request.form()
    data, err = _parse_schedule_form(form, cid)
    if err:
        flash(request, err, "error")
        return RedirectResponse(f"/reports/schedules/{schedule_id}/edit", status_code=303)
    nxt = compute_next_run(data["frequency"], data["hour"], data["minute"], data["weekday"], data["day_of_month"])
    if report_store.update_schedule(schedule_id, cid, data, nxt):
        flash(request, "Schedule updated", "success")
    else:
        flash(request, "Failed to update schedule", "error")
    return RedirectResponse("/reports/schedules", status_code=303)


@router.post("/schedules/{schedule_id}/toggle", name="reports_schedule_toggle")
async def schedule_toggle(schedule_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    s = report_store.get_schedule(schedule_id, cid)
    if not s:
        flash(request, "Schedule not found", "error")
    else:
        active = not s.get("is_active")
        nxt = compute_next_run(s["frequency"], s["hour"], s["minute"], s.get("weekday"),
                               s.get("day_of_month")) if active else None
        report_store.set_schedule_active(schedule_id, cid, active, nxt)
        flash(request, "Schedule activated" if active else "Schedule paused", "success")
    return RedirectResponse("/reports/schedules", status_code=303)


@router.post("/schedules/{schedule_id}/delete", name="reports_schedule_delete")
async def schedule_delete(schedule_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    if report_store.delete_schedule(schedule_id, cid):
        flash(request, "Schedule deleted", "success")
    else:
        flash(request, "Failed to delete schedule", "error")
    return RedirectResponse("/reports/schedules", status_code=303)


@router.post("/schedules/{schedule_id}/run", name="reports_schedule_run_now")
async def schedule_run_now(schedule_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    s = report_store.get_schedule(schedule_id, cid)
    if not s:
        flash(request, "Schedule not found", "error")
        return RedirectResponse("/reports/schedules", status_code=303)
    from db import run_sync
    out = await run_sync(run_schedule, s, _user(request) or "manual")
    if out["ok"]:
        flash(request, "Report generated" + (" and emailed" if out["emailed"] else
                                             " (email skipped — RESEND_API_KEY not set or no recipients)"), "success")
    else:
        flash(request, f"Run failed: {out['error']}", "error")
    return RedirectResponse("/reports/runs", status_code=303)


# ── Runs ─────────────────────────────────────────────────────────────

@router.get("/runs", name="reports_runs")
async def runs(request: Request, user=Depends(login_required)):
    cid = current_company(request)
    report_id = request.query_params.get("report_id") or None
    ctx = _ctx(request, runs=report_store.list_runs(cid, report_id, 200), report_id=report_id or "",
               os_path_exists=os.path.exists)
    return templates.TemplateResponse("reports/runs.html", ctx)


@router.get("/runs/{run_id}/download", name="reports_run_download")
async def run_download(run_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    r = report_store.get_run(run_id, cid)
    path = (r or {}).get("output_path")
    # Only serve files inside our own output directory
    if not r or not path or os.path.dirname(os.path.abspath(path)) != os.path.abspath(OUTPUT_DIR) \
            or not os.path.exists(path):
        flash(request, "Output file is no longer available — run the report again", "error")
        return RedirectResponse("/reports/runs", status_code=303)
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return FileResponse(path, filename=os.path.basename(path), media_type=MIME.get(ext, "application/octet-stream"))


# ── Per-report routes (dynamic — keep last) ──────────────────────────

@router.get("/{report_id}", name="reports_view")
async def report_view(report_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    r = report_store.get_definition(report_id, cid)
    if not r:
        flash(request, "Report not found", "error")
        return RedirectResponse("/reports/", status_code=303)
    error = None
    try:
        from db import run_sync
        result = await run_sync(run_report, r, cid)
    except CatalogError as e:
        error, result = str(e), _empty_result()
    except Exception as e:
        logger.error("report %s run failed: %s", report_id, e)
        error, result = "The report could not be run — check the server log", _empty_result()
    meta = build_meta(r, cid, result)
    ctx = _ctx(request, report=r, result=result, rows=result.rows[:1000], meta=meta, error=error,
               chart=chart_data(result, r.get("chart")), format_cell=format_cell, NUMBER="number",
               schedules=[s for s in report_store.list_schedules(cid) if s.get("report_id") == report_id],
               runs=report_store.list_runs(cid, report_id, 5), describe_schedule=describe_schedule)
    return templates.TemplateResponse("reports/view.html", ctx)


@router.get("/{report_id}/print", name="reports_print")
async def report_print(report_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    r = report_store.get_definition(report_id, cid)
    if not r:
        flash(request, "Report not found", "error")
        return RedirectResponse("/reports/", status_code=303)
    try:
        from db import run_sync
        result = await run_sync(run_report, r, cid)
    except Exception as e:
        logger.error("report %s print failed: %s", report_id, e)
        result = _empty_result()
    meta = build_meta(r, cid, result)
    return HTMLResponse(render(result, meta, "html", r.get("chart")).decode("utf-8"))


@router.get("/{report_id}/export/{fmt}", name="reports_export")
async def report_export(report_id: str, fmt: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    fmt = (fmt or "").lower()
    if fmt not in FORMATS:
        flash(request, "Unknown export format", "error")
        return RedirectResponse(f"/reports/{report_id}", status_code=303)
    r = report_store.get_definition(report_id, cid)
    if not r:
        flash(request, "Report not found", "error")
        return RedirectResponse("/reports/", status_code=303)
    run_id = report_store.start_run(cid, report_id, _user(request) or "export", output_format=fmt)
    try:
        from db import run_sync
        result = await run_sync(run_report, r, cid)
        meta = build_meta(r, cid, result)
        content = await run_sync(render, result, meta, fmt, r.get("chart"))
        path = save_output(content, r.get("name") or "report", fmt)
        report_store.finish_run(run_id, "ok", result.row_count, None, path)
    except Exception as e:
        logger.error("report %s export failed: %s", report_id, e)
        report_store.finish_run(run_id, "error", 0, str(e), None)
        flash(request, f"Export failed: {e}", "error")
        return RedirectResponse(f"/reports/{report_id}", status_code=303)
    return Response(content=content, media_type=MIME[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{safe_filename(r.get("name"), fmt)}"'})


@router.post("/{report_id}/delete", name="reports_delete")
async def report_delete(report_id: str, request: Request, user=Depends(manager_required)):
    cid = current_company(request)
    if report_store.delete_definition(report_id, cid):
        flash(request, "Report deleted", "success")
    else:
        flash(request, "Failed to delete report", "error")
    return RedirectResponse("/reports/list", status_code=303)


@router.post("/{report_id}/duplicate", name="reports_duplicate")
async def report_duplicate(report_id: str, request: Request, user=Depends(login_required)):
    cid = current_company(request)
    r = report_store.get_definition(report_id, cid)
    if not r:
        flash(request, "Report not found", "error")
        return RedirectResponse("/reports/list", status_code=303)
    d = dict(r); d["name"] = f"{r.get('name')} (copy)"; d["is_template"] = False; d["created_by"] = _user(request)
    new = report_store.create_definition(cid, d)
    if new:
        flash(request, "Report duplicated — you are editing the copy", "success")
        return RedirectResponse(f"/reports/builder/{new['id']}", status_code=303)
    flash(request, "Failed to duplicate report", "error")
    return RedirectResponse(f"/reports/{report_id}", status_code=303)
