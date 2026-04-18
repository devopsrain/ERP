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
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import Optional

from auth import login_required, manager_or_admin_required, admin_required

router = APIRouter()


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

@router.get("/machinery", response_class=HTMLResponse)
@login_required
async def machinery_dashboard(request: Request):
    """Machinery & Equipment dashboard."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    stats = store.get_dashboard_stats(cid)
    recent_transfers = store.get_transfers(cid)[:5]
    upcoming_maintenance = store.get_upcoming_maintenance(cid, days_ahead=7)
    
    # Get assets needing attention
    underutilized = store.get_underutilized_assets(cid)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/dashboard.html",
        {
            "request": request,
            "stats": stats,
            "recent_transfers": recent_transfers,
            "upcoming_maintenance": upcoming_maintenance,
            "underutilized": underutilized,
            "title": "Machinery & Equipment",
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# ASSET REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/assets", response_class=HTMLResponse)
@login_required
async def asset_list(request: Request, status: Optional[str] = None,
                     category: Optional[str] = None, site_id: Optional[str] = None):
    """List all assets with optional filters."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, status=status, category=category, site_id=site_id)
    sites = store.get_sites(cid)
    
    # Import enums for dropdown options
    from models.machinery import AssetCategory, AssetStatus
    
    return request.app.state.templates.TemplateResponse(
        "machinery/asset_list.html",
        {
            "request": request,
            "assets": assets,
            "sites": sites,
            "categories": [e.value for e in AssetCategory],
            "statuses": [e.value for e in AssetStatus],
            "filter_status": status,
            "filter_category": category,
            "filter_site": site_id,
            "title": "Asset Registry",
        }
    )


@router.get("/machinery/assets/new", response_class=HTMLResponse)
@manager_or_admin_required
async def asset_new(request: Request):
    """New asset form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    sites = store.get_sites(cid)
    
    from models.machinery import (AssetCategory, AssetType, AssetStatus,
                                   OwnershipType, FuelType, ASSET_TYPE_INFO)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/asset_form.html",
        {
            "request": request,
            "asset": None,
            "sites": sites,
            "categories": [e.value for e in AssetCategory],
            "asset_types": [e.value for e in AssetType],
            "statuses": [e.value for e in AssetStatus],
            "ownership_types": [e.value for e in OwnershipType],
            "fuel_types": [e.value for e in FuelType],
            "asset_type_info": ASSET_TYPE_INFO,
            "is_edit": False,
            "title": "Add New Asset",
        }
    )


@router.post("/machinery/assets/new")
@manager_or_admin_required
async def asset_create(request: Request):
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
    
    # Technical specs
    specs = {}
    for key in ['engine_make', 'engine_model', 'engine_power_hp', 'bucket_capacity_m3',
                'operating_weight_kg', 'max_lift_capacity_kg', 'max_reach_m']:
        val = form.get(key, '')
        if val:
            specs[key] = val
    data['technical_specs'] = specs
    
    # Financial info
    financial = {}
    for key in ['purchase_price', 'purchase_date', 'internal_rental_rate_per_hour',
                'depreciation_method', 'salvage_value', 'useful_life_years']:
        val = form.get(key, '')
        if val:
            financial[key] = float(val) if key in ['purchase_price', 'internal_rental_rate_per_hour', 'salvage_value'] else val
    data['financial'] = financial
    
    # Required licenses and training
    licenses = form.get('required_licenses', '')
    data['required_licenses'] = [l.strip() for l in licenses.split(',') if l.strip()]
    
    training = form.get('required_training_courses', '')
    data['required_training_courses'] = [t.strip() for t in training.split(',') if t.strip()]
    
    asset_id = store.create_asset(data)
    
    if asset_id:
        request.session['flash'] = {'type': 'success', 'message': 'Asset created successfully'}
        return RedirectResponse(url=f"/machinery/assets/{asset_id}", status_code=303)
    
    request.session['flash'] = {'type': 'error', 'message': 'Failed to create asset'}
    return RedirectResponse(url="/machinery/assets/new", status_code=303)


@router.get("/machinery/assets/{asset_id}", response_class=HTMLResponse)
@login_required
async def asset_detail(request: Request, asset_id: str):
    """View asset details."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    # Get related data
    shift_logs = store.get_shift_logs(cid, asset_id=asset_id)[:10]
    fuel_logs = store.get_fuel_logs(cid, asset_id=asset_id)[:10]
    maintenance = store.get_maintenance_orders(cid, asset_id=asset_id)[:5]
    transfers = store.get_transfers(cid, asset_id=asset_id)[:5]
    active_shift = store.get_active_shift(asset_id)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/asset_detail.html",
        {
            "request": request,
            "asset": asset,
            "shift_logs": shift_logs,
            "fuel_logs": fuel_logs,
            "maintenance": maintenance,
            "transfers": transfers,
            "active_shift": active_shift,
            "title": f"Asset: {asset['name']}",
        }
    )


