from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from deps import flash, template_context, require_auth, login_required, admin_required, super_admin_required
from template_engine import templates
import logging
logger = logging.getLogger(__name__)

import io
import pandas as pd
from datetime import datetime
from inventory_data_store import InventoryDataStore

router = APIRouter(prefix="/inventory", tags=["inventory"])
inv_store = InventoryDataStore(data_dir="data")


def _company(request: Request) -> str:
    return (
        getattr(request.state, "company_id", None)
        or request.session.get("current_company_id")
        or request.session.get("company_id")
        or "default"
    )


@router.get("/", name="inventory_dashboard")
async def dashboard(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    ctx = template_context(request)
    ctx.update(summary=inv_store.get_dashboard_summary(company_id=company_id))
    return templates.TemplateResponse("inventory/dashboard.html", ctx)


@router.get("/items", name="inventory_items_list")
async def items_list(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    category = request.query_params.get("category", "")
    status   = request.query_params.get("status", "")
    items    = inv_store.get_all_items(
        status=status or None,
        category=category or None,
        company_id=company_id,
    )
    items.reverse()
    ctx = template_context(request)
    ctx.update(items=items, categories=inv_store.get_categories(company_id=company_id),
               selected_category=category, selected_status=status)
    return templates.TemplateResponse("inventory/items_list.html", ctx)


@router.get("/items/add", name="inventory_add_item_get")
async def add_item_get(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    return templates.TemplateResponse("inventory/add_item.html",
                                      {**template_context(request),
                                       "item": {}, "categories": inv_store.get_categories(company_id=company_id)})


@router.post("/items/add", name="inventory_add_item")
async def add_item_post(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    form = await request.form()
    item = {k: form.get(k, "").strip() for k in ["name","sku","category","description","unit",
            "serial_number","batch_number","barcode","location","is_rentable","valuation_method"]}
    item["company_id"] = company_id
    for f in ["unit_price","cost_price","current_stock","min_stock_level","reorder_point","reorder_quantity"]:
        item[f] = float(form.get(f, 0) or 0)
    if not item["name"]:
        flash(request, "Item name is required", "error")
        return templates.TemplateResponse("inventory/add_item.html",
                                          {**template_context(request), "item": item,
                                           "categories": inv_store.get_categories(company_id=company_id)})
    if not item["sku"]:
        item["sku"] = inv_store.generate_sku(item["category"], item["name"])
    if inv_store.save_item(item):
        flash(request, f"Item '{item['name']}' added!", "success")
        return RedirectResponse("/inventory/items", status_code=303)
    flash(request, "Error saving item", "error")
    return templates.TemplateResponse("inventory/add_item.html",
                                      {**template_context(request), "item": item,
                                       "categories": inv_store.get_categories(company_id=company_id)})


@router.get("/items/edit/{item_id}", name="inventory_edit_item_get")
async def edit_item_get(item_id: str, request: Request, user=Depends(login_required)):
    company_id = _company(request)
    item = inv_store.get_item_by_id(item_id, company_id=company_id)
    if not item:
        flash(request, "Item not found", "error")
        return RedirectResponse("/inventory/items", status_code=302)
    return templates.TemplateResponse("inventory/edit_item.html",
                                      {**template_context(request), "item": item,
                                       "categories": inv_store.get_categories(company_id=company_id)})


@router.post("/items/edit/{item_id}", name="inventory_edit_item")
async def edit_item_post(item_id: str, request: Request, user=Depends(login_required)):
    company_id = _company(request)
    item = inv_store.get_item_by_id(item_id, company_id=company_id)
    if not item:
        flash(request, "Item not found", "error")
        return RedirectResponse("/inventory/items", status_code=302)
    form = await request.form()
    item.update({k: form.get(k, "").strip() for k in ["name","sku","category","description","unit",
                "serial_number","batch_number","barcode","location","is_rentable","valuation_method"]})
    for f in ["unit_price","cost_price","min_stock_level","reorder_point","reorder_quantity"]:
        item[f] = float(form.get(f, 0) or 0)
    item["company_id"] = company_id
    if inv_store.save_item(item):
        flash(request, "Item updated!", "success")
        return RedirectResponse("/inventory/items", status_code=303)
    flash(request, "Error updating item", "error")
    return templates.TemplateResponse("inventory/edit_item.html",
                                      {**template_context(request), "item": item,
                                       "categories": inv_store.get_categories(company_id=company_id)})


@router.post("/items/delete/{item_id}", name="inventory_delete_item")
async def delete_item(item_id: str, request: Request, user=Depends(login_required)):
    inv_store.delete_item(item_id, company_id=_company(request))
    flash(request, "Item deleted", "success")
    return RedirectResponse("/inventory/items", status_code=303)


@router.get("/items/import", name="inventory_import_items_get")
async def import_items_get(request: Request, user=Depends(login_required)):
    return templates.TemplateResponse("inventory/import_items.html", template_context(request))


@router.post("/items/import", name="inventory_import_items")
async def import_items_post(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    form = await request.form()
    _file = form.get("file")
    if not _file or not getattr(_file, "filename", None):  # type: ignore[union-attr]
        flash(request, "No file selected", "error")
        return RedirectResponse("/inventory/items/import", status_code=303)
    try:
        content = await _file.read()  # type: ignore[union-attr]
        df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        if df.empty:
            flash(request, "No data in file", "error")
            return RedirectResponse("/inventory/items/import", status_code=303)
        result = inv_store.import_items_from_dataframe(df, _file.filename, company_id=company_id)
        ctx = template_context(request)
        ctx.update(result=result, filename=_file.filename)
        return templates.TemplateResponse("inventory/import_result.html", ctx)
    except Exception as e:
        flash(request, f"Error: {e}", "error")
        return RedirectResponse("/inventory/items/import", status_code=303)


@router.get("/items/export", name="inventory_export_items")
async def export_items(request: Request, user=Depends(login_required)):
    from fastapi.responses import FileResponse as _FR
    filepath = inv_store.export_items_to_excel(company_id=_company(request))
    if filepath:
        fname = f"inventory_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return _FR(filepath, filename=fname,
                   media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    flash(request, "Export failed", "error")
    return RedirectResponse("/inventory/items", status_code=302)


@router.get("/download-template", name="inventory_download_template")
async def download_template(request: Request, user=Depends(login_required)):
    """Download an Excel template for bulk inventory import."""
    import tempfile
    import os
    
    # Define template columns matching the import expectations
    # Column names will be normalized (lowercase, spaces to underscores) during import
    template_data = {
        'Name': ['Example Item 1', 'Example Item 2'],
        'SKU': ['SKU001', 'SKU002'],
        'Category': ['Electronics', 'Office Supplies'],
        'Description': ['Sample description', 'Another description'],
        'Unit of Measure': ['pcs', 'box'],
        'Unit Cost': [100.00, 25.50],
        'Quantity on Hand': [50, 100],
        'Reorder Point': [10, 20],
        'Reorder Quantity': [25, 50],
        'Location': ['Warehouse A', 'Warehouse B'],
        'Status': ['active', 'active']
    }
    
    df = pd.DataFrame(template_data)
    fd, filepath = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventory Template')
        # Add a notes sheet with field descriptions
        notes_data = {
            'Field': ['Name', 'SKU', 'Category', 'Description', 'Unit of Measure', 
                      'Unit Cost', 'Quantity on Hand', 'Reorder Point', 'Reorder Quantity', 
                      'Location', 'Status'],
            'Description': [
                'Item name (required)',
                'Stock Keeping Unit code (auto-generated if blank)',
                'Item category (e.g., Electronics, Office Supplies)',
                'Item description',
                'Unit of measure (e.g., pcs, box, kg)',
                'Purchase/cost price per unit',
                'Current stock quantity',
                'Quantity level that triggers reorder alert',
                'Quantity to order when reordering',
                'Warehouse/bin location',
                'active or inactive'
            ]
        }
        pd.DataFrame(notes_data).to_excel(writer, index=False, sheet_name='Field Descriptions')
    
    return FileResponse(
        filepath,
        filename='inventory_import_template.xlsx',
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@router.get("/movements", name="inventory_movements_list")
async def movements_list(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    mtype     = request.query_params.get("type", "")
    movements = inv_store.get_all_movements(movement_type=mtype or None, company_id=company_id)
    movements.reverse()
    ctx = template_context(request)
    ctx.update(movements=movements, selected_type=mtype)
    return templates.TemplateResponse("inventory/movements_list.html", ctx)


@router.get("/valuation", name="inventory_valuation_report")
async def valuation_report(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    ctx = template_context(request)
    ctx.update(valuation=inv_store.get_valuation_report(company_id=company_id))
    return templates.TemplateResponse("inventory/valuation.html", ctx)


@router.get("/replenishment", name="inventory_replenishment")
async def replenishment(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    ctx = template_context(request)
    ctx.update(alerts=inv_store.get_replenishment_alerts(company_id=company_id))
    return templates.TemplateResponse("inventory/replenishment.html", ctx)


@router.get("/allocations", name="inventory_allocations")
async def allocations(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    ctx = template_context(request)
    ctx.update(allocations=inv_store.get_all_allocations(company_id=company_id))
    return templates.TemplateResponse("inventory/allocations.html", ctx)


@router.get("/maintenance", name="inventory_maintenance")
async def maintenance(request: Request, user=Depends(login_required)):
    company_id = _company(request)
    ctx = template_context(request)
    ctx.update(schedules=inv_store.get_maintenance_schedules(company_id=company_id))
    return templates.TemplateResponse("inventory/maintenance.html", ctx)


@router.get("/reports", name="inventory_reports")
async def reports(request: Request, user=Depends(login_required)):
    """Inventory reports hub - links to stock, valuation, and movement reports."""
    company_id = _company(request)
    ctx = template_context(request)
    ctx.update(
        summary=inv_store.get_dashboard_summary(company_id=company_id),
        valuation=inv_store.get_valuation_report(company_id=company_id),
    )
    return templates.TemplateResponse("inventory/reports.html", ctx)
