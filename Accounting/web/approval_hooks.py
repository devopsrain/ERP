"""
Glue between the approval engine and the modules that need approvals.

Import this module (it is imported by procurement_routes and hrm_routes) and
the built-in completion handlers register themselves:

* ``purchase_requisition`` → procurement_store.approve_pr(...)
* ``leave``                → hrm_store.update_leave_status(...)

Use :func:`request_approval` from any route after creating/submitting an
entity. It returns the approval request id, or ``None`` when no workflow
matches (caller should then treat the entity as auto-approved / keep its
legacy manual approval path).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _store():
    from approval_data_store import approval_store
    return approval_store


def request_approval(company_id: str, entity_type: str, entity_id: str, title: str,
                     amount: Any, requested_by: str, payload: Optional[Dict[str, Any]] = None,
                     currency: str = "ETB") -> Optional[str]:
    """Submit an approval request; never raises."""
    try:
        try:
            amt = float(amount or 0)
        except (TypeError, ValueError):
            amt = 0.0
        return _store().submit(company_id, entity_type, str(entity_id), title, amt,
                               requested_by, payload=payload or {}, currency=currency)
    except Exception as exc:
        logger.warning("request_approval(%s/%s) failed: %s", entity_type, entity_id, exc)
        return None


def approval_status(entity_type: str, entity_id: str, company_id: str = None) -> Optional[dict]:
    try:
        return _store().status_of(entity_type, str(entity_id), company_id)
    except Exception as exc:
        logger.debug("approval_status(%s/%s): %s", entity_type, entity_id, exc)
        return None


# ── built-in completion handlers ─────────────────────────────────

def _sync_purchase_requisition(req: dict, status: str, actor: str, comment: str) -> None:
    if req.get("entity_type") != "purchase_requisition":
        return
    from procurement_data_store import procurement_store
    procurement_store.approve_pr(req["entity_id"], req["company_id"], actor,
                                 approved=(status == "approved"),
                                 note=comment or f"Approval workflow: {status}")


def _sync_leave(req: dict, status: str, actor: str, comment: str) -> None:
    if req.get("entity_type") != "leave":
        return
    from hrm_data_store import hrm_store
    hrm_store.update_leave_status(leave_id=req["entity_id"], status=status,
                                  approver_id=actor, approver_note=comment or "",
                                  company_id=req["company_id"])


def _sync_capa(req: dict, status: str, actor: str, comment: str) -> None:
    """Quality CAPA approved through the engine -> mark approved in quality_capa."""
    if req.get("entity_type") != "capa" or status != "approved":
        return
    from quality_data_store import quality_store
    quality_store.approve_capa(req["entity_id"], req["company_id"], actor)


# Sales orders: commercial_data_store registers its own on_decided handler
# for entity_type "sales_order"; production orders / raw-material plans are
# handled by manufacturing_data_store. Nothing to add here.


def _emit_webhook(req: dict, status: str, actor: str, comment: str) -> None:
    """Fan the decision out to tenant webhooks (approval.decided)."""
    try:
        from webhook_data_store import emit
    except Exception:
        return
    emit(req["company_id"], "approval.decided", {
        "request_id": req.get("id"), "entity_type": req.get("entity_type"),
        "entity_id": req.get("entity_id"), "title": req.get("title"),
        "amount": float(req.get("amount") or 0), "status": status,
        "actor": actor, "comment": comment,
    })


def _install() -> None:
    try:
        from approval_data_store import register_on_decided
    except Exception as exc:  # approval module absent — nothing to hook
        logger.debug("approval hooks not installed: %s", exc)
        return
    for fn in (_sync_purchase_requisition, _sync_leave, _sync_capa, _emit_webhook):
        register_on_decided(fn)


_install()

__all__ = ["request_approval", "approval_status"]
