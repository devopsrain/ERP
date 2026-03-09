from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

from backup_data_store import backup_engine, backup_scheduler
from fastapi.responses import FileResponse as _FR
import os

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/dashboard", name="backup_dashboard")
async def dashboard(request: Request, user=Depends(require_auth("operator"))):
    ctx = template_context(request)
    ctx.update(
        stats=backup_engine.get_stats(),
        backups=backup_engine.list_backups(),
        log=backup_engine.get_backup_log(limit=20),
        scheduler_running=backup_scheduler.is_running,
        next_run=backup_scheduler.next_run,
    )
    return templates.TemplateResponse("backup/dashboard.html", ctx)


@router.post("/create", name="backup_create_backup")
async def create_backup(request: Request, user=Depends(admin_required)):
    form = await request.form()
    label = form.get("label", "").strip() or None
    username = request.session.get("username", "unknown")
    result = backup_engine.create_backup(label=label, triggered_by=f"manual:{username}")
    try:
        from siem_data_store import siem_store
        siem_store.log_upload_event(request, module="backup", endpoint="/backup/create",
                                    filename=result.get("archive_name", ""),
                                    status="success" if result.get("success") else "failed",
                                    user=username)
    except Exception:
        pass
    if result.get("success"):
        flash(request, f"Backup created: {result['archive_name']}", "success")
    else:
        flash(request, f"Backup failed: {result.get('error', 'Unknown')}", "error")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.get("/details/{archive_name}", name="backup_backup_details")
async def backup_details(archive_name: str, request: Request, user=Depends(require_auth("operator"))):
    details = backup_engine.get_backup_details(archive_name)
    if not details:
        flash(request, "Backup not found", "error")
        return RedirectResponse("/backup/dashboard", status_code=302)
    ctx = template_context(request)
    ctx.update(details=details)
    return templates.TemplateResponse("backup/details.html", ctx)


@router.get("/download/{archive_name}", name="backup_download_backup")
async def download_backup(archive_name: str, request: Request, user=Depends(admin_required)):
    archive_path = os.path.join(backup_engine.backup_dir, archive_name)
    if not os.path.exists(archive_path) or not archive_name.endswith(".zip"):
        flash(request, "Archive not found", "error")
        return RedirectResponse("/backup/dashboard", status_code=302)
    return _FR(archive_path, filename=archive_name, media_type="application/zip")


@router.post("/restore/{archive_name}", name="backup_restore_backup")
async def restore_backup(archive_name: str, request: Request, user=Depends(super_admin_required)):
    form = await request.form()
    confirm = form.get("confirm") == "yes"
    result = backup_engine.restore_backup(archive_name, confirm=confirm)
    if result.get("success"):
        flash(request, f"Restored {result['restored_files']} files from {archive_name}.", "success")
    else:
        flash(request, f"Restore failed: {result.get('error', 'Unknown')}", "error")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.post("/delete/{archive_name}", name="backup_delete_backup")
async def delete_backup(archive_name: str, request: Request, user=Depends(admin_required)):
    if backup_engine.delete_backup(archive_name):
        flash(request, f"Deleted {archive_name}", "success")
    else:
        flash(request, "Archive not found", "error")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.post("/purge", name="backup_purge_old")
async def purge_old(request: Request, user=Depends(admin_required)):
    form = await request.form()
    keep = int(form.get("keep_count", 10))
    result = backup_engine.purge_old_backups(keep_count=keep)
    flash(request, f"Purged {result.get('deleted', 0)} old backups.", "success")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.post("/start-scheduler", name="backup_start_scheduler")
async def start_scheduler(request: Request, user=Depends(admin_required)):
    backup_scheduler.start()
    flash(request, "Backup scheduler started.", "success")
    return RedirectResponse("/backup/dashboard", status_code=303)


@router.post("/stop-scheduler", name="backup_stop_scheduler")
async def stop_scheduler(request: Request, user=Depends(admin_required)):
    backup_scheduler.stop()
    flash(request, "Backup scheduler stopped.", "info")
    return RedirectResponse("/backup/dashboard", status_code=303)
