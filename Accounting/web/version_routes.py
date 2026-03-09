from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

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
