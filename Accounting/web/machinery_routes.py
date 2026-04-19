"""
Machinery & Equipment Management Routes

FastAPI routes for construction equipment management:
- Asset registry and tracking
- Site-to-site transfers
- Maintenance scheduling
- Operator shift logs
- HR/LMS integration
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import Optional

from deps import flash, template_context, login_required, admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/machinery", tags=["machinery"])


def get_machinery_store():
    """Get machinery data store instance."""
    from machinery_data_store import machinery_store
    return machinery_store


def get_current_company(request: Request) -> str:
    """Get current company ID from session."""
    return request.session.get('company_id', 'default')


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", name="machinery_dashboard")
async def machinery_dashboard(request: Request, user=Depends(login_required)):
    """Machinery & Equipment dashboard."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    stats = store.get_dashboard_stats(cid)
    recent_transfers = store.get_transfers(cid)[:5]
    upcoming_maintenance = store.get_upcoming_maintenance(cid, days_ahead=7)
    underutilized = store.get_underutilized_assets(cid)
    
    ctx = template_context(request)
    ctx.update(
        stats=stats,
        recent_transfers=recent_transfers,
        upcoming_maintenance=upcoming_maintenance,
        underutilized=underutilized,
        title="Machinery & Equipment",
    )
    return templates.TemplateResponse("machinery/dashboard.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# ASSET REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/assets", name="machinery_asset_list")
async def asset_list(request: Request, user=Depends(login_required),
                     status: Optional[str] = None,
                     category: Optional[str] = None, site_id: Optional[str] = None):
    """List all assets with optional filters."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, status=status, category=category, site_id=site_id)
    sites = store.get_sites(cid)
    
    from models.machinery import AssetCategory, AssetStatus
    
    ctx = template_context(request)
    ctx.update(
        assets=assets,
        sites=sites,
        categories=[e.value for e in AssetCategory],
        statuses=[e.value for e in AssetStatus],
        filter_status=status,
        filter_category=category,
        filter_site=site_id,
        title="Asset Registry",
    )
    return templates.TemplateResponse("machinery/asset_list.html", ctx)


@router.get("/assets/new", name="machinery_asset_new")
async def asset_new(request: Request, user=Depends(admin_required)):
    """New asset form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    sites = store.get_sites(cid)
    
    from models.machinery import (AssetCategory, AssetType, AssetStatus,
                                   OwnershipType, FuelType, ASSET_TYPE_INFO)
    
    ctx = template_context(request)
    ctx.update(
        asset=None,
        sites=sites,
        categories=[e.value for e in AssetCategory],
        asset_types=[e.value for e in AssetType],
        statuses=[e.value for e in AssetStatus],
        ownership_types=[e.value for e in OwnershipType],
        fuel_types=[e.value for e in FuelType],
        asset_type_info=ASSET_TYPE_INFO,
        is_edit=False,
        title="Add New Asset",
    )
    return templates.TemplateResponse("machinery/asset_form.html", ctx)