@router.get("/machinery/assets/{asset_id}/edit", response_class=HTMLResponse)
@manager_or_admin_required
async def asset_edit(request: Request, asset_id: str):
    """Edit asset form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    sites = store.get_sites(cid)
    
    from models.machinery import (AssetCategory, AssetType, AssetStatus,
                                   OwnershipType, FuelType, ASSET_TYPE_INFO)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/asset_form.html",
        {
            "request": request,
            "asset": asset,
            "sites": sites,
            "categories": [e.value for e in AssetCategory],
            "asset_types": [e.value for e in AssetType],
            "statuses": [e.value for e in AssetStatus],
            "ownership_types": [e.value for e in OwnershipType],
            "fuel_types": [e.value for e in FuelType],
            "asset_type_info": ASSET_TYPE_INFO,
            "is_edit": True,
            "title": f"Edit: {asset['name']}",
        }
    )


@router.post("/machinery/assets/{asset_id}/edit")
@manager_or_admin_required
async def asset_update(request: Request, asset_id: str):
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
    
    # Technical specs
    specs = {}
    for key in ['engine_make', 'engine_model', 'engine_power_hp', 'bucket_capacity_m3',
                'operating_weight_kg', 'max_lift_capacity_kg', 'max_reach_m']:
        val = form.get(key, '')
        if val:
            specs[key] = val
    data['technical_specs'] = specs
    
    # Required licenses and training
    licenses = form.get('required_licenses', '')
    data['required_licenses'] = [l.strip() for l in licenses.split(',') if l.strip()]
    
    training = form.get('required_training_courses', '')
    data['required_training_courses'] = [t.strip() for t in training.split(',') if t.strip()]
    
    if store.update_asset(asset_id, data):
        request.session['flash'] = {'type': 'success', 'message': 'Asset updated'}
    else:
        request.session['flash'] = {'type': 'error', 'message': 'Update failed'}
    
    return RedirectResponse(url=f"/machinery/assets/{asset_id}", status_code=303)


@router.post("/machinery/assets/{asset_id}/delete")
@admin_required
async def asset_delete(request: Request, asset_id: str):
    """Soft delete an asset."""
    store = get_machinery_store()
    store.delete_asset(asset_id)
    request.session['flash'] = {'type': 'success', 'message': 'Asset decommissioned'}
    return RedirectResponse(url="/machinery/assets", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# SITES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/sites", response_class=HTMLResponse)
@login_required
async def site_list(request: Request):
    """List all sites, yards, workshops."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    sites = store.get_sites(cid)
    
    # Get asset counts per site
    for site in sites:
        assets = store.get_assets_at_site(site['site_id'], cid)
        site['asset_count'] = len(assets)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/site_list.html",
        {
            "request": request,
            "sites": sites,
            "title": "Sites & Yards",
        }
    )


@router.get("/machinery/sites/new", response_class=HTMLResponse)
@manager_or_admin_required
async def site_new(request: Request):
    """New site form."""
    return request.app.state.templates.TemplateResponse(
        "machinery/site_form.html",
        {
            "request": request,
            "site": None,
            "is_edit": False,
            "title": "Add New Site",
        }
    )


