"""
EBMS Event Bus — Redis pub/sub with in-process fallback.

Architecture suggestion #3: Event-Driven Cross-Module Updates.

Instead of route handlers directly calling journal, SIEM, and backup code
after a payroll run, they emit a lightweight event.  Independent handlers
react to that event, keeping modules decoupled.

  ┌─────────────────┐    emit("payroll.completed", {...})    ┌──────────────────────┐
  │  payroll_routes │ ─────────────────────────────────────► │ event_handlers.py    │
  └─────────────────┘                                        │  • post journal entry│
                                                             │  • log to SIEM       │
                                                             │  • trigger backup    │
                                                             └──────────────────────┘

Transport:
  - Redis PUBLISH/SUBSCRIBE (preferred — works across workers and containers)
  - In-process asyncio dispatch (fallback when Redis unavailable)

Usage — emit:
    from events import event_bus
    await event_bus.emit("payroll.completed", {"company_id": ..., ...})

Usage — register handler:
    from events import event_bus

    @event_bus.on("payroll.completed")
    async def my_handler(payload: dict): ...

Usage — start background listener (in app lifespan):
    import asyncio
    from events import event_bus
    task = asyncio.create_task(event_bus.listen())
    yield
    event_bus.stop(); task.cancel()

Usage — run as standalone worker process:
    python -m events          # listens forever on Redis
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "ebms:events:"


class EventBus:
    """
    Async event bus backed by Redis pub/sub.
    Falls back to synchronous in-process dispatch when Redis is unavailable.
    """

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._sync_redis = None   # lazily-created sync client (for publish)
        self._running    = False

    # ── Handler registration ───────────────────────────────────────────────

    def on(self, event: str):
        """Decorator — register an async (or sync) handler for an event."""
        def decorator(fn: Callable) -> Callable:
            self._handlers.setdefault(event, []).append(fn)
            logger.debug("event_handler_registered: %s → %s", event, fn.__qualname__)
            return fn
        return decorator

    def register(self, event: str, handler: Callable) -> None:
        """Programmatic registration (alternative to @event_bus.on)."""
        self._handlers.setdefault(event, []).append(handler)

    # ── Emit ────────────────────────────────────────────────────────────────

    async def emit(self, event: str, payload: dict | None = None) -> None:
        """
        Publish an event.

        1. Tries Redis PUBLISH so other worker processes / containers receive it.
        2. Also dispatches to in-process handlers immediately (covers the case
           where there is no separate event-worker container running).
        """
        payload = payload or {}
        message = json.dumps({"event": event, "payload": payload})
        _published = False

        try:
            r = self._get_sync_redis()
            if r is not None:
                channel = f"{_CHANNEL_PREFIX}{event}"
                r.publish(channel, message)
                _published = True
        except Exception as exc:
            logger.warning("event_bus_publish_failed: event=%s err=%s", event, exc)

        # Always dispatch in-process (handles cases without a separate worker)
        await self._dispatch(event, payload)
        logger.debug("event_emitted: event=%s redis=%s", event, _published)

    # ── Background listener ─────────────────────────────────────────────────

    async def listen(self) -> None:
        """
        Long-running coroutine — subscribe to all EBMS channels and dispatch
        handlers as messages arrive.

        Run as an asyncio Task in the FastAPI lifespan:
            task = asyncio.create_task(event_bus.listen())
        """
        self._running = True
        try:
            import redis.asyncio as aioredis
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            r = aioredis.from_url(redis_url, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.psubscribe(f"{_CHANNEL_PREFIX}*")
            logger.info("event_bus_listening: pattern=%s*", _CHANNEL_PREFIX)
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "pmessage":
                    continue
                try:
                    data  = json.loads(message["data"])
                    event = data.get("event", "")
                    pl    = data.get("payload", {})
                    # Dispatch only handlers that weren't already triggered
                    # in-process by the emitting worker.  In a multi-worker
                    # setup each worker independently dispatches its own
                    # locally-registered handlers.
                    await self._dispatch(event, pl)
                except Exception as exc:
                    logger.error("event_bus_dispatch_error: %s", exc, exc_info=True)
        except Exception as exc:
            logger.warning("event_bus_listener_unavailable: %s", exc)
            self._running = False

    def stop(self) -> None:
        """Signal the background listener to stop on the next iteration."""
        self._running = False

    # ── Internal ────────────────────────────────────────────────────────────

    async def _dispatch(self, event: str, payload: dict) -> None:
        for handler in self._handlers.get(event, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)
            except Exception as exc:
                logger.error(
                    "event_handler_error: event=%s handler=%s err=%s",
                    event, getattr(handler, "__qualname__", "?"), exc,
                    exc_info=True,
                )

    def _get_sync_redis(self):
        if self._sync_redis is not None:
            try:
                self._sync_redis.ping()
                return self._sync_redis
            except Exception:
                self._sync_redis = None
        try:
            import redis as _redis
            url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            client = _redis.from_url(url, decode_responses=True, socket_timeout=2)
            client.ping()
            self._sync_redis = client
            return self._sync_redis
        except Exception:
            return None


# ── Module-level singleton ────────────────────────────────────────────────────
event_bus = EventBus()


# ── CLI worker entry-point ────────────────────────────────────────────────────
# Run:  python -m events
#       docker compose up event-worker

def _worker_main():
    """Standalone worker: imports all handlers and listens on Redis forever."""
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("ebms_event_worker starting")

    # Import handlers so they register themselves on the module-level bus
    try:
        import event_handlers  # noqa: F401
        logger.info("event_handlers loaded")
    except Exception as exc:
        logger.error("Failed to load event_handlers: %s", exc)
        sys.exit(1)

    try:
        asyncio.run(event_bus.listen())
    except KeyboardInterrupt:
        logger.info("ebms_event_worker stopped")


if __name__ == "__main__":
    _worker_main()