@router.post("/assets/new", name="machinery_asset_create")
async def asset_create(request: Request, user=Depends(admin_required)):
    """Create a new asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    data = {
        'company_id': cid,
        'name': form.get('name', ''),
        'description': form.get('description', ''),
        'serial_number': form.get('serial_number', ''),
        'vin_chassis_number': form.get('vin_chassis_number', ''),
        'category': form.get('category', 'other'),
        'asset_type': form.get('asset_type', 'other'),
        'product_class': form.get('product_class', ''),
        'manufacturer': form.get('manufacturer', ''),
        'model': form.get('model', ''),
        'year_manufactured': int(form.get('year_manufactured', 0) or 0),
        'status': form.get('status', 'available'),
        'ownership_type': form.get('ownership_type', 'owned'),
        'fuel_type': form.get('fuel_type', 'diesel'),
        'current_site_id': form.get('current_site_id', ''),
        'current_site_name': form.get('current_site_name', ''),
        'home_yard_id': form.get('home_yard_id', ''),
        'home_yard_name': form.get('home_yard_name', ''),
        'service_interval_hours': float(form.get('service_interval_hours', 250) or 250),
        'notes': form.get('notes', ''),
        'created_by': request.session.get('username', ''),
    }
    
    specs = {}
    for key in ['engine_make', 'engine_model', 'engine_power_hp', 'bucket_capacity_m3',
                'operating_weight_kg', 'max_lift_capacity_kg', 'max_reach_m']:
        val = form.get(key, '')
        if val:
            specs[key] = val
    data['technical_specs'] = specs
    
    financial = {}
    for key in ['purchase_price', 'purchase_date', 'internal_rental_rate_per_hour',
                'depreciation_method', 'salvage_value', 'useful_life_years']:
        val = form.get(key, '')
        if val:
            financial[key] = val if key in ['purchase_date', 'depreciation_method', 'useful_life_years'] else float(val)
    data['financial'] = financial
    
    licenses = form.get('required_licenses', '')
    data['required_licenses'] = [l.strip() for l in licenses.split(',') if l.strip()]
    
    training = form.get('required_training_courses', '')
    data['required_training_courses'] = [t.strip() for t in training.split(',') if t.strip()]
    
    asset_id = store.create_asset(data)
    
    if asset_id:
        flash(request, "Asset created successfully", "success")
        return RedirectResponse(url=f"/machinery/assets/{asset_id}", status_code=303)
    
    flash(request, "Failed to create asset", "error")
    return RedirectResponse(url="/machinery/assets/new", status_code=303)


@router.get("/assets/{asset_id}", name="machinery_asset_detail")
async def asset_detail(request: Request, asset_id: str, user=Depends(login_required)):
    """View asset details."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    shift_logs = store.get_shift_logs(cid, asset_id=asset_id)[:10]
    fuel_logs = store.get_fuel_logs(cid, asset_id=asset_id)[:10]
    maintenance = store.get_maintenance_orders(cid, asset_id=asset_id)[:5]
    transfers = store.get_transfers(cid, asset_id=asset_id)[:5]
    active_shift = store.get_active_shift(asset_id)
    
    ctx = template_context(request)
    ctx.update(
        asset=asset,
        shift_logs=shift_logs,
        fuel_logs=fuel_logs,
        maintenance=maintenance,
        transfers=transfers,
        active_shift=active_shift,
        title=f"Asset: {asset['name']}",
    )
    return templates.TemplateResponse("machinery/asset_detail.html", ctx)


@router.get("/assets/{asset_id}/edit", name="machinery_asset_edit")
async def asset_edit(request: Request, asset_id: str, user=Depends(admin_required)):
    """Edit asset form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    sites = store.get_sites(cid)
    
    from models.machinery import (AssetCategory, AssetType, AssetStatus,
                                   OwnershipType, FuelType, ASSET_TYPE_INFO)
    
    ctx = template_context(request)
    ctx.update(
        asset=asset,
        sites=sites,
        categories=[e.value for e in AssetCategory],
        asset_types=[e.value for e in AssetType],
        statuses=[e.value for e in AssetStatus],
        ownership_types=[e.value for e in OwnershipType],
        fuel_types=[e.value for e in FuelType],
        asset_type_info=ASSET_TYPE_INFO,
        is_edit=True,
        title=f"Edit: {asset['name']}",
    )
    return templates.TemplateResponse("machinery/asset_form.html", ctx)


@router.post("/assets/{asset_id}/edit", name="machinery_asset_update")
async def asset_update(request: Request, asset_id: str, user=Depends(admin_required)):
    """Update an existing asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    data = {
        'name': form.get('name', ''),
        'description': form.get('description', ''),
        'serial_number': form.get('serial_number', ''),
        'vin_chassis_number': form.get('vin_chassis_number', ''),
        'category': form.get('category', ''),
        'asset_type': form.get('asset_type', ''),
        'product_class': form.get('product_class', ''),
        'manufacturer': form.get('manufacturer', ''),
        'model': form.get('model', ''),
        'year_manufactured': int(form.get('year_manufactured', 0) or 0),
        'status': form.get('status', ''),
        'ownership_type': form.get('ownership_type', ''),
        'fuel_type': form.get('fuel_type', ''),
        'current_site_id': form.get('current_site_id', ''),
        'current_site_name': form.get('current_site_name', ''),
        'service_interval_hours': float(form.get('service_interval_hours', 250) or 250),
        'notes': form.get('notes', ''),
    }
    
    specs = {}
    for key in ['engine_make', 'engine_model', 'engine_power_hp', 'bucket_capacity_m3',
                'operating_weight_kg', 'max_lift_capacity_kg', 'max_reach_m']:
        val = form.get(key, '')
        if val:
            specs[key] = val
    data['technical_specs'] = specs
    
    licenses = form.get('required_licenses', '')
    data['required_licenses'] = [l.strip() for l in licenses.split(',') if l.strip()]
    
    training = form.get('required_training_courses', '')
    data['required_training_courses'] = [t.strip() for t in training.split(',') if t.strip()]
    
    if store.update_asset(asset_id, data):
        flash(request, "Asset updated", "success")
    else:
        flash(request, "Update failed", "error")
    
    return RedirectResponse(url=f"/machinery/assets/{asset_id}", status_code=303)