@router.post("/machinery/sites/new")
@manager_or_admin_required
async def site_create(request: Request):
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
        request.session['flash'] = {'type': 'success', 'message': 'Site created'}
        return RedirectResponse(url="/machinery/sites", status_code=303)
    
    request.session['flash'] = {'type': 'error', 'message': 'Failed to create site'}
    return RedirectResponse(url="/machinery/sites/new", status_code=303)


@router.get("/machinery/sites/{site_id}", response_class=HTMLResponse)
@login_required
async def site_detail(request: Request, site_id: str):
    """View site details with assets."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    site = store.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    
    assets = store.get_assets_at_site(site_id, cid)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/site_detail.html",
        {
            "request": request,
            "site": site,
            "assets": assets,
            "title": f"Site: {site['name']}",
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# TRANSFERS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/transfers", response_class=HTMLResponse)
@login_required
async def transfer_list(request: Request, status: Optional[str] = None):
    """List all transfer orders."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    transfers = store.get_transfers(cid, status=status)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/transfer_list.html",
        {
            "request": request,
            "transfers": transfers,
            "filter_status": status,
            "title": "Transfer Orders",
        }
    )


@router.get("/machinery/transfers/new", response_class=HTMLResponse)
@manager_or_admin_required
async def transfer_new(request: Request, asset_id: Optional[str] = None):
    """New transfer request form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    # Get assets that can be transferred (available or in-use)
    assets = store.get_assets(cid, status=None, is_active=True)
    transferable = [a for a in assets if a['status'] in ('available', 'in_use')]
    
    sites = store.get_sites(cid)
    
    # Pre-select asset if provided
    selected_asset = None
    if asset_id:
        selected_asset = store.get_asset(asset_id, cid)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/transfer_form.html",
        {
            "request": request,
            "assets": transferable,
            "sites": sites,
            "selected_asset": selected_asset,
            "title": "Request Transfer",
        }
    )


@router.post("/machinery/transfers/new")
@manager_or_admin_required
async def transfer_create(request: Request):
    """Create a transfer request."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    asset_id = form.get('asset_id', '')
    asset = store.get_asset(asset_id, cid)
    
    to_site = store.get_site(form.get('to_site_id', ''))
    from_site = store.get_site(form.get('from_site_id', '') or (asset['current_site_id'] if asset else ''))
    
    data = {
        'company_id': cid,
        'asset_id': asset_id,
        'asset_name': asset['name'] if asset else '',
        'asset_internal_code': asset['internal_code'] if asset else '',
        'from_site_id': from_site['site_id'] if from_site else '',
        'from_site_name': from_site['name'] if from_site else '',
        'to_site_id': to_site['site_id'] if to_site else '',
        'to_site_name': to_site['name'] if to_site else '',
        'transport_method': form.get('transport_method', ''),
        'estimated_duration_hours': float(form.get('estimated_duration_hours', 0) or 0),
        'reason': form.get('reason', ''),
        'priority': form.get('priority', 'normal'),
        'notes': form.get('notes', ''),
        'requested_by_id': request.session.get('user_id', ''),
        'requested_by_name': request.session.get('username', ''),
    }
    
    transfer_id = store.create_transfer(data)
    
    if transfer_id:
        request.session['flash'] = {'type': 'success', 'message': 'Transfer request submitted'}
        return RedirectResponse(url="/machinery/transfers", status_code=303)
    
    request.session['flash'] = {'type': 'error', 'message': 'Failed to create transfer'}
    return RedirectResponse(url="/machinery/transfers/new", status_code=303)


@router.get("/machinery/transfers/{transfer_id}", response_class=HTMLResponse)
@login_required
async def transfer_detail(request: Request, transfer_id: str):
    """View transfer details."""
    store = get_machinery_store()
    
    transfer = store.get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    
    return request.app.state.templates.TemplateResponse(
        "machinery/transfer_detail.html",
        {
            "request": request,
            "transfer": transfer,
            "title": f"Transfer: {transfer['asset_name']}",
        }
    )


