"""
Management Overview Routes — chronological activity feed across all modules.

Requires manager privilege or higher (feed spans HR, finance and contracts).
"""
import logging
from collections import OrderedDict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request

import activity_feed_store
from db import run_sync
from deps import current_company, require_auth, template_context
from template_engine import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/overview", tags=["overview"])

_DEFAULT_LIMIT = 60
_MAX_LIMIT = 200


@router.get("/", name="overview_feed")
async def overview_feed(request: Request, user=Depends(require_auth("manager"))):
    cid = current_company(request)

    module = request.query_params.get("module") or None
    valid_keys = {m["key"] for m in activity_feed_store.MODULES}
    if module not in valid_keys:
        module = None

    try:
        limit = int(request.query_params.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    items = await run_sync(activity_feed_store.get_feed, cid, limit, module)

    # Group by calendar day (items arrive newest-first, so days stay ordered)
    groups: "OrderedDict[date, list]" = OrderedDict()
    for it in items:
        groups.setdefault(it["ts"].date(), []).append(it)

    today = date.today()
    ctx = template_context(request)
    ctx.update(
        groups=groups,
        modules=activity_feed_store.MODULES,
        active_module=module or "",
        limit=limit,
        total_items=len(items),
        today=today,
        yesterday=today - timedelta(days=1),
    )
    return templates.TemplateResponse("overview/feed.html", ctx)