@router.post("/assets/{asset_id}/delete", name="machinery_asset_delete")
async def asset_delete(request: Request, asset_id: str, user=Depends(admin_required)):
    """Soft delete an asset."""
    store = get_machinery_store()
    store.delete_asset(asset_id)
    flash(request, "Asset decommissioned", "success")
    return RedirectResponse(url="/machinery/assets", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# SITES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sites", name="machinery_site_list")
async def site_list(request: Request, user=Depends(login_required)):
    """List all sites, yards, workshops."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    sites = store.get_sites(cid)
    
    for site in sites:
        assets = store.get_assets_at_site(site['site_id'], cid)
        site['asset_count'] = len(assets)
    
    ctx = template_context(request)
    ctx.update(sites=sites, title="Sites & Yards")
    return templates.TemplateResponse("machinery/site_list.html", ctx)


@router.get("/sites/new", name="machinery_site_new")
async def site_new(request: Request, user=Depends(admin_required)):
    """New site form."""
    ctx = template_context(request)
    ctx.update(site=None, is_edit=False, title="Add New Site")
    return templates.TemplateResponse("machinery/site_form.html", ctx)


@router.post("/sites/new", name="machinery_site_create")
async def site_create(request: Request, user=Depends(admin_required)):
    """Create a new site."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    data = {
        'company_id': cid,
        'name': form.get('name', ''),
        'site_type': form.get('site_type', 'project'),
        'address': form.get('address', ''),
        'city': form.get('city', ''),
        'region': form.get('region', ''),
        'country': form.get('country', 'Ethiopia'),
        'latitude': float(form.get('latitude', 0) or 0),
        'longitude': float(form.get('longitude', 0) or 0),
        'geofence_radius_m': float(form.get('geofence_radius_m', 500) or 500),
        'project_id': form.get('project_id', ''),
        'project_name': form.get('project_name', ''),
        'site_manager_id': form.get('site_manager_id', ''),
        'site_manager_name': form.get('site_manager_name', ''),
        'contact_phone': form.get('contact_phone', ''),
    }
    
    site_id = store.create_site(data)
    
    if site_id:
        flash(request, "Site created", "success")
        return RedirectResponse(url="/machinery/sites", status_code=303)
    
    flash(request, "Failed to create site", "error")
    return RedirectResponse(url="/machinery/sites/new", status_code=303)


@router.get("/sites/{site_id}", name="machinery_site_detail")
async def site_detail(request: Request, site_id: str, user=Depends(login_required)):
    """View site details."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    site = store.get_site(site_id, cid)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    
    assets = store.get_assets_at_site(site_id, cid)
    
    ctx = template_context(request)
    ctx.update(site=site, assets=assets, title=f"Site: {site['name']}")
    return templates.TemplateResponse("machinery/site_detail.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# TRANSFERS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/transfers", name="machinery_transfer_list")
async def transfer_list(request: Request, user=Depends(login_required)):
    """List all transfer orders."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    status = request.query_params.get('status', '')
    transfers = store.get_transfers(cid, status=status if status else None)
    
    ctx = template_context(request)
    ctx.update(transfers=transfers, filter_status=status, title="Transfer Orders")
    return templates.TemplateResponse("machinery/transfer_list.html", ctx)