@router.post("/machinery/transfers/{transfer_id}/approve")
@manager_or_admin_required
async def transfer_approve(request: Request, transfer_id: str):
    """Approve a transfer request."""
    store = get_machinery_store()
    
    store.approve_transfer(
        transfer_id,
        request.session.get('user_id', ''),
        request.session.get('username', '')
    )
    
    request.session['flash'] = {'type': 'success', 'message': 'Transfer approved'}
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


@router.post("/machinery/transfers/{transfer_id}/reject")
@manager_or_admin_required
async def transfer_reject(request: Request, transfer_id: str):
    """Reject a transfer request."""
    store = get_machinery_store()
    form = await request.form()
    
    store.reject_transfer(
        transfer_id,
        form.get('rejection_reason', ''),
        request.session.get('user_id', ''),
        request.session.get('username', '')
    )
    
    request.session['flash'] = {'type': 'info', 'message': 'Transfer rejected'}
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


@router.post("/machinery/transfers/{transfer_id}/start")
@manager_or_admin_required
async def transfer_start(request: Request, transfer_id: str):
    """Start a transfer (mark as in-transit)."""
    store = get_machinery_store()
    form = await request.form()
    
    store.start_transfer(
        transfer_id,
        form.get('driver_id', ''),
        form.get('driver_name', ''),
        form.get('vehicle_id', ''),
        form.get('vehicle_name', '')
    )
    
    request.session['flash'] = {'type': 'info', 'message': 'Transfer started - asset in transit'}
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


@router.post("/machinery/transfers/{transfer_id}/complete")
@login_required
async def transfer_complete(request: Request, transfer_id: str):
    """Complete a transfer."""
    store = get_machinery_store()
    
    store.complete_transfer(transfer_id)
    
    request.session['flash'] = {'type': 'success', 'message': 'Transfer completed'}
    return RedirectResponse(url=f"/machinery/transfers/{transfer_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MAINTENANCE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/maintenance", response_class=HTMLResponse)
@login_required
async def maintenance_list(request: Request, status: Optional[str] = None):
    """List all maintenance work orders."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    orders = store.get_maintenance_orders(cid, status=status)
    
    from models.machinery import MaintenanceStatus
    
    return request.app.state.templates.TemplateResponse(
        "machinery/maintenance_list.html",
        {
            "request": request,
            "orders": orders,
            "statuses": [e.value for e in MaintenanceStatus],
            "filter_status": status,
            "title": "Maintenance Work Orders",
        }
    )


@router.get("/machinery/maintenance/new", response_class=HTMLResponse)
@manager_or_admin_required
async def maintenance_new(request: Request, asset_id: Optional[str] = None):
    """New maintenance work order form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, is_active=True)
    sites = store.get_sites(cid, site_type='workshop')
    
    selected_asset = None
    if asset_id:
        selected_asset = store.get_asset(asset_id, cid)
    
    from models.machinery import MaintenanceType
    
    return request.app.state.templates.TemplateResponse(
        "machinery/maintenance_form.html",
        {
            "request": request,
            "order": None,
            "assets": assets,
            "workshops": sites,
            "selected_asset": selected_asset,
            "maintenance_types": [e.value for e in MaintenanceType],
            "is_edit": False,
            "title": "Create Work Order",
        }
    )


