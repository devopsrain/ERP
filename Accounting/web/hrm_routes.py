from datetime import date, datetime

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from deps import login_required, admin_required, flash, template_context
from hrm_data_store import hrm_store
from template_engine import templates

router = APIRouter(prefix="/hrm", tags=["hrm"])


def _company(request: Request) -> str:
    return (
        getattr(request.state, "company_id", None)
        or request.session.get("current_company_id")
        or request.session.get("company_id")
        or "default"
    )


def _user(request: Request) -> str:
    return request.session.get("username", "system")


def _form_dict(form) -> dict:
    return {k: v for k, v in form.items()}


def _parse_date(value: str):
    """'' → None; 'YYYY-MM-DD' → date."""
    if not value or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _leave_days(start, end) -> int:
    if start and end and end >= start:
        return (end - start).days + 1
    return 0


def _ess_employee(request: Request):
    """Match the logged-in user to their employee record by username/email/name."""
    cid = _company(request)
    emp = hrm_store.get_employee_for_user(
        cid,
        username=request.session.get("username", ""),
        email=request.session.get("email", ""),
    )
    if not emp:
        full_name = request.session.get("full_name", "")
        if full_name:
            emp = hrm_store.get_employee_for_user(cid, username=full_name)
    return emp


# ═════════════════════════════════════════════════════════════════════════
# HR analytics — HTML dashboard for browsers, JSON preserved for API clients
# ═════════════════════════════════════════════════════════════════════════

@router.get("/analytics", name="hrm_analytics")
async def hrm_analytics(request: Request, user=Depends(login_required)):
    cid = _company(request)
    accept = request.headers.get("accept", "")
    wants_json = (
        request.query_params.get("format") == "json"
        or ("application/json" in accept and "text/html" not in accept)
    )
    if wants_json:
        return JSONResponse({
            "status": "ok",
            "company_id": cid,
            "data": hrm_store.get_hr_analytics(cid),
        })
    hrm_store.ensure_schema()
    ctx = template_context(request)
    ctx.update(
        dashboard=hrm_store.get_hr_dashboard(cid),
        analytics=hrm_store.get_hr_analytics(cid),
        current_year=date.today().year,
    )
    return templates.TemplateResponse("hrm/analytics.html", ctx)


# ═════════════════════════════════════════════════════════════════════════
# Leave administration
# ═════════════════════════════════════════════════════════════════════════