@router.get("/transfers/new", name="machinery_transfer_new")
async def transfer_new(request: Request, user=Depends(admin_required)):
    """New transfer order form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, status='available')
    sites = store.get_sites(cid)
    
    from models.machinery import TransferPriority
    
    ctx = template_context(request)
    ctx.update(
        transfer=None,
        assets=assets,
        sites=sites,
        priorities=[e.value for e in TransferPriority],
        is_edit=False,
        title="New Transfer Order",
    )
    return templates.TemplateResponse("machinery/transfer_form.html", ctx)


@router.post("/transfers/new", name="machinery_transfer_create")
async def transfer_create(request: Request, user=Depends(admin_required)):
    """Create a new transfer order."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    data = {
        'company_id': cid,
        'asset_id': form.get('asset_id', ''),
        'from_site_id': form.get('from_site_id', ''),
        'from_site_name': form.get('from_site_name', ''),
        'to_site_id': form.get('to_site_id', ''),
        'to_site_name': form.get('to_site_name', ''),
        'scheduled_date': form.get('scheduled_date', ''),
        'priority': form.get('priority', 'normal'),
        'reason': form.get('reason', ''),
        'special_requirements': form.get('special_requirements', ''),
        'estimated_transport_cost': float(form.get('estimated_transport_cost', 0) or 0),
        'requested_by': request.session.get('username', ''),
    }
    
    transfer_id = store.create_transfer(data)
    
    if transfer_id:
        flash(request, "Transfer order created", "success")
        return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)
    
    flash(request, "Failed to create transfer", "error")
    return RedirectResponse(url="/machinery/transfers/new", status_code=303)


@router.get("/transfers/{transfer_id}", name="machinery_transfer_detail")
async def transfer_detail(request: Request, transfer_id: str, user=Depends(login_required)):
    """View transfer order details."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    transfer = store.get_transfer(transfer_id, cid)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    
    ctx = template_context(request)
    ctx.update(transfer=transfer, title=f"Transfer: {transfer_id[:8]}")
    return templates.TemplateResponse("machinery/transfer_detail.html", ctx)


@router.post("/transfers/{transfer_id}/approve", name="machinery_transfer_approve")
async def transfer_approve(request: Request, transfer_id: str, user=Depends(admin_required)):
    """Approve a transfer order."""
    store = get_machinery_store()
    username = request.session.get('username', '')
    
    if store.update_transfer_status(transfer_id, 'approved', username):
        flash(request, "Transfer approved", "success")
    else:
        flash(request, "Approval failed", "error")
    
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


@router.post("/transfers/{transfer_id}/reject", name="machinery_transfer_reject")
async def transfer_reject(request: Request, transfer_id: str, user=Depends(admin_required)):
    """Reject a transfer order."""
    store = get_machinery_store()
    username = request.session.get('username', '')
    
    if store.update_transfer_status(transfer_id, 'rejected', username):
        flash(request, "Transfer rejected", "success")
    else:
        flash(request, "Rejection failed", "error")
    
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


@router.post("/transfers/{transfer_id}/start", name="machinery_transfer_start")
async def transfer_start(request: Request, transfer_id: str, user=Depends(admin_required)):
    """Start a transfer (in transit)."""
    store = get_machinery_store()
    username = request.session.get('username', '')
    
    if store.update_transfer_status(transfer_id, 'in_transit', username):
        flash(request, "Transfer started", "success")
    else:
        flash(request, "Could not start transfer", "error")
    
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


@router.post("/transfers/{transfer_id}/complete", name="machinery_transfer_complete")
async def transfer_complete(request: Request, transfer_id: str, user=Depends(login_required)):
    """Complete a transfer."""
    store = get_machinery_store()
    username = request.session.get('username', '')
    
    if store.complete_transfer(transfer_id, username):
        flash(request, "Transfer completed", "success")
    else:
        flash(request, "Could not complete transfer", "error")
    
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MAINTENANCE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/maintenance", name="machinery_maintenance_list")
async def maintenance_list(request: Request, user=Depends(login_required)):
    """List maintenance work orders."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    status = request.query_params.get('status', '')
    mtype = request.query_params.get('type', '')
    
    orders = store.get_maintenance_orders(cid, status=status if status else None,
                                           maintenance_type=mtype if mtype else None)
    
    ctx = template_context(request)
    ctx.update(orders=orders, filter_status=status, filter_type=mtype, title="Maintenance")
    return templates.TemplateResponse("machinery/maintenance_list.html", ctx)