@router.post("/machinery/maintenance/new")
@manager_or_admin_required
async def maintenance_create(request: Request):
    """Create a maintenance work order."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    asset_id = form.get('asset_id', '')
    asset = store.get_asset(asset_id, cid)
    
    data = {
        'company_id': cid,
        'asset_id': asset_id,
        'asset_name': asset['name'] if asset else '',
        'asset_internal_code': asset['internal_code'] if asset else '',
        'maintenance_type': form.get('maintenance_type', 'preventive'),
        'priority': form.get('priority', 'normal'),
        'scheduled_date': form.get('scheduled_date') or None,
        'due_date': form.get('due_date') or None,
        'title': form.get('title', ''),
        'description': form.get('description', ''),
        'assigned_technician_id': form.get('assigned_technician_id', ''),
        'assigned_technician_name': form.get('assigned_technician_name', ''),
        'workshop_id': form.get('workshop_id', ''),
        'workshop_name': form.get('workshop_name', ''),
        'engine_hours_at_service': float(form.get('engine_hours_at_service', 0) or 0),
        'requires_approval': form.get('requires_approval') == 'on',
        'created_by': request.session.get('username', ''),
    }
    
    wo_id = store.create_maintenance(data)
    
    if wo_id:
        request.session['flash'] = {'type': 'success', 'message': 'Work order created'}
        return RedirectResponse(url=f"/machinery/maintenance/{wo_id}", status_code=303)
    
    request.session['flash'] = {'type': 'error', 'message': 'Failed to create work order'}
    return RedirectResponse(url="/machinery/maintenance/new", status_code=303)


@router.get("/machinery/maintenance/{work_order_id}", response_class=HTMLResponse)
@login_required
async def maintenance_detail(request: Request, work_order_id: str):
    """View maintenance work order details."""
    store = get_machinery_store()
    
    order = store.get_maintenance(work_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Work order not found")
    
    return request.app.state.templates.TemplateResponse(
        "machinery/maintenance_detail.html",
        {
            "request": request,
            "order": order,
            "title": f"WO: {order['work_order_number']}",
        }
    )


@router.post("/machinery/maintenance/{work_order_id}/start")
@login_required
async def maintenance_start(request: Request, work_order_id: str):
    """Start maintenance work."""
    store = get_machinery_store()
    
    store.start_maintenance(work_order_id)
    
    request.session['flash'] = {'type': 'info', 'message': 'Maintenance started'}
    return RedirectResponse(url=f"/machinery/maintenance/{work_order_id}", status_code=303)


@router.post("/machinery/maintenance/{work_order_id}/complete")
@login_required
async def maintenance_complete(request: Request, work_order_id: str):
    """Complete maintenance work order."""
    store = get_machinery_store()
    form = await request.form()
    
    data = {
        'work_performed': form.get('work_performed', ''),
        'findings': form.get('findings', ''),
        'recommendations': form.get('recommendations', ''),
        'labor_hours': float(form.get('labor_hours', 0) or 0),
        'labor_cost': float(form.get('labor_cost', 0) or 0),
        'parts_cost': float(form.get('parts_cost', 0) or 0),
        'external_service_cost': float(form.get('external_service_cost', 0) or 0),
        'engine_hours_at_service': float(form.get('engine_hours_at_service', 0) or 0),
    }
    
    # Parse parts used
    parts_str = form.get('parts_used', '')
    if parts_str:
        data['parts_used'] = [p.strip() for p in parts_str.split('\n') if p.strip()]
    
    if store.complete_maintenance(work_order_id, data):
        request.session['flash'] = {'type': 'success', 'message': 'Maintenance completed'}
    else:
        request.session['flash'] = {'type': 'error', 'message': 'Failed to complete'}
    
    return RedirectResponse(url=f"/machinery/maintenance/{work_order_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# OPERATOR SHIFT LOGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/shift-logs", response_class=HTMLResponse)
@login_required
async def shift_log_list(request: Request, asset_id: Optional[str] = None,
                         operator_id: Optional[str] = None):
    """List shift logs."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    logs = store.get_shift_logs(cid, asset_id=asset_id, operator_id=operator_id)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/shift_log_list.html",
        {
            "request": request,
            "logs": logs,
            "filter_asset": asset_id,
            "filter_operator": operator_id,
            "title": "Operator Shift Logs",
        }
    )


