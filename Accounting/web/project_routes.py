"""
Project Management Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from deps import flash, template_context, login_required
from template_engine import templates
from project_data_store import project_store
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/project", tags=["project"])


@router.on_event("startup")
async def _startup():
    project_store.ensure_schema()


@router.get("/", name="project_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx.update(projects=project_store.get_projects(cid), stats=project_store.get_stats(cid))
    return templates.TemplateResponse("project/dashboard.html", ctx)


@router.get("/new", name="project_new_get")
async def new_project_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("project/project_form.html", {**template_context(request), "project": {}, "action": "create"})


@router.post("/new", name="project_new_post")
async def new_project_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["created_by"] = request.session.get("username", "")
    p = project_store.create_project(cid, data)
    if p:
        flash(request, "Project created", "success")
        return RedirectResponse(f"/project/{p['id']}", status_code=303)
    flash(request, "Failed to create project", "error")
    return RedirectResponse("/project/new", status_code=303)


# Contractors & Consultants (static paths — must be registered BEFORE /{project_id})
@router.get("/contractors", name="project_contractors")
async def contractors_list(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    type_filter = request.query_params.get("type") or ""
    contractors = project_store.get_contractors(cid, type_filter or None)
    ctx = template_context(request)
    ctx.update(contractors=contractors, type_filter=type_filter)
    return templates.TemplateResponse("project/contractors.html", ctx)


@router.get("/contractors/new", name="project_contractor_new_get")
async def contractor_new_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse(
        "project/contractor_form.html",
        {**template_context(request), "contractor": {}, "action": "create"}
    )


@router.post("/contractors/new", name="project_contractor_new_post")
async def contractor_new_post(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    c = project_store.create_contractor(cid, data)
    if c:
        flash(request, "Contractor added", "success")
        return RedirectResponse("/project/contractors", status_code=303)
    flash(request, "Failed to add contractor", "error")
    return RedirectResponse("/project/contractors/new", status_code=303)


@router.post("/contractors/{contractor_id}/edit", name="project_contractor_edit")
async def contractor_edit(contractor_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if project_store.update_contractor(contractor_id, cid, data):
        flash(request, "Contractor updated", "success")
    else:
        flash(request, "Failed to update contractor", "error")
    return RedirectResponse("/project/contractors", status_code=303)


@router.post("/contractors/{contractor_id}/deactivate", name="project_contractor_deactivate")
async def contractor_deactivate(contractor_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    project_store.deactivate_contractor(contractor_id, cid)
    flash(request, "Contractor deactivated", "success")
    return RedirectResponse("/project/contractors", status_code=303)


# Reports (static paths — must be registered BEFORE /{project_id})
@router.get("/reports/budget", name="project_report_budget")
async def report_budget(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    report = project_store.budget_vs_actual(cid)
    ctx = template_context(request)
    ctx.update(rows=report["rows"], totals=report["totals"])
    return templates.TemplateResponse("project/report_budget.html", ctx)


@router.get("/reports/progress", name="project_report_progress")
async def report_progress(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx.update(rows=project_store.progress_report(cid))
    return templates.TemplateResponse("project/report_progress.html", ctx)


@router.get("/reports/delays", name="project_report_delays")
async def report_delays(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    ctx = template_context(request)
    ctx.update(**project_store.delay_analysis(cid))
    return templates.TemplateResponse("project/report_delays.html", ctx)


@router.get("/{project_id}", name="project_detail")
async def project_detail(project_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    p = project_store.get_project(project_id, cid)
    if not p:
        flash(request, "Project not found", "error")
        return RedirectResponse("/project/", status_code=303)
    wbs = project_store.get_wbs(project_id)
    tasks = project_store.get_tasks(project_id)
    tasks_by_wbs = {}
    for t in tasks:
        tasks_by_wbs.setdefault(t["wbs_element_id"], []).append(t)
    progress_by_wbs = {w["id"]: project_store.get_wbs_progress(w["id"]) for w in wbs}
    reports = project_store.get_site_reports(project_id)
    payments = project_store.get_payments(project_id, cid)
    payments_total = round(sum(float(x["amount"] or 0) for x in payments), 2)
    ctx = template_context(request)
    ctx.update(project=p, wbs=wbs, tasks_by_wbs=tasks_by_wbs,
               progress_by_wbs=progress_by_wbs, reports=reports,
               payments=payments, payments_total=payments_total)
    return templates.TemplateResponse("project/project_detail.html", ctx)


@router.post("/{project_id}/edit", name="project_edit")
async def project_edit(project_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    form = await request.form()
    data = {k: v for k, v in form.items()}
    project_store.update_project(project_id, cid, data)
    flash(request, "Project updated", "success")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/{project_id}/delete", name="project_delete")
async def project_delete(project_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    project_store.delete_project(project_id, cid)
    flash(request, "Project deleted", "success")
    return RedirectResponse("/project/", status_code=303)


# WBS
@router.post("/{project_id}/wbs/add", name="project_add_wbs")
async def add_wbs(project_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    project_store.create_wbs_element(
        project_id, form.get("title",""), form.get("parent_id") or None, int(form.get("sequence",0))
    )
    flash(request, "WBS element added", "success")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/{project_id}/wbs/{wbs_id}/delete", name="project_delete_wbs")
async def delete_wbs(project_id: str, wbs_id: str, request: Request, user=Depends(login_required)):
    project_store.delete_wbs_element(wbs_id, project_id)
    flash(request, "WBS element removed", "success")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


# Tasks
@router.post("/{project_id}/task/add", name="project_add_task")
async def add_task(project_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["project_id"] = project_id
    result = project_store.create_task(data)
    if result:
        flash(request, "Task created", "success")
    else:
        flash(request, "Failed to create task", "error")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/task/{task_id}/status", name="project_task_status")
async def task_status(task_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    new_status = form.get("status", "")
    result = project_store.update_task_status(task_id, new_status)
    if not result["ok"]:
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/task/{task_id}/edit", name="project_edit_task")
async def edit_task(task_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {k: v for k, v in form.items()}
    project_store.update_task(task_id, data)
    t = project_store.get_task(task_id)
    if t:
        return RedirectResponse(f"/project/{t['project_id']}", status_code=303)
    return RedirectResponse("/project/", status_code=303)


@router.post("/task/{task_id}/delete", name="project_delete_task")
async def delete_task(task_id: str, request: Request, user=Depends(login_required)):
    t = project_store.get_task(task_id)
    project_id = t["project_id"] if t else ""
    project_store.delete_task(task_id)
    flash(request, "Task deleted", "success")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


# Payments
@router.post("/{project_id}/payment/add", name="project_add_payment")
async def add_payment(project_id: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    if not project_store.get_project(project_id, cid):
        flash(request, "Project not found", "error")
        return RedirectResponse("/project/", status_code=303)
    form = await request.form()
    data = {k: v for k, v in form.items()}
    if project_store.create_payment(project_id, cid, data):
        flash(request, "Payment recorded", "success")
    else:
        flash(request, "Failed to record payment", "error")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


# Site Reports
@router.post("/{project_id}/report/add", name="project_add_report")
async def add_report(project_id: str, request: Request, user=Depends(login_required)):
    form = await request.form()
    data = {k: v for k, v in form.items()}
    data["submitted_by"] = request.session.get("username", "")
    project_store.create_site_report(project_id, data)
    flash(request, "Site report submitted", "success")
    return RedirectResponse(f"/project/{project_id}", status_code=303)
