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


class InventoryDataStore:
    """PostgreSQL-backed inventory management."""

    def __init__(self, data_dir=None):
        pass

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------
    def add_item(self, data: dict) -> Optional[str]:
        cid = data.get('company_id', 'default')
        item_id = data.get('item_id') or str(uuid.uuid4())[:8].upper()
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO inventory_items
                       (item_id, company_id, name, sku, category, description,
                        unit_of_measure, unit_cost, quantity_on_hand, reorder_point,
                        reorder_quantity, location, status, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (item_id, company_id) DO NOTHING""",
                    (item_id, cid,
                     data.get('name', ''),
                     data.get('sku', ''),
                     data.get('category', ''),
                     data.get('description', ''),
                     data.get('unit_of_measure', 'pcs'),
                     float(data.get('unit_cost', 0)),
                     float(data.get('quantity_on_hand', 0)),
                     float(data.get('reorder_point', 0)),
                     float(data.get('reorder_quantity', 0)),
                     data.get('location', ''),
                     data.get('status', 'active'),
                     datetime.utcnow().isoformat())
                )
            return item_id
        except Exception as e:
            logger.error("add_item failed: %s", e)
            return None

    def get_items(self, company_id: str = None) -> pd.DataFrame:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_items WHERE company_id=%s AND status='active' "
                    "ORDER BY name",
                    (cid,)
                )
                rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        except Exception as e:
            logger.error("get_items failed: %s", e)
            return pd.DataFrame()

    def get_item(self, item_id: str, company_id: str = None) -> Optional[dict]:
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute(
                    "SELECT * FROM inventory_items WHERE item_id=%s AND company_id=%s",
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
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE inventory_items SET
                       name=%s, sku=%s, category=%s, description=%s,
                       unit_of_measure=%s, unit_cost=%s, quantity_on_hand=%s,
                       reorder_point=%s, reorder_quantity=%s, location=%s,
                       status=%s
                       WHERE item_id=%s AND company_id=%s""",
                    (data.get('name', ''),
                     data.get('sku', ''),
                     data.get('category', ''),
                     data.get('description', ''),
                     data.get('unit_of_measure', 'pcs'),
                     float(data.get('unit_cost', 0)),
                     float(data.get('quantity_on_hand', 0)),
                     float(data.get('reorder_point', 0)),
                     float(data.get('reorder_quantity', 0)),
                     data.get('location', ''),
                     data.get('status', 'active'),
                     item_id, cid)
                )
            return True
        except Exception as e:
            logger.error("update_item failed: %s", e)
            return False

    def delete_item(self, item_id: str, company_id: str = None) -> bool:
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE inventory_items SET status='deleted' "
                    "WHERE item_id=%s AND company_id=%s",
                    (item_id, cid)
                )
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
            with get_cursor() as cur:
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
        cid = data.get('company_id', 'default')
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO inventory_movements
                           (company_id, item_id, movement_type, quantity,
                            reference, notes, moved_by, moved_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (cid,
                         data.get('item_id', ''),
                         data.get('movement_type', 'in'),
                         float(data.get('quantity', 0)),
                         data.get('reference', ''),
                         data.get('notes', ''),
                         data.get('moved_by', ''),
                         datetime.utcnow().isoformat())
                    )
                    # update quantity
                    qty = float(data.get('quantity', 0))
                    if data.get('movement_type') in ('out', 'issue', 'allocation'):
                        qty = -qty
                    cur.execute(
                        """UPDATE inventory_items
                           SET quantity_on_hand = quantity_on_hand + %s
                           WHERE item_id=%s AND company_id=%s""",
                        (qty, data.get('item_id', ''), cid)
                    )
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
                        "ORDER BY moved_at DESC",
                        (cid, item_id)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM inventory_movements WHERE company_id=%s "
                        "ORDER BY moved_at DESC",
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
            with get_cursor() as cur:
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
            with get_cursor() as cur:
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


# Singleton
inventory_store = InventoryDataStore()