@router.get("/machinery/shift-logs/start", response_class=HTMLResponse)
@login_required
async def shift_log_start_form(request: Request, asset_id: Optional[str] = None):
    """Start shift form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    # Get available assets
    assets = store.get_available_assets(cid)
    sites = store.get_sites(cid)
    
    selected_asset = None
    if asset_id:
        selected_asset = store.get_asset(asset_id, cid)
    
    # Get operators from HR
    operators = []
    try:
        from employee_store import get_employee_store
        emp_store = get_employee_store()
        employees = emp_store.get_employees_by_company(cid)
        operators = [{'id': e.get('employee_id', ''), 'name': e.get('full_name', '')} 
                    for e in employees]
    except:
        pass
    
    return request.app.state.templates.TemplateResponse(
        "machinery/shift_start.html",
        {
            "request": request,
            "assets": assets,
            "sites": sites,
            "operators": operators,
            "selected_asset": selected_asset,
            "title": "Start Shift",
        }
    )


@router.post("/machinery/shift-logs/start")
@login_required
async def shift_log_start(request: Request):
    """Start an operator shift."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    asset_id = form.get('asset_id', '')
    operator_id = form.get('operator_id', '')
    
    # Check operator certification
    cert_check = store.check_operator_certification(operator_id, asset_id, cid)
    
    if not cert_check['is_certified']:
        issues = []
        if cert_check['missing_licenses']:
            issues.append(f"Missing licenses: {', '.join(cert_check['missing_licenses'])}")
        if cert_check['expired_licenses']:
            issues.append(f"Expired: {', '.join(cert_check['expired_licenses'])}")
        if cert_check['missing_training']:
            issues.append(f"Required training: {', '.join(cert_check['missing_training'])}")
        
        request.session['flash'] = {
            'type': 'error',
            'message': f"Cannot start shift - {'; '.join(issues)}"
        }
        return RedirectResponse(url="/machinery/shift-logs/start", status_code=303)
    
    asset = store.get_asset(asset_id, cid)
    site = store.get_site(form.get('site_id', ''))
    
    data = {
        'company_id': cid,
        'asset_id': asset_id,
        'asset_name': asset['name'] if asset else '',
        'operator_id': operator_id,
        'operator_name': form.get('operator_name', ''),
        'shift_date': date.today(),
        'shift_start': datetime.now(),
        'engine_hours_start': float(form.get('engine_hours_start', 0) or 0),
        'fuel_start_liters': float(form.get('fuel_start_liters', 0) or 0),
        'site_id': site['site_id'] if site else '',
        'site_name': site['name'] if site else '',
        'project_id': form.get('project_id', ''),
        'project_name': form.get('project_name', ''),
        'pre_shift_inspection_done': form.get('pre_shift_inspection') == 'on',
        'pre_shift_issues': form.get('pre_shift_issues', ''),
    }
    
    log_id = store.create_shift_log(data)
    
    if log_id:
        request.session['flash'] = {'type': 'success', 'message': 'Shift started'}
        return RedirectResponse(url=f"/machinery/shift-logs/{log_id}", status_code=303)
    
    request.session['flash'] = {'type': 'error', 'message': 'Failed to start shift'}
    return RedirectResponse(url="/machinery/shift-logs/start", status_code=303)


@router.get("/machinery/shift-logs/{log_id}", response_class=HTMLResponse)
@login_required
async def shift_log_detail(request: Request, log_id: str):
    """View shift log details."""
    store = get_machinery_store()
    
    log = store.get_shift_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Shift log not found")
    
    return request.app.state.templates.TemplateResponse(
        "machinery/shift_log_detail.html",
        {
            "request": request,
            "log": log,
            "title": f"Shift Log: {log['asset_name']}",
        }
    )


