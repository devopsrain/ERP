"""
Bridge: Redis/in-process event bus → outbound webhook deliveries.

The bus (events.py) registers handlers per exact event name and has no
wildcard subscription, so this module subscribes every catalogued event
(plus a few existing internal ones) and forwards them to
``webhook_data_store.emit``. Extra names can be added via the env var
``WEBHOOK_BUS_EVENTS="a.b,c.d"``.

Modules that do not go through the bus can call ``emit`` directly::

    from webhook_data_store import emit
    emit(company_id, "invoice.created", {...})

Duplicate dispatch (the emitting worker's own Redis listener re-delivering
the message) is absorbed by the dedupe key inside ``emit``.
"""
from __future__ import annotations

import logging
import os

from events import event_bus
from webhook_data_store import STANDARD_EVENT_NAMES, emit

logger = logging.getLogger(__name__)

INTERNAL_BUS_EVENTS = ["payroll.completed", "account.created"]

_extra = [e.strip() for e in os.environ.get("WEBHOOK_BUS_EVENTS", "").replace(";", ",").split(",") if e.strip()]
BUS_EVENTS = list(dict.fromkeys(STANDARD_EVENT_NAMES + INTERNAL_BUS_EVENTS + _extra))


def _make_handler(event_name: str):
    def _handler(payload: dict) -> None:
        payload = dict(payload or {})
        company_id = payload.get("company_id") or "default"
        # Sync handler: emit() only writes rows and hands the HTTP work to a thread,
        # so the bus's dispatch loop is never blocked on a slow receiver.
        emit(company_id, event_name, payload)
    _handler.__qualname__ = f"webhook_forward[{event_name}]"
    return _handler


_registered = False


def register() -> int:
    """Idempotent — register a forwarding handler per bus event. Returns count."""
    global _registered
    if _registered:
        return len(BUS_EVENTS)
    for name in BUS_EVENTS:
        event_bus.register(name, _make_handler(name))
    _registered = True
    logger.info("webhook bus bridge registered for %d events", len(BUS_EVENTS))
    return len(BUS_EVENTS)


register()