@router.get("/maintenance/new", name="machinery_maintenance_new")
async def maintenance_new(request: Request, user=Depends(admin_required)):
    """New maintenance work order form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid)
    
    from models.machinery import MaintenanceType, WorkOrderPriority
    
    ctx = template_context(request)
    ctx.update(
        work_order=None,
        assets=assets,
        maintenance_types=[e.value for e in MaintenanceType],
        priorities=[e.value for e in WorkOrderPriority],
        is_edit=False,
        title="New Maintenance Order",
    )
    return templates.TemplateResponse("machinery/maintenance_form.html", ctx)


@router.post("/maintenance/new", name="machinery_maintenance_create")
async def maintenance_create(request: Request, user=Depends(admin_required)):
    """Create a new maintenance work order."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    data = {
        'company_id': cid,
        'asset_id': form.get('asset_id', ''),
        'maintenance_type': form.get('maintenance_type', 'corrective'),
        'priority': form.get('priority', 'normal'),
        'description': form.get('description', ''),
        'scheduled_date': form.get('scheduled_date', ''),
        'estimated_hours': float(form.get('estimated_hours', 0) or 0),
        'estimated_cost': float(form.get('estimated_cost', 0) or 0),
        'assigned_technician_id': form.get('assigned_technician_id', ''),
        'assigned_technician_name': form.get('assigned_technician_name', ''),
        'parts_required': form.get('parts_required', ''),
        'created_by': request.session.get('username', ''),
    }
    
    work_order_id = store.create_maintenance_order(data)
    
    if work_order_id:
        flash(request, "Maintenance order created", "success")
        return RedirectResponse(url=f"/machinery/maintenance/{work_order_id}", status_code=303)
    
    flash(request, "Failed to create maintenance order", "error")
    return RedirectResponse(url="/machinery/maintenance/new", status_code=303)


@router.get("/maintenance/{work_order_id}", name="machinery_maintenance_detail")
async def maintenance_detail(request: Request, work_order_id: str, user=Depends(login_required)):
    """View maintenance work order details."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    work_order = store.get_maintenance_order(work_order_id, cid)
    if not work_order:
        raise HTTPException(status_code=404, detail="Work order not found")
    
    ctx = template_context(request)
    ctx.update(work_order=work_order, title=f"Work Order: {work_order_id[:8]}")
    return templates.TemplateResponse("machinery/maintenance_detail.html", ctx)


@router.post("/maintenance/{work_order_id}/start", name="machinery_maintenance_start")
async def maintenance_start(request: Request, work_order_id: str, user=Depends(login_required)):
    """Start maintenance work."""
    store = get_machinery_store()
    
    if store.start_maintenance(work_order_id):
        flash(request, "Maintenance started", "success")
    else:
        flash(request, "Could not start", "error")
    
    return RedirectResponse(url=f"/machinery/maintenance/{work_order_id}", status_code=303)


@router.post("/maintenance/{work_order_id}/complete", name="machinery_maintenance_complete")
async def maintenance_complete(request: Request, work_order_id: str, user=Depends(login_required)):
    """Complete maintenance work."""
    store = get_machinery_store()
    form = await request.form()
    
    completion_data = {
        'actual_hours': float(form.get('actual_hours', 0) or 0),
        'actual_cost': float(form.get('actual_cost', 0) or 0),
        'parts_used': form.get('parts_used', ''),
        'notes': form.get('notes', ''),
        'completed_by': request.session.get('username', ''),
    }
    
    if store.complete_maintenance(work_order_id, completion_data):
        flash(request, "Maintenance completed", "success")
    else:
        flash(request, "Could not complete", "error")
    
    return RedirectResponse(url=f"/machinery/maintenance/{work_order_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# SHIFT LOGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/shift-logs", name="machinery_shift_log_list")
async def shift_log_list(request: Request, user=Depends(login_required)):
    """List operator shift logs."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset_id = request.query_params.get('asset_id', '')
    operator_id = request.query_params.get('operator_id', '')
    
    logs = store.get_shift_logs(cid, asset_id=asset_id if asset_id else None,
                                 operator_id=operator_id if operator_id else None)
    
    ctx = template_context(request)
    ctx.update(logs=logs, filter_asset=asset_id, filter_operator=operator_id, title="Shift Logs")
    return templates.TemplateResponse("machinery/shift_log_list.html", ctx)


