from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from deps import login_required, admin_required
from hrm_data_store import hrm_store

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


@router.get("/analytics", name="hrm_analytics")
async def hrm_analytics(request: Request, user=Depends(admin_required)):
    return JSONResponse({
        "status": "ok",
        "company_id": _company(request),
        "data": hrm_store.get_hr_analytics(_company(request)),
    })


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