@router.post("/machinery/shift-logs/{log_id}/end")
@login_required
async def shift_log_end(request: Request, log_id: str):
    """End an operator shift."""
    store = get_machinery_store()
    form = await request.form()
    
    data = {
        'shift_end': datetime.now(),
        'engine_hours_end': float(form.get('engine_hours_end', 0) or 0),
        'fuel_end_liters': float(form.get('fuel_end_liters', 0) or 0),
        'fuel_added_liters': float(form.get('fuel_added_liters', 0) or 0),
        'idle_hours': float(form.get('idle_hours', 0) or 0),
        'post_shift_inspection_done': form.get('post_shift_inspection') == 'on',
        'post_shift_issues': form.get('post_shift_issues', ''),
        'work_description': form.get('work_description', ''),
        'incidents_reported': form.get('incidents_reported') == 'on',
        'incident_description': form.get('incident_description', ''),
    }
    
    # Parse tasks completed
    tasks_str = form.get('tasks_completed', '')
    if tasks_str:
        data['tasks_completed'] = [t.strip() for t in tasks_str.split('\n') if t.strip()]
    
    if store.end_shift_log(log_id, data):
        request.session['flash'] = {'type': 'success', 'message': 'Shift ended'}
    else:
        request.session['flash'] = {'type': 'error', 'message': 'Failed to end shift'}
    
    return RedirectResponse(url=f"/machinery/shift-logs/{log_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# FUEL LOGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/fuel-logs", response_class=HTMLResponse)
@login_required
async def fuel_log_list(request: Request, asset_id: Optional[str] = None):
    """List fuel logs."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    logs = store.get_fuel_logs(cid, asset_id=asset_id)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/fuel_log_list.html",
        {
            "request": request,
            "logs": logs,
            "filter_asset": asset_id,
            "title": "Fuel Logs",
        }
    )


@router.get("/machinery/fuel-logs/new", response_class=HTMLResponse)
@login_required
async def fuel_log_new(request: Request, asset_id: Optional[str] = None):
    """New fuel log form."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, is_active=True)
    sites = store.get_sites(cid)
    
    selected_asset = None
    if asset_id:
        selected_asset = store.get_asset(asset_id, cid)
    
    return request.app.state.templates.TemplateResponse(
        "machinery/fuel_log_form.html",
        {
            "request": request,
            "assets": assets,
            "sites": sites,
            "selected_asset": selected_asset,
            "title": "Record Fueling",
        }
    )


@router.post("/machinery/fuel-logs/new")
@login_required
async def fuel_log_create(request: Request):
    """Create a fuel log entry."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    asset_id = form.get('asset_id', '')
    asset = store.get_asset(asset_id, cid)
    site = store.get_site(form.get('site_id', ''))
    
    data = {
        'company_id': cid,
        'asset_id': asset_id,
        'asset_name': asset['name'] if asset else '',
        'fuel_type': form.get('fuel_type', 'diesel'),
        'quantity_liters': float(form.get('quantity_liters', 0) or 0),
        'unit_price': float(form.get('unit_price', 0) or 0),
        'odometer_reading': float(form.get('odometer_reading', 0) or 0),
        'engine_hours': float(form.get('engine_hours', 0) or 0),
        'site_id': site['site_id'] if site else '',
        'site_name': site['name'] if site else '',
        'fuel_station': form.get('fuel_station', ''),
        'receipt_number': form.get('receipt_number', ''),
        'fueled_by_id': request.session.get('user_id', ''),
        'fueled_by_name': request.session.get('username', ''),
        'notes': form.get('notes', ''),
    }
    
    fuel_id = store.create_fuel_log(data)
    
    if fuel_id:
        request.session['flash'] = {'type': 'success', 'message': 'Fuel log recorded'}
        return RedirectResponse(url="/machinery/fuel-logs", status_code=303)
    
    request.session['flash'] = {'type': 'error', 'message': 'Failed to record fuel log'}
    return RedirectResponse(url="/machinery/fuel-logs/new", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# OPERATOR ASSIGNMENT WITH CERTIFICATION CHECK
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/assets/{asset_id}/assign-operator", response_class=HTMLResponse)
@manager_or_admin_required
async def assign_operator_form(request: Request, asset_id: str):
    """Assign operator form with certification check."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    # Get operators from HR
    operators = []
    try:
        from employee_store import get_employee_store
        emp_store = get_employee_store()
        employees = emp_store.get_employees_by_company(cid)
        
        # Check certification for each operator
        for emp in employees:
            emp_id = emp.get('employee_id', '')
            cert_check = store.check_operator_certification(emp_id, asset_id, cid)
            operators.append({
                'id': emp_id,
                'name': emp.get('full_name', ''),
                'department': emp.get('department', ''),
                'is_certified': cert_check['is_certified'],
                'issues': cert_check,
            })
    except Exception as e:
        pass
    
    return request.app.state.templates.TemplateResponse(
        "machinery/assign_operator.html",
        {
            "request": request,
            "asset": asset,
            "operators": operators,
            "title": f"Assign Operator: {asset['name']}",
        }
    )