@router.get("/shift-logs/start", name="machinery_shift_start_form")
async def shift_start_form(request: Request, user=Depends(login_required)):
    """Start shift form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, status='available')
    
    ctx = template_context(request)
    ctx.update(assets=assets, title="Start Shift")
    return templates.TemplateResponse("machinery/shift_start.html", ctx)


@router.post("/shift-logs/start", name="machinery_shift_start")
async def shift_start(request: Request, user=Depends(login_required)):
    """Start an operator shift."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    username = request.session.get('username', '')
    user_id = request.session.get('user_id', '')
    
    data = {
        'company_id': cid,
        'asset_id': form.get('asset_id', ''),
        'operator_id': user_id,
        'operator_name': username,
        'shift_type': form.get('shift_type', 'day'),
        'site_id': form.get('site_id', ''),
        'site_name': form.get('site_name', ''),
        'start_hour_meter': float(form.get('start_hour_meter', 0) or 0),
        'start_odometer': float(form.get('start_odometer', 0) or 0),
        'pre_shift_check': form.get('pre_shift_check') == 'on',
        'pre_shift_notes': form.get('pre_shift_notes', ''),
    }
    
    log_id = store.start_shift(data)
    
    if log_id:
        flash(request, "Shift started", "success")
        return RedirectResponse(url=f"/machinery/shift-logs/{log_id}", status_code=303)
    
    flash(request, "Could not start shift", "error")
    return RedirectResponse(url="/machinery/shift-logs/start", status_code=303)


@router.get("/shift-logs/{log_id}", name="machinery_shift_log_detail")
async def shift_log_detail(request: Request, log_id: str, user=Depends(login_required)):
    """View shift log details."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    log = store.get_shift_log(log_id, cid)
    if not log:
        raise HTTPException(status_code=404, detail="Shift log not found")
    
    ctx = template_context(request)
    ctx.update(log=log, title=f"Shift Log: {log_id[:8]}")
    return templates.TemplateResponse("machinery/shift_log_detail.html", ctx)


@router.post("/shift-logs/{log_id}/end", name="machinery_shift_end")
async def shift_end(request: Request, log_id: str, user=Depends(login_required)):
    """End an operator shift."""
    store = get_machinery_store()
    form = await request.form()
    
    end_data = {
        'end_hour_meter': float(form.get('end_hour_meter', 0) or 0),
        'end_odometer': float(form.get('end_odometer', 0) or 0),
        'work_performed': form.get('work_performed', ''),
        'issues_reported': form.get('issues_reported', ''),
        'post_shift_notes': form.get('post_shift_notes', ''),
    }
    
    if store.end_shift(log_id, end_data):
        flash(request, "Shift ended", "success")
    else:
        flash(request, "Could not end shift", "error")
    
    return RedirectResponse(url=f"/machinery/shift-logs/{log_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# FUEL LOGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/fuel-logs", name="machinery_fuel_log_list")
async def fuel_log_list(request: Request, user=Depends(login_required)):
    """List fuel logs."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset_id = request.query_params.get('asset_id', '')
    logs = store.get_fuel_logs(cid, asset_id=asset_id if asset_id else None)
    
    ctx = template_context(request)
    ctx.update(logs=logs, filter_asset=asset_id, title="Fuel Logs")
    return templates.TemplateResponse("machinery/fuel_log_list.html", ctx)