@router.get("/leave", name="hrm_leave_admin")
async def leave_admin(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    status = request.query_params.get("status", "")
    ctx = template_context(request)
    ctx.update(
        leave_requests=hrm_store.list_leave_requests(cid, status=status),
        leave_types=hrm_store.list_leave_types(cid, active_only=True),
        employees=hrm_store.list_employees(cid),
        status_filter=status,
    )
    return templates.TemplateResponse("hrm/leave_admin.html", ctx)


@router.post("/leave/new", name="hrm_leave_new")
async def leave_new(request: Request, user=Depends(login_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    start = _parse_date(data.get("start_date", ""))
    end = _parse_date(data.get("end_date", ""))
    if not data.get("employee_id") or not start or not end or end < start:
        flash(request, "Employee and a valid date range are required", "error")
        return RedirectResponse("/hrm/leave", status_code=303)
    data.update(
        company_id=cid, start_date=start, end_date=end,
        days_requested=int(data.get("days_requested") or 0) or _leave_days(start, end),
        status="pending",
    )
    leave_id = hrm_store.create_leave_request(data)
    if leave_id:
        flash(request, "Leave request submitted", "success")
    else:
        flash(request, "Failed to create leave request", "error")
    return RedirectResponse("/hrm/leave", status_code=303)


@router.get("/leave/types", name="hrm_leave_types")
async def leave_types(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    ctx = template_context(request)
    ctx["leave_types"] = hrm_store.list_leave_types(cid)
    return templates.TemplateResponse("hrm/leave_types.html", ctx)


@router.post("/leave/types/new", name="hrm_leave_type_new")
async def leave_type_new(request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    if not (data.get("name") or "").strip():
        flash(request, "Leave type name is required", "error")
        return RedirectResponse("/hrm/leave/types", status_code=303)
    data["company_id"] = cid
    if hrm_store.create_leave_type(data):
        flash(request, f"Leave type '{data['name']}' created", "success")
    else:
        flash(request, "Failed to create leave type", "error")
    return RedirectResponse("/hrm/leave/types", status_code=303)


@router.post("/leave/types/{leave_type_id}/update", name="hrm_leave_type_update")
async def leave_type_update(leave_type_id: str, request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    data.setdefault("active", "")  # unchecked checkbox is absent from the form
    if hrm_store.update_leave_type(leave_type_id, cid, data):
        flash(request, "Leave type updated", "success")
    else:
        flash(request, "Failed to update leave type", "error")
    return RedirectResponse("/hrm/leave/types", status_code=303)


@router.get("/leave/balances", name="hrm_leave_balances")
async def leave_balances(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    employee_id = request.query_params.get("employee_id", "")
    ctx = template_context(request)
    ctx.update(
        balances=hrm_store.get_leave_balances(cid, employee_id=employee_id),
        employees=hrm_store.list_employees(cid),
        employee_filter=employee_id,
        current_year=date.today().year,
    )
    return templates.TemplateResponse("hrm/leave_balances.html", ctx)


@router.get("/leave/calendar", name="hrm_leave_calendar")
async def leave_calendar(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    month = request.query_params.get("month", "") or date.today().strftime("%Y-%m")
    ctx = template_context(request)
    ctx.update(
        entries=hrm_store.get_leave_calendar(cid, month=month),
        month=month,
    )
    return templates.TemplateResponse("hrm/leave_calendar.html", ctx)


@router.get("/leave/report", name="hrm_leave_report")
async def leave_report(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    try:
        year = int(request.query_params.get("year", "") or date.today().year)
    except ValueError:
        year = date.today().year
    ctx = template_context(request)
    ctx.update(
        report=hrm_store.get_leave_report(cid, year=year),
        year=year,
    )
    return templates.TemplateResponse("hrm/leave_report.html", ctx)


@router.post("/leave/{leave_id}/decision", name="hrm_leave_decision")
async def leave_decision(leave_id: str, request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    decision = data.get("decision", "")
    if decision not in ("approved", "rejected"):
        flash(request, "Invalid decision", "error")
        return RedirectResponse("/hrm/leave", status_code=303)
    ok = hrm_store.update_leave_status(
        leave_id=leave_id,
        status=decision,
        approver_id=request.session.get("user_id", "") or _user(request),
        approver_note=data.get("approver_note", ""),
        company_id=cid,
    )
    flash(request, f"Leave request {decision}" if ok else "Failed to update leave request",
          "success" if ok else "error")
    return RedirectResponse("/hrm/leave", status_code=303)


# ═════════════════════════════════════════════════════════════════════════
# Employee Self-Service (ESS)
# ═════════════════════════════════════════════════════════════════════════

@router.get("/ess", name="hrm_ess")
async def ess_home(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    emp = _ess_employee(request)
    ctx = template_context(request)
    if emp:
        eid = emp["employee_id"]
        ctx.update(
            employee=emp,
            terms=hrm_store.get_employee_terms(eid, cid),
            leave_types=hrm_store.list_leave_types(cid, active_only=True),
            my_leave=hrm_store.list_leave_requests(cid, employee_id=eid),
            balances=hrm_store.get_leave_balances(cid, employee_id=eid),
            my_grievances=hrm_store.list_grievances(cid, employee_id=eid),
            payslip_url=f"/payroll/employees/{eid}/payslip",
        )
    else:
        ctx.update(
            employee=None, terms=None, leave_types=[], my_leave=[],
            balances=[], my_grievances=[], payslip_url="",
        )
    return templates.TemplateResponse("hrm/ess.html", ctx)


@router.post("/ess/leave", name="hrm_ess_leave")
async def ess_leave(request: Request, user=Depends(login_required)):
    cid = _company(request)
    emp = _ess_employee(request)
    if not emp:
        flash(request, "No employee record is linked to your account", "error")
        return RedirectResponse("/hrm/ess", status_code=303)
    data = _form_dict(await request.form())
    start = _parse_date(data.get("start_date", ""))
    end = _parse_date(data.get("end_date", ""))
    if not start or not end or end < start:
        flash(request, "A valid date range is required", "error")
        return RedirectResponse("/hrm/ess", status_code=303)
    data.update(
        company_id=cid, employee_id=emp["employee_id"],
        start_date=start, end_date=end,
        days_requested=_leave_days(start, end), status="pending",
    )
    if hrm_store.create_leave_request(data):
        flash(request, "Leave request submitted for approval", "success")
    else:
        flash(request, "Failed to submit leave request", "error")
    return RedirectResponse("/hrm/ess", status_code=303)


@router.post("/ess/profile", name="hrm_ess_profile")
async def ess_profile(request: Request, user=Depends(login_required)):
    cid = _company(request)
    emp = _ess_employee(request)
    if not emp:
        flash(request, "No employee record is linked to your account", "error")
        return RedirectResponse("/hrm/ess", status_code=303)
    data = _form_dict(await request.form())
    if hrm_store.update_employee_contact(emp["employee_id"], cid, data):
        flash(request, "Contact details updated", "success")
    else:
        flash(request, "Failed to update contact details", "error")
    return RedirectResponse("/hrm/ess", status_code=303)


@router.post("/ess/grievances/submit", name="hrm_ess_grievance_submit")
async def ess_grievance_submit(request: Request, user=Depends(login_required)):
    cid = _company(request)
    emp = _ess_employee(request)
    if not emp:
        flash(request, "No employee record is linked to your account", "error")
        return RedirectResponse("/hrm/ess", status_code=303)
    data = _form_dict(await request.form())
    if not (data.get("title") or "").strip():
        flash(request, "Grievance title is required", "error")
        return RedirectResponse("/hrm/ess", status_code=303)
    data.update(company_id=cid, employee_id=emp["employee_id"], status="open")
    if hrm_store.create_grievance(data):
        flash(request, "Grievance submitted", "success")
    else:
        flash(request, "Failed to submit grievance", "error")
    return RedirectResponse("/hrm/ess", status_code=303)


# ═════════════════════════════════════════════════════════════════════════
# Grievance administration
# ═════════════════════════════════════════════════════════════════════════

@router.get("/grievances", name="hrm_grievances")
async def grievances_admin(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    status = request.query_params.get("status", "")
    ctx = template_context(request)
    ctx.update(
        grievances=hrm_store.list_grievances(cid, status=status),
        status_filter=status,
    )
    return templates.TemplateResponse("hrm/grievances.html", ctx)


@router.post("/grievances/{grievance_id}/status", name="hrm_grievance_status")
async def grievance_status(grievance_id: str, request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    ok = hrm_store.update_grievance_status(
        grievance_id, cid,
        status=data.get("status", ""),
        resolution_note=data.get("resolution_note", ""),
    )
    flash(request, "Grievance updated" if ok else "Failed to update grievance",
          "success" if ok else "error")
    return RedirectResponse("/hrm/grievances", status_code=303)


# ═════════════════════════════════════════════════════════════════════════
# Employee records — terms, KPI/OKR, disciplinary, promotions
# ═════════════════════════════════════════════════════════════════════════

@router.get("/employees", name="hrm_employees")
async def employees_list(request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    ctx = template_context(request)
    ctx["employee_terms"] = hrm_store.list_employee_terms(cid)
    return templates.TemplateResponse("hrm/employees.html", ctx)


@router.post("/employees/terms", name="hrm_employee_terms_save")
async def employee_terms_save(request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    data["company_id"] = cid
    if not data.get("employee_id"):
        flash(request, "Employee is required", "error")
        return RedirectResponse("/hrm/employees", status_code=303)
    if hrm_store.upsert_employee_terms(data):
        flash(request, "Employment terms saved", "success")
    else:
        flash(request, "Failed to save employment terms", "error")
    return RedirectResponse(f"/hrm/employees/{data['employee_id']}", status_code=303)


@router.get("/employees/{employee_id}", name="hrm_employee_detail")
async def employee_detail(employee_id: str, request: Request, user=Depends(login_required)):
    hrm_store.ensure_schema()
    cid = _company(request)
    emp = hrm_store.get_employee(employee_id, cid)
    if not emp:
        flash(request, "Employee not found", "error")
        return RedirectResponse("/hrm/employees", status_code=303)
    ctx = template_context(request)
    ctx.update(
        employee=emp,
        terms=hrm_store.get_employee_terms(employee_id, cid),
        kpi_records=hrm_store.list_kpi_records(cid, employee_id=employee_id),
        disciplinary_records=hrm_store.list_disciplinary_records(cid, employee_id=employee_id),
        promotions=hrm_store.list_promotions(cid, employee_id=employee_id),
        payslip_url=f"/payroll/employees/{employee_id}/payslip",
    )
    return templates.TemplateResponse("hrm/employee_detail.html", ctx)


@router.post("/employees/{employee_id}/kpi", name="hrm_employee_kpi_new")
async def employee_kpi_new(employee_id: str, request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    data.update(company_id=cid, employee_id=employee_id)
    if hrm_store.create_kpi_record(data):
        flash(request, "KPI/OKR record added", "success")
    else:
        flash(request, "Failed to add KPI/OKR record", "error")
    return RedirectResponse(f"/hrm/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/disciplinary", name="hrm_employee_disciplinary_new")
async def employee_disciplinary_new(employee_id: str, request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    data.update(company_id=cid, employee_id=employee_id)
    if hrm_store.create_disciplinary_record(data):
        flash(request, "Disciplinary record added", "success")
    else:
        flash(request, "Failed to add disciplinary record", "error")
    return RedirectResponse(f"/hrm/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/promotion", name="hrm_employee_promotion_new")
async def employee_promotion_new(employee_id: str, request: Request, user=Depends(admin_required)):
    cid = _company(request)
    data = _form_dict(await request.form())
    data.update(company_id=cid, employee_id=employee_id)
    if hrm_store.create_promotion(data):
        flash(request, "Promotion/increment recorded", "success")
    else:
        flash(request, "Failed to record promotion/increment", "error")
    return RedirectResponse(f"/hrm/employees/{employee_id}", status_code=303)


# ═════════════════════════════════════════════════════════════════════════
# Pre-existing JSON API endpoints (unchanged)
# ═════════════════════════════════════════════════════════════════════════

@router.post("/payroll/runs", name="hrm_create_payroll_run")
async def create_payroll_run(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    payload["created_by"] = _user(request)
    run_id = hrm_store.create_payroll_run(payload)
    if not run_id:
        return JSONResponse({"status": "error", "error": "Failed to create payroll run"}, status_code=500)
    return JSONResponse({"status": "ok", "run_id": run_id})


@router.post("/leave/requests", name="hrm_create_leave_request")
async def create_leave_request(request: Request, user=Depends(login_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    payload.setdefault("employee_id", request.session.get("user_id", ""))
    leave_id = hrm_store.create_leave_request(payload)
    if not leave_id:
        return JSONResponse({"status": "error", "error": "Failed to create leave request"}, status_code=500)
    return JSONResponse({"status": "ok", "leave_id": leave_id})


@router.post("/leave/requests/{leave_id}/approve", name="hrm_approve_leave_request")
async def approve_leave_request(leave_id: str, request: Request, user=Depends(admin_required)):
    payload = await request.json()
    ok = hrm_store.update_leave_status(
        leave_id=leave_id,
        status=payload.get("status", "approved"),
        approver_id=request.session.get("user_id", ""),
        approver_note=payload.get("approver_note", ""),
        company_id=_company(request),
    )
    return JSONResponse({"status": "ok" if ok else "error", "leave_id": leave_id})


@router.post("/learning/training-records", name="hrm_create_training_record")
async def create_training_record(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    training_id = hrm_store.create_training_record(payload)
    if not training_id:
        return JSONResponse({"status": "error", "error": "Failed to create training record"}, status_code=500)
    return JSONResponse({"status": "ok", "training_id": training_id})


@router.post("/performance/reviews", name="hrm_create_performance_review")
async def create_performance_review(request: Request, user=Depends(admin_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    payload.setdefault("reviewer_id", request.session.get("user_id", ""))
    review_id = hrm_store.create_performance_review(payload)
    if not review_id:
        return JSONResponse({"status": "error", "error": "Failed to create performance review"}, status_code=500)
    return JSONResponse({"status": "ok", "review_id": review_id})


@router.post("/ess/grievances", name="hrm_create_grievance")
async def create_grievance(request: Request, user=Depends(login_required)):
    payload = await request.json()
    payload["company_id"] = _company(request)
    payload.setdefault("employee_id", request.session.get("user_id", ""))
    grievance_id = hrm_store.create_grievance(payload)
    if not grievance_id:
        return JSONResponse({"status": "error", "error": "Failed to create grievance"}, status_code=500)
    return JSONResponse({"status": "ok", "grievance_id": grievance_id})
