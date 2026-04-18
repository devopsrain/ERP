from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

from models.multi_company import Company, User, UserRole, SubscriptionPlan, CompanyStatus

router = APIRouter(prefix="/company", tags=["multicompany"])

def _user_manager():
    from multicompany_demo_setup import get_user_manager
    return get_user_manager()

def _payroll_manager(company_id):
    from core.multi_company_payroll import MultiCompanyPayrollManager
    mgr = MultiCompanyPayrollManager()
    mgr.switch_company(company_id)
    return mgr


@router.get("/login", name="multicompany_company_login")
async def company_login_get(request: Request):
    return templates.TemplateResponse("multicompany/login.html", template_context(request))


@router.post("/login", name="multicompany_company_login_post")
async def company_login_post(request: Request):
    form     = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    try:
        um   = _user_manager()
        user = um.authenticate(username, password)
        if user:
            request.session.update({
                "logged_in":         True,
                "user_id":           user.user_id,
                "username":          user.username,
                "full_name":         getattr(user, "full_name", username),
                "privilege_level":   getattr(user, "privilege_level", "viewer"),
            })
            companies = um.get_user_companies(user.user_id)
            if len(companies) == 1:
                request.session["current_company_id"] = companies[0].company_id
                return RedirectResponse("/company/dashboard", status_code=303)
            return RedirectResponse("/company/select", status_code=303)
    except Exception as e:
        logger.warning("Multicompany login error: %s", e)
    flash(request, "Invalid credentials", "error")
    return templates.TemplateResponse("multicompany/login.html", template_context(request))


@router.get("/logout", name="multicompany_logout")
async def company_logout(request: Request):
    request.session.clear()
    flash(request, "Logged out.", "info")
    return RedirectResponse("/company/login", status_code=302)


@router.get("/register", name="multicompany_company_register")
async def company_register_get(request: Request):
    return templates.TemplateResponse("multicompany/register.html", template_context(request))


@router.post("/register", name="multicompany_company_register_post")
async def company_register_post(request: Request):
    form = await request.form()
    username  = form.get("username", "").strip()
    password  = form.get("password", "")
    full_name = form.get("full_name", "").strip()
    email     = form.get("email", "").strip()
    try:
        um = _user_manager()
        if um.username_exists(username):
            flash(request, "Username already taken", "error")
            return templates.TemplateResponse("multicompany/register.html", template_context(request))
        user = um.create_user(username=username, password=password,
                              full_name=full_name, email=email)
        flash(request, "Account created! Please login.", "success")
        return RedirectResponse("/company/login", status_code=303)
    except Exception as e:
        flash(request, f"Registration failed: {e}", "error")
        return templates.TemplateResponse("multicompany/register.html", template_context(request))


@router.get("/select", name="multicompany_company_select")
async def company_select(request: Request, user=Depends(login_required)):
    try:
        um        = _user_manager()
        companies = um.get_user_companies(request.session.get("user_id"))
    except Exception:
        companies = []
    ctx = template_context(request)
    ctx.update(companies=companies)
    return templates.TemplateResponse("multicompany/company_select.html", ctx)


@router.get("/switch/{company_id}", name="multicompany_company_switch")
async def company_switch(company_id: str, request: Request, user=Depends(login_required)):
    request.session["current_company_id"] = company_id
    flash(request, "Company switched", "success")
    return RedirectResponse("/company/dashboard", status_code=302)


@router.get("/dashboard", name="multicompany_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    company_id = request.session.get("current_company_id")
    ctx = template_context(request)
    company = None
    user_role = None
    try:
        um      = _user_manager()
        company = um.get_company(company_id)
        user_role = um.get_user_role(request.session.get("user_id"), company_id)
    except Exception:
        pass
    # Build a lightweight summary; the payroll manager is optional
    company_summary: dict = {}
    recent_payroll = None
    if company is not None:
        try:
            pm = _payroll_manager(company_id)
            employees = pm.get_all_employees() if hasattr(pm, "get_all_employees") else []
            payroll_runs = pm.get_payroll_history() if hasattr(pm, "get_payroll_history") else []
            if payroll_runs:
                recent_payroll = payroll_runs[-1]
            company_summary = {
                "total_employees": len(employees),
                "max_employees": getattr(getattr(company, "subscription_plan", None), "max_employees", 0) if company else 0,
                "user_count": len(um.get_company_users(company_id)) if hasattr(um, "get_company_users") else 0,
                "payroll_runs_count": len(payroll_runs),
                "recent_payroll": recent_payroll,
                "employees_by_category": {},
            }
        except Exception:
            company_summary = {
                "total_employees": 0, "max_employees": 0, "user_count": 0,
                "payroll_runs_count": 0, "recent_payroll": None, "employees_by_category": {},
            }
    ctx.update(
        company=company,
        user_role=user_role,
        company_summary=company_summary,
        recent_payroll=recent_payroll,
    )
    return templates.TemplateResponse("multicompany/dashboard.html", ctx)


@router.get("/create", name="multicompany_create_company_get")
async def create_company_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("multicompany/create.html", template_context(request))


@router.post("/create", name="multicompany_create_company")
async def create_company_post(request: Request, user=Depends(login_required)):
    form = await request.form()
    name = form.get("company_name", "").strip()
    plan = form.get("subscription_plan", "basic")
    if not name:
        flash(request, "Company name is required", "error")
        return templates.TemplateResponse("multicompany/create.html", template_context(request))
    try:
        um      = _user_manager()
        company = um.create_company(name=name, plan=plan,
                                    owner_id=request.session.get("user_id"))
        flash(request, f"Company '{name}' created!", "success")
        return RedirectResponse("/company/select", status_code=303)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return templates.TemplateResponse("multicompany/create.html", template_context(request))


@router.get("/settings", name="multicompany_company_settings")
async def company_settings(request: Request, user=Depends(login_required)):
    company_id = request.session.get("current_company_id")
    ctx = template_context(request)
    try:
        um      = _user_manager()
        company = um.get_company(company_id)
        users   = um.get_company_users(company_id)
        ctx.update(company=company, users=users)
    except Exception:
        ctx.update(company=None, users=[])
    return templates.TemplateResponse("multicompany/settings.html", ctx)
