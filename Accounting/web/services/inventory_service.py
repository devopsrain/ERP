"""
Async Inventory Service — Step 7 (service layer) + Step 2 (native async reads).

Routes call this service.  The service owns:
  - Read queries via native asyncpg (no event-loop blocking).
  - Write operations via asyncpg with explicit cache invalidation.
  - Business rules (low-stock detection, dashboard aggregation).

Data stores are no longer called directly from routes for inventory;
use this service instead.

Example usage from a route:
    from services.inventory_service import inventory_service

    @router.get("/inventory/")
    async def index(request: Request, user=Depends(login_required)):
        items = await inventory_service.get_items(company_id)
        return templates.TemplateResponse("inventory/index.html", {"items": items, ...})
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_ITEMS = 120     # 2 minutes for item lists
_CACHE_TTL_DASH  = 60      # 1 minute for dashboard summary

# ── helpers ──────────────────────────────────────────────────────────

def _cache_key_items(company_id: str) -> str:
    return f"svc:inventory:items:{company_id}"

def _cache_key_dash(company_id: str) -> str:
    return f"svc:inventory:dash:{company_id}"

def _ext_cache():
    from extensions import cache
    return cache

def _invalidate(company_id: str) -> None:
    """Evict all inventory caches for this tenant on any write."""
    c = _ext_cache()
    c.delete(_cache_key_items(company_id))
    c.delete(_cache_key_dash(company_id))
    c.delete(f"dashboard_stats:{company_id}")     # also purge the portal stats card
    c.delete(f"api:inventory:{company_id}:0:100:0")   # purge paged API caches
    c.delete(f"api:inventory:{company_id}:1:100:0")


# ── service class ────────────────────────────────────────────────────

class InventoryService:
    """
    Async facade over the inventory DB tables.
    All methods are async and use asyncpg directly.
    Falls back to run_sync + the legacy sync store when asyncpg is unavailable.
    """

    # ── reads ─────────────────────────────────────────────────────

    async def get_items(self, company_id: str = "default",
                        status: str = "active",
                        category: Optional[str] = None) -> list[dict]:
        """Return all inventory items for a company (cached)."""
        ck = _cache_key_items(company_id)
        cached = _ext_cache().get(ck)
        if cached is not None:
            items = cached
        else:
            try:
                from async_db import get_async_tenant_conn
                async with get_async_tenant_conn(company_id) as conn:
                    rows = await conn.fetch(
                        "SELECT * FROM inventory_items WHERE company_id=$1 "
                        "AND status='active' ORDER BY name",
                        company_id,
                    )
                    items = [dict(r) for r in rows]
                _ext_cache().set(ck, items, timeout=_CACHE_TTL_ITEMS)
            except Exception:
                from db import run_sync
                from inventory_data_store import inventory_store
                items = await run_sync(lambda: inventory_store.get_all_items(company_id=company_id))

        if status and status != "all":
            items = [i for i in items if i.get("status") == status]
        if category:
            items = [i for i in items if i.get("category") == category]
        return items

    async def get_item(self, item_id: str, company_id: str = "default") -> Optional[dict]:
        """Return a single item by id."""
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(company_id) as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM inventory_items WHERE item_id=$1 AND company_id=$2",
                    item_id, company_id,
                )
                return dict(row) if row else None
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            return await run_sync(lambda: inventory_store.get_item(item_id, company_id))

    async def get_low_stock(self, company_id: str = "default") -> list[dict]:
        """Return items where quantity_on_hand <= reorder_point."""
        items = await self.get_items(company_id)
        return [
            i for i in items
            if (float(i.get("quantity_on_hand") or 0)
                <= float(i.get("reorder_point") or 0))
        ]

    async def get_categories(self, company_id: str = "default") -> list[dict]:
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(company_id) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM inventory_categories WHERE company_id=$1 ORDER BY name",
                    company_id,
                )
                return [dict(r) for r in rows]
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            return await run_sync(lambda: inventory_store.get_categories(company_id))

    async def get_movements(self, company_id: str = "default",
                             item_id: Optional[str] = None) -> list[dict]:
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(company_id) as conn:
                if item_id:
                    rows = await conn.fetch(
                        "SELECT * FROM inventory_movements "
                        "WHERE company_id=$1 AND item_id=$2 ORDER BY moved_at DESC",
                        company_id, item_id,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM inventory_movements WHERE company_id=$1 "
                        "ORDER BY moved_at DESC LIMIT 200",
                        company_id,
                    )
                return [dict(r) for r in rows]
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            return await run_sync(lambda: inventory_store.get_movements(item_id, company_id))

    async def get_dashboard_summary(self, company_id: str = "default") -> dict:
        """Aggregate dashboard stats (cached separately from item list)."""
        ck = _cache_key_dash(company_id)
        cached = _ext_cache().get(ck)
        if cached is not None:
            return cached

        items        = await self.get_items(company_id)
        low_stock    = await self.get_low_stock(company_id)
        movements    = await self.get_movements(company_id)
        requisitions = await self.get_requisitions(company_id, status="pending")

        total_value = sum(
            float(i.get("unit_cost") or 0) * float(i.get("quantity_on_hand") or 0)
            for i in items
        )
        summary = {
            "total_items":          len(items),
            "total_stock_value":    round(total_value, 2),
            "low_stock_count":      len(low_stock),
            "total_movements":      len(movements),
            "pending_requisitions": len(requisitions),
            "low_stock_items":      low_stock[:10],
            "recent_movements":     movements[:10],
        }
        _ext_cache().set(ck, summary, timeout=_CACHE_TTL_DASH)
        return summary

    async def get_requisitions(self, company_id: str = "default",
                                status: Optional[str] = None) -> list[dict]:
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(company_id) as conn:
                if status:
                    rows = await conn.fetch(
                        "SELECT * FROM inventory_requisitions "
                        "WHERE company_id=$1 AND status=$2 ORDER BY requested_at DESC",
                        company_id, status,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM inventory_requisitions WHERE company_id=$1 "
                        "ORDER BY requested_at DESC",
                        company_id,
                    )
                return [dict(r) for r in rows]
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            return await run_sync(
                lambda: inventory_store.get_requisitions(company_id, status)
            )

    # ── writes ────────────────────────────────────────────────────

    async def add_item(self, data: dict) -> Optional[str]:
        """Insert a new inventory item; invalidates cache."""
        cid = data.get("company_id", "default")
        item_id = data.get("item_id") or str(uuid.uuid4())[:8].upper()
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(cid) as conn:
                await conn.execute(
                    """INSERT INTO inventory_items
                       (item_id, company_id, name, sku, category, description,
                        unit_of_measure, unit_cost, quantity_on_hand, reorder_point,
                        reorder_quantity, location, status, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                       ON CONFLICT (item_id, company_id) DO NOTHING""",
                    item_id, cid,
                    data.get("name", ""),
                    data.get("sku", ""),
                    data.get("category", ""),
                    data.get("description", ""),
                    data.get("unit_of_measure", "pcs"),
                    float(data.get("unit_cost", 0)),
                    float(data.get("quantity_on_hand", 0)),
                    float(data.get("reorder_point", 0)),
                    float(data.get("reorder_quantity", 0)),
                    data.get("location", ""),
                    data.get("status", "active"),
                    datetime.utcnow().isoformat(),
                )
            _invalidate(cid)
            return item_id
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            item_id = await run_sync(lambda: inventory_store.add_item(data))
            if item_id:
                _invalidate(cid)
            return item_id

    async def update_item(self, item_id: str, data: dict,
                           company_id: str = "default") -> bool:
        """Update an existing item; invalidates cache."""
        cid = company_id or data.get("company_id", "default")
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(cid) as conn:
                await conn.execute(
                    """UPDATE inventory_items SET
                       name=$1, sku=$2, category=$3, description=$4,
                       unit_of_measure=$5, unit_cost=$6, quantity_on_hand=$7,
                       reorder_point=$8, reorder_quantity=$9, location=$10,
                       status=$11
                       WHERE item_id=$12 AND company_id=$13""",
                    data.get("name", ""),
                    data.get("sku", ""),
                    data.get("category", ""),
                    data.get("description", ""),
                    data.get("unit_of_measure", "pcs"),
                    float(data.get("unit_cost", 0)),
                    float(data.get("quantity_on_hand", 0)),
                    float(data.get("reorder_point", 0)),
                    float(data.get("reorder_quantity", 0)),
                    data.get("location", ""),
                    data.get("status", "active"),
                    item_id, cid,
                )
            _invalidate(cid)
            return True
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            ok = await run_sync(lambda: inventory_store.update_item(item_id, data, cid))
            if ok:
                _invalidate(cid)
            return ok

    async def delete_item(self, item_id: str, company_id: str = "default") -> bool:
        """Soft-delete an item (sets status=deleted); invalidates cache."""
        try:
            from async_db import get_async_tenant_conn
            async with get_async_tenant_conn(company_id) as conn:
                await conn.execute(
                    "UPDATE inventory_items SET status='deleted' "
                    "WHERE item_id=$1 AND company_id=$2",
                    item_id, company_id,
                )
            _invalidate(company_id)
            return True
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            ok = await run_sync(lambda: inventory_store.delete_item(item_id, company_id))
            if ok:
                _invalidate(company_id)
            return ok

    async def record_movement(self, data: dict) -> bool:
        """Record a stock movement and update quantity atomically; invalidates cache."""
        cid = data.get("company_id", "default")
        qty_delta = float(data.get("quantity", 0))
        if data.get("movement_type") in ("out", "issue", "allocation"):
            qty_delta = -qty_delta
        try:
            from async_db import get_async_tenant_transaction
            async with get_async_tenant_transaction(cid) as conn:
                await conn.execute(
                    """INSERT INTO inventory_movements
                       (company_id, item_id, movement_type, quantity,
                        reference, notes, moved_by, moved_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    cid,
                    data.get("item_id", ""),
                    data.get("movement_type", "in"),
                    float(data.get("quantity", 0)),
                    data.get("reference", ""),
                    data.get("notes", ""),
                    data.get("moved_by", ""),
                    datetime.utcnow().isoformat(),
                )
                await conn.execute(
                    "UPDATE inventory_items SET quantity_on_hand = quantity_on_hand + $1 "
                    "WHERE item_id=$2 AND company_id=$3",
                    qty_delta, data.get("item_id", ""), cid,
                )
            _invalidate(cid)
            return True
        except Exception:
            from db import run_sync
            from inventory_data_store import inventory_store
            ok = await run_sync(lambda: inventory_store.record_movement(data))
            if ok:
                _invalidate(cid)
            return ok

    async def generate_sku(self, category: str = "", name: str = "") -> str:
        prefix = (category[:3] if category else "ITM").upper()
        suffix = uuid.uuid4().hex[:6].upper()
        return f"{prefix}-{suffix}"


# ── singleton ────────────────────────────────────────────────────────
inventory_service = InventoryService()
