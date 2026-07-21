"""
Inventory Data Store - PostgreSQL backend (7 tables)
"""

import logging
import uuid
import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


def _f(value) -> float:
    """Form/Excel value → float; empty strings and junk become 0."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _bool_text(value) -> str:
    """Normalize checkbox/select values to the 'true'/'false' TEXT column."""
    return 'true' if str(value or '').strip().lower() in ('true', 'yes', '1', 'on') else 'false'


class InventoryDataStore:
    """PostgreSQL-backed inventory management."""

    def __init__(self, data_dir=None):
        pass

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------
    def add_item(self, data: dict) -> Optional[str]:
        # Column names follow init_db.sql inventory_items — the earlier version
        # of this method wrote to a legacy schema (item_id/unit_of_measure/
        # quantity_on_hand) that doesn't exist, so every insert failed.
        cid = data.get('company_id', 'default')
        item_id = data.get('item_id') or data.get('id') or str(uuid.uuid4())[:8].upper()
        now = datetime.utcnow().isoformat()
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """INSERT INTO inventory_items
                       (id, company_id, sku, name, description, category, unit,
                        unit_price, cost_price, serial_number, batch_number, barcode,
                        current_stock, min_stock_level, reorder_point, reorder_quantity,
                        location, is_rentable, status, valuation_method, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO NOTHING""",
                    (item_id, cid,
                     data.get('sku', ''),
                     data.get('name', ''),
                     data.get('description', ''),
                     data.get('category', ''),
                     data.get('unit') or data.get('unit_of_measure') or 'pcs',
                     _f(data.get('unit_price')),
                     _f(data.get('cost_price') or data.get('unit_cost')),
                     data.get('serial_number', ''),
                     data.get('batch_number', ''),
                     data.get('barcode', ''),
                     _f(data.get('current_stock') or data.get('quantity_on_hand')),
                     _f(data.get('min_stock_level')),
                     _f(data.get('reorder_point')),
                     _f(data.get('reorder_quantity')),
                     data.get('location', ''),
                     _bool_text(data.get('is_rentable')),
                     data.get('status', 'active'),
                     data.get('valuation_method') or 'FIFO',
                     now, now)
                )
            self._invalidate_cache(cid)
            return item_id
        except Exception as e:
            logger.error("add_item failed: %s", e)
            return None

    def _invalidate_cache(self, company_id: str = 'default') -> None:
        from extensions import cache
        cache.delete(f"inventory:{company_id}")
        cache.delete(f"dashboard_stats:{company_id}")

    def get_items(self, company_id: str = None) -> pd.DataFrame:
        from extensions import cache
        cid = company_id or 'default'
        ck  = f"inventory:{cid}"
        cached = cache.get(ck)
        if cached is not None:
            return pd.DataFrame(cached)
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_items WHERE company_id=%s AND status='active' "
                    "ORDER BY name",
                    (cid,)
                )
                rows = cur.fetchall()
                result = [dict(r) for r in rows] if rows else []
                cache.set(ck, result, timeout=120)
                return pd.DataFrame(result) if result else pd.DataFrame()
        except Exception as e:
            logger.error("get_items failed: %s", e)
            return pd.DataFrame()

    def get_item(self, item_id: str, company_id: str = None) -> Optional[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_items WHERE id=%s AND company_id=%s",
                    (item_id, cid)
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_item failed: %s", e)
            return None

    def update_item(self, item_id: str, data: dict, company_id: str = None) -> bool:
        cid = company_id or data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """UPDATE inventory_items SET
                       name=%s, sku=%s, category=%s, description=%s, unit=%s,
                       unit_price=%s, cost_price=%s, serial_number=%s, batch_number=%s,
                       barcode=%s, current_stock=%s, min_stock_level=%s,
                       reorder_point=%s, reorder_quantity=%s, location=%s,
                       is_rentable=%s, status=%s, valuation_method=%s, updated_at=%s
                       WHERE id=%s AND company_id=%s""",
                    (data.get('name', ''),
                     data.get('sku', ''),
                     data.get('category', ''),
                     data.get('description', ''),
                     data.get('unit') or data.get('unit_of_measure') or 'pcs',
                     _f(data.get('unit_price')),
                     _f(data.get('cost_price') or data.get('unit_cost')),
                     data.get('serial_number', ''),
                     data.get('batch_number', ''),
                     data.get('barcode', ''),
                     _f(data.get('current_stock') or data.get('quantity_on_hand')),
                     _f(data.get('min_stock_level')),
                     _f(data.get('reorder_point')),
                     _f(data.get('reorder_quantity')),
                     data.get('location', ''),
                     _bool_text(data.get('is_rentable')),
                     data.get('status', 'active'),
                     data.get('valuation_method') or 'FIFO',
                     datetime.utcnow().isoformat(),
                     item_id, cid)
                )
            self._invalidate_cache(cid)
            return True
        except Exception as e:
            logger.error("update_item failed: %s", e)
            return False

    def delete_item(self, item_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "UPDATE inventory_items SET status='deleted' "
                    "WHERE id=%s AND company_id=%s",
                    (item_id, cid)
                )
            self._invalidate_cache(cid)
            return True
        except Exception as e:
            logger.error("delete_item failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------
    def add_category(self, data: dict) -> bool:
        cid = data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """INSERT INTO inventory_categories
                       (company_id, name, description, created_at)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (company_id, name) DO NOTHING""",
                    (cid, data.get('name', ''),
                     data.get('description', ''),
                     datetime.utcnow().isoformat())
                )
            return True
        except Exception as e:
            logger.error("add_category failed: %s", e)
            return False

    def get_categories(self, company_id: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_categories WHERE company_id=%s ORDER BY name",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_categories failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Movements
    # ------------------------------------------------------------------
    def record_movement(self, data: dict) -> bool:
        # Columns follow init_db.sql inventory_movements (the add_movement form
        # already sends these exact field names).
        cid = data.get('company_id', 'default')
        now = datetime.utcnow().isoformat()
        qty = _f(data.get('quantity'))
        unit_cost = _f(data.get('unit_cost'))
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    item_name = data.get('item_name', '')
                    if not item_name and data.get('item_id'):
                        cur.execute("SELECT name FROM inventory_items WHERE id=%s AND company_id=%s",
                                    (data.get('item_id'), cid))
                        row = cur.fetchone()
                        item_name = row['name'] if row else ''
                    cur.execute(
                        """INSERT INTO inventory_movements
                           (id, company_id, item_id, item_name, movement_type, quantity,
                            unit_cost, total_cost, from_location, to_location,
                            reference_number, reason, approved_by, approval_status,
                            date, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), cid,
                         data.get('item_id', ''),
                         item_name,
                         data.get('movement_type', 'in'),
                         qty, unit_cost, qty * unit_cost,
                         data.get('from_location', ''),
                         data.get('to_location', ''),
                         data.get('reference_number') or data.get('reference', ''),
                         data.get('reason') or data.get('notes', ''),
                         data.get('approved_by') or data.get('moved_by', ''),
                         data.get('approval_status', 'approved'),
                         data.get('date') or now,
                         now)
                    )
                    # update stock level
                    delta = -qty if data.get('movement_type') in ('out', 'issue', 'allocation') else qty
                    cur.execute(
                        """UPDATE inventory_items
                           SET current_stock = current_stock + %s, updated_at=%s
                           WHERE id=%s AND company_id=%s""",
                        (delta, now, data.get('item_id', ''), cid)
                    )
            self._invalidate_cache(cid)
            return True
        except Exception as e:
            logger.error("record_movement failed: %s", e)
            return False

    def get_movements(self, item_id: str = None, company_id: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                if item_id:
                    cur.execute(
                        "SELECT * FROM inventory_movements WHERE company_id=%s AND item_id=%s "
                        "ORDER BY created_at DESC",
                        (cid, item_id)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM inventory_movements WHERE company_id=%s "
                        "ORDER BY created_at DESC",
                        (cid,)
                    )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_movements failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Requisitions
    # ------------------------------------------------------------------
    def add_requisition(self, data: dict) -> Optional[int]:
        cid = data.get('company_id', 'default')
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    """INSERT INTO inventory_requisitions
                       (company_id, item_id, quantity, reason, requested_by,
                        status, requested_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (cid,
                     data.get('item_id', ''),
                     float(data.get('quantity', 0)),
                     data.get('reason', ''),
                     data.get('requested_by', ''),
                     'pending',
                     datetime.utcnow().isoformat())
                )
                row = cur.fetchone()
                return row['id'] if row else None
        except Exception as e:
            logger.error("add_requisition failed: %s", e)
            return None

    def get_requisitions(self, company_id: str = None, status: str = None) -> List[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                if status:
                    cur.execute(
                        "SELECT * FROM inventory_requisitions WHERE company_id=%s AND status=%s "
                        "ORDER BY requested_at DESC",
                        (cid, status)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM inventory_requisitions WHERE company_id=%s "
                        "ORDER BY requested_at DESC",
                        (cid,)
                    )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_requisitions failed: %s", e)
            return []

    def update_requisition_status(self, req_id: int, status: str,
                                   company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "UPDATE inventory_requisitions SET status=%s WHERE id=%s AND company_id=%s",
                    (status, req_id, cid)
                )
            return True
        except Exception as e:
            logger.error("update_requisition_status failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Bulk import
    # ------------------------------------------------------------------
    def bulk_import(self, records: List[Dict], company_id: str = None) -> dict:
        result = {'imported': 0, 'errors': []}
        cid = company_id or 'default'
        imported_at = datetime.utcnow().isoformat()
        for r in records:
            r['company_id'] = cid
            item_id = self.add_item(r)
            if item_id:
                result['imported'] += 1
            else:
                result['errors'].append(f"Failed: {r.get('name','')}")
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO inventory_import_history
                       (company_id, imported_at, record_count, status)
                       VALUES (%s,%s,%s,%s)""",
                    (cid, imported_at, result['imported'], 'completed')
                )
        except Exception:
            pass
        return result


    def get_all_items(self, status: str = None, category: str = None,
                      company_id: str = None) -> list:
        """Return list-of-dicts version of get_items() with optional filters."""
        df = self.get_items(company_id=company_id)
        if df is None or (hasattr(df, 'empty') and df.empty):
            return []
        if status and 'status' in df.columns:
            df = df[df['status'] == status]
        if category and 'category' in df.columns:
            df = df[df['category'] == category]
        return df.to_dict('records')

    def get_dashboard_summary(self, company_id: str = None) -> dict:
        """Aggregate counts and values for the inventory dashboard."""
        try:
            items = self.get_items(company_id=company_id)
            movements = self.get_movements(company_id=company_id)
            requisitions = self.get_requisitions(
                company_id=company_id, status='pending'
            )
            if items is None or (hasattr(items, 'empty') and items.empty):
                total_items, total_value, low_stock_items = 0, 0.0, []
                low_stock_count = 0
            else:
                total_items = len(items)
                price_col = next(
                    (c for c in ('unit_price', 'cost_price', 'price') if c in items.columns),
                    None
                )
                total_value = float(items[price_col].fillna(0).sum()) if price_col else 0.0
                if 'quantity' in items.columns and 'reorder_level' in items.columns:
                    low = items[items['quantity'].fillna(0) < items['reorder_level'].fillna(0)]
                elif 'stock' in items.columns and 'min_stock' in items.columns:
                    low = items[items['stock'].fillna(0) < items['min_stock'].fillna(0)]
                else:
                    low = items.iloc[0:0]  # empty slice
                low_stock_items = low.to_dict('records')
                low_stock_count = len(low)
            return {
                'total_items':          total_items,
                'total_stock_value':    total_value,
                'low_stock_count':      low_stock_count,
                'total_movements':      len(movements) if movements else 0,
                'active_allocations':   0,
                'upcoming_maintenance': 0,
                'overdue_maintenance':  0,
                'pending_requisitions': len(requisitions) if requisitions else 0,
                'low_stock_items':      low_stock_items,
                'recent_movements':     (movements[-10:] if movements else []),
            }
        except Exception as e:
            logger.error("get_dashboard_summary failed: %s", e)
            return {
                'total_items': 0, 'total_stock_value': 0.0, 'low_stock_count': 0,
                'total_movements': 0, 'active_allocations': 0,
                'upcoming_maintenance': 0, 'overdue_maintenance': 0,
                'pending_requisitions': 0, 'low_stock_items': [], 'recent_movements': [],
            }

    # ------------------------------------------------------------------
    # Methods required by inventory_routes.py
    # ------------------------------------------------------------------
    def generate_sku(self, category: str = '', name: str = '') -> str:
        """Generate a unique SKU code based on category and name."""
        import uuid
        prefix = (category[:3] if category else 'ITM').upper()
        suffix = uuid.uuid4().hex[:6].upper()
        return f"{prefix}-{suffix}"

    def save_item(self, data: dict) -> Optional[str]:
        """Save an item - create if new, update if exists."""
        item_id = data.get('item_id') or data.get('id')
        if item_id:
            self.update_item(item_id, data, company_id=data.get('company_id'))
            return item_id
        return self.add_item(data)

    def get_item_by_id(self, item_id: str, company_id: str = None) -> Optional[dict]:
        """Alias for get_item() - used by routes."""
        return self.get_item(item_id, company_id)

    def get_all_movements(self, movement_type: str = None, company_id: str = None) -> List[dict]:
        """Return all movements, optionally filtered by type."""
        movements = self.get_movements(company_id=company_id)
        if movement_type and movements:
            movements = [m for m in movements if m.get('movement_type') == movement_type]
        return movements

    def get_categories(self, company_id: str = None) -> List[str]:
        """Return distinct categories from inventory items."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT DISTINCT category FROM inventory_items "
                    "WHERE company_id=%s AND category IS NOT NULL AND category != '' "
                    "ORDER BY category",
                    (cid,)
                )
                return [r['category'] for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_categories failed: %s", e)
            return ['Electronics', 'Office Supplies', 'Furniture', 'Raw Materials', 'Finished Goods']

    def get_valuation_report(self, company_id: str = None) -> dict:
        """Generate inventory valuation report."""
        items = self.get_all_items(company_id=company_id)
        total_value = 0.0
        by_category = {}
        for item in items:
            qty = float(item.get('current_stock', 0) or item.get('quantity_on_hand', 0) or item.get('quantity', 0) or 0)
            price = float(item.get('unit_price', 0) or item.get('cost_price', 0) or item.get('price', 0) or 0)
            value = qty * price
            total_value += value
            cat = item.get('category', 'Uncategorized')
            by_category[cat] = by_category.get(cat, 0.0) + value
        return {
            'total_value': total_value,
            'item_count': len(items),
            'by_category': by_category,
            'items': items,
        }

    def get_replenishment_alerts(self, company_id: str = None) -> List[dict]:
        """Return items that are below their reorder level."""
        items = self.get_all_items(company_id=company_id)
        alerts = []
        for item in items:
            qty = float(item.get('current_stock', 0) or item.get('quantity_on_hand', 0) or item.get('quantity', 0) or 0)
            reorder = float(item.get('reorder_point', 0) or item.get('min_stock_level', 0)
                            or item.get('reorder_level', 0) or item.get('min_stock', 10) or 10)
            if qty < reorder:
                alerts.append({
                    **item,
                    'current_qty': qty,
                    'reorder_level': reorder,
                    'shortfall': reorder - qty,
                })
        return alerts

    def get_all_allocations(self, company_id: str = None) -> List[dict]:
        """Return all inventory allocations (for events, projects, etc.)."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_allocations WHERE company_id=%s "
                    "ORDER BY allocated_at DESC",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            # Table might not exist - return empty
            return []

    def get_maintenance_schedules(self, company_id: str = None) -> List[dict]:
        """Return maintenance schedules for inventory items."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_maintenance WHERE company_id=%s "
                    "ORDER BY due_date ASC",
                    (cid,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            # Table might not exist - return empty
            return []

    def import_items_from_dataframe(self, df, filename: str = '',
                                    company_id: str = None) -> dict:
        """Import inventory items from a pandas DataFrame."""
        import pandas as pd
        result = {'success': False, 'imported': 0, 'errors': [], 'message': ''}
        
        if df is None or df.empty:
            result['message'] = 'No data to import'
            return result
        
        # Normalize column names
        df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]
        
        records = df.to_dict('records')
        for r in records:
            r['created_by'] = f'import:{filename}'
        
        import_result = self.bulk_import(records, company_id=company_id)
        result['success'] = import_result['imported'] > 0
        result['imported'] = import_result['imported']
        result['errors'] = import_result.get('errors', [])
        result['message'] = f"Imported {import_result['imported']} items"
        
        return result

    def export_items_to_excel(self, company_id: str = None) -> Optional[str]:
        """Export inventory items to an Excel file."""
        import tempfile
        import os
        import pandas as pd
        
        items = self.get_all_items(company_id=company_id)
        if not items:
            items = [{'name': '', 'sku': '', 'category': '', 'quantity': 0, 'unit_price': 0}]
        
        fd, filepath = tempfile.mkstemp(suffix='.xlsx')
        os.close(fd)
        
        df = pd.DataFrame(items)
        df.to_excel(filepath, index=False, sheet_name='Inventory')
        return filepath


# Singleton
inventory_store = InventoryDataStore()