@router.post("/machinery/assets/{asset_id}/assign-operator")
@manager_or_admin_required
async def assign_operator(request: Request, asset_id: str):
    """Assign an operator to an asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    form = await request.form()
    
    result = store.assign_operator(
        asset_id=asset_id,
        operator_id=form.get('operator_id', ''),
        operator_name=form.get('operator_name', ''),
        is_backup=form.get('is_backup') == 'on',
        company_id=cid,
        force=form.get('force') == 'on'
    )
    
    if result['success']:
        request.session['flash'] = {'type': 'success', 'message': result['message']}
    else:
        request.session['flash'] = {'type': 'error', 'message': result['message']}
    
    return RedirectResponse(url=f"/machinery/assets/{asset_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# REPORTS & ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/machinery/reports/utilization", response_class=HTMLResponse)
@login_required
async def utilization_report(request: Request):
    """Asset utilization report."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, is_active=True)
    
    # Calculate utilization metrics
    for asset in assets:
        util = asset.get('utilization', {})
        total_hours = float(util.get('total_engine_hours', 0))
        idle_hours = float(util.get('total_idle_hours', 0))
        if total_hours > 0:
            asset['utilization_pct'] = round((total_hours - idle_hours) / total_hours * 100, 1)
        else:
            asset['utilization_pct'] = 0
    
    return request.app.state.templates.TemplateResponse(
        "machinery/report_utilization.html",
        {
            "request": request,
            "assets": assets,
            "title": "Utilization Report",
        }
    )


@router.get("/machinery/reports/project-cost", response_class=HTMLResponse)
@login_required
async def project_cost_report(request: Request, project_id: Optional[str] = None):
    """Project equipment cost report."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    cost_data = None
    if project_id:
        cost_data = store.calculate_project_equipment_cost(project_id, company_id=cid)
    
    # Get list of projects from sites
    sites = store.get_sites(cid, site_type='project')
    projects = [{'id': s['project_id'], 'name': s['project_name'] or s['name']} 
               for s in sites if s.get('project_id')]
    
    return request.app.state.templates.TemplateResponse(
        "machinery/report_project_cost.html",
        {
            "request": request,
            "projects": projects,
            "selected_project": project_id,
            "cost_data": cost_data,
            "title": "Project Equipment Cost",
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS (JSON)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/machinery/assets")
@login_required
async def api_get_assets(request: Request, status: Optional[str] = None,
                         category: Optional[str] = None):
    """API: Get assets."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    assets = store.get_assets(cid, status=status, category=category)
    return JSONResponse(content={"assets": assets})


@router.get("/api/machinery/assets/{asset_id}")
@login_required
async def api_get_asset(request: Request, asset_id: str):
    """API: Get single asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    asset = store.get_asset(asset_id, cid)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    return JSONResponse(content={"asset": asset})


@router.get("/api/machinery/check-certification")
@login_required
async def api_check_certification(request: Request, operator_id: str, asset_id: str):
    """API: Check operator certification for an asset."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    result = store.check_operator_certification(operator_id, asset_id, cid)
    return JSONResponse(content=result)


@router.get("/api/machinery/dashboard-stats")
@login_required
async def api_dashboard_stats(request: Request):
    """API: Get dashboard statistics."""
    store = get_machinery_store()
    cid = get_current_company(request)
    
    stats = store.get_dashboard_stats(cid)
    return JSONResponse(content=stats)
