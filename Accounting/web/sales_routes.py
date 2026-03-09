from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/", name="sales_landing")
@router.get("", name="sales_landing_noslash")
async def landing(request: Request):
    return templates.TemplateResponse("sales/index.html", template_context(request))


@router.post("/contact", name="sales_contact")
async def contact(request: Request):
    form = await request.form()
    name    = form.get("name", "").strip()
    email   = form.get("email", "").strip()
    company = form.get("company", "").strip()
    tier    = form.get("tier", "").strip()
    message = form.get("message", "").strip()
    if not name or not email:
        flash(request, "Please provide your name and email.", "danger")
        return RedirectResponse("/sales/#contact", status_code=303)
    try:
        from sales_data_store import sales_store
        sales_store.save_contact({
            "name": name, "email": email, "company": company,
            "tier": tier, "message": message,
            "ip_address": request.client.host if request.client else "",
        })
    except Exception as e:
        logger.error("Could not save sales contact: %s", e)
    flash(request, "Thank you! We will be in touch.", "success")
    return RedirectResponse("/sales/#contact", status_code=303)