@router.get("/fuel-logs/new", name="machinery_fuel_log_new")
async def fuel_log_new(request: Request, user=Depends(login_required)):
    """New fuel log form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid)
    
    from models.machinery import FuelType
    
    ctx = template_context(request)
    ctx.update(
        assets=assets,
        fuel_types=[e.value for e in FuelType],
        title="Log Fuel",
    )
    return templates.TemplateResponse("machinery/fuel_log_form.html", ctx)


@router.post("/fuel-logs/new", name="machinery_fuel_log_create")
async def fuel_log_create(request: Request, user=Depends(login_required)):
    """Create a new fuel log."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    data = {
        'company_id': cid,
        'asset_id': form.get('asset_id', ''),
        'fuel_type': form.get('fuel_type', 'diesel'),
        'quantity_liters': float(form.get('quantity_liters', 0) or 0),
        'cost_per_liter': float(form.get('cost_per_liter', 0) or 0),
        'total_cost': float(form.get('total_cost', 0) or 0),
        'hour_meter_reading': float(form.get('hour_meter_reading', 0) or 0),
        'odometer_reading': float(form.get('odometer_reading', 0) or 0),
        'fuel_station': form.get('fuel_station', ''),
        'receipt_number': form.get('receipt_number', ''),
        'logged_by': request.session.get('username', ''),
    }
    
    log_id = store.create_fuel_log(data)
    
    if log_id:
        flash(request, "Fuel log created", "success")
        return RedirectResponse(url="/machinery/fuel-logs", status_code=303)
    
    flash(request, "Could not create fuel log", "error")
    return RedirectResponse(url="/machinery/fuel-logs/new", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# OPERATOR ASSIGNMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/assets/{asset_id}/assign-operator", name="machinery_assign_operator_form")
async def assign_operator_form(request: Request, asset_id: str, user=Depends(admin_required)):
    """Assign operator form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    qualified_operators = store.get_qualified_operators(asset_id, cid)
    
    ctx = template_context(request)
    ctx.update(
        asset=asset,
        operators=qualified_operators,
        title=f"Assign Operator: {asset['name']}",
    )
    return templates.TemplateResponse("machinery/assign_operator.html", ctx)


@router.post("/assets/{asset_id}/assign-operator", name="machinery_assign_operator")
async def assign_operator(request: Request, asset_id: str, user=Depends(admin_required)):
    """Assign an operator to an asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    operator_id = form.get('operator_id', '')
    
    if store.assign_operator(asset_id, operator_id, cid):
        flash(request, "Operator assigned", "success")
    else:
        flash(request, "Could not assign operator", "error")
    
    return RedirectResponse(url=f"/machinery/assets/{asset_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/reports/utilization", name="machinery_report_utilization")
async def report_utilization(request: Request, user=Depends(login_required)):
    """Asset utilization report."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    start_date = request.query_params.get('start_date', '')
    end_date = request.query_params.get('end_date', '')
    
    report_data = store.get_utilization_report(cid, start_date, end_date)
    
    ctx = template_context(request)
    ctx.update(
        report=report_data,
        start_date=start_date,
        end_date=end_date,
        title="Utilization Report",
    )
    return templates.TemplateResponse("machinery/report_utilization.html", ctx)


@router.get("/reports/project-cost", name="machinery_report_project_cost")
async def report_project_cost(request: Request, user=Depends(login_required)):
    """Project cost allocation report."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    site_id = request.query_params.get('site_id', '')
    start_date = request.query_params.get('start_date', '')
    end_date = request.query_params.get('end_date', '')
    
    sites = store.get_sites(cid)
    report_data = store.get_project_cost_report(cid, site_id, start_date, end_date) if site_id else {}
    
    ctx = template_context(request)
    ctx.update(
        report=report_data,
        sites=sites,
        filter_site=site_id,
        start_date=start_date,
        end_date=end_date,
        title="Project Cost Report",
    )
    return templates.TemplateResponse("machinery/report_project_cost.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/assets", name="machinery_api_assets")
async def api_get_assets(request: Request, user=Depends(login_required),
                         status: Optional[str] = None, category: Optional[str] = None):
    """API: Get assets."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, status=status, category=category)
    return JSONResponse(content={"assets": assets})


@router.get("/api/assets/{asset_id}", name="machinery_api_asset")
async def api_get_asset(request: Request, asset_id: str, user=Depends(login_required)):
    """API: Get single asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    return JSONResponse(content={"asset": asset})


@router.get("/api/check-certification", name="machinery_api_check_cert")
async def api_check_certification(request: Request, operator_id: str, asset_id: str,
                                   user=Depends(login_required)):
    """API: Check operator certification for an asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    result = store.check_operator_certification(operator_id, asset_id, cid)
    return JSONResponse(content=result)


@router.get("/api/dashboard-stats", name="machinery_api_stats")
async def api_dashboard_stats(request: Request, user=Depends(login_required)):
    """API: Get dashboard statistics."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    stats = store.get_dashboard_stats(cid)
    return JSONResponse(content=stats)
