"""
Prometheus-compatible metrics endpoint.

Uses only the standard library (no prometheus_client required) to avoid
adding a hard dependency.  Exposes a /metrics route that Prometheus or
Grafana Agent can scrape.

Collected metrics
-----------------
- http_requests_total          (counter, labels: method, path_pattern, status)
- http_request_duration_ms     (histogram buckets, labels: method, path_pattern)
- db_pool_connections_used     (gauge, labels: pool_type)
- app_info                     (info gauge)

Usage in app.py
---------------
    from metrics import MetricsMiddleware, metrics_router

    # register the middleware BEFORE other middleware so it sees all requests
    app.middleware("http")(MetricsMiddleware(app))
    app.include_router(metrics_router)
"""
from __future__ import annotations

import os
import time
import threading
from collections import defaultdict
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from starlette.requests import Request
from starlette.responses import Response

# ── Storage (in-process, per-worker) ────────────────────────────────
_lock = threading.Lock()

_request_count:   dict[tuple, int]       = defaultdict(int)
_duration_sum_ms: dict[tuple, float]     = defaultdict(float)
_duration_count:  dict[tuple, int]       = defaultdict(int)
_duration_buckets: dict[tuple, list[int]] = defaultdict(lambda: [0] * 9)

_BUCKET_BOUNDARIES = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000]  # ms


def _path_pattern(path: str) -> str:
    """
    Collapse dynamic path segments to labels so cardinality stays low.
    e.g. /auth/users/abc-123/update → /auth/users/{id}/update
    """
    import re
    path = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27}", "/{uuid}", path)
    path = re.sub(r"/\d+", "/{id}", path)
    return path


def record_request(method: str, path: str, status: int, duration_ms: float) -> None:
    pattern = _path_pattern(path)
    key = (method, pattern, str(status))
    dk  = (method, pattern)
    with _lock:
        _request_count[key] += 1
        _duration_sum_ms[dk] += duration_ms
        _duration_count[dk]  += 1
        buckets = _duration_buckets[dk]
        for i, boundary in enumerate(_BUCKET_BOUNDARIES):
            if duration_ms <= boundary:
                buckets[i] += 1


def _format_label(k, v):
    return f'{k}="{v}"'


def _render_labels(labels: dict) -> str:
    pairs = ",".join(_format_label(k, v) for k, v in labels.items())
    return "{" + pairs + "}" if pairs else ""


def render_metrics() -> str:
    lines = []

    # app_info
    version = os.environ.get("APP_VERSION", "unknown")
    env     = os.environ.get("ENVIRONMENT", "production")
    lines.append("# HELP app_info Static application metadata.")
    lines.append("# TYPE app_info gauge")
    lines.append(f'app_info{{version="{version}",env="{env}"}} 1')

    with _lock:
        # http_requests_total
        lines.append("# HELP http_requests_total Total HTTP requests by method/path/status.")
        lines.append("# TYPE http_requests_total counter")
        for (method, pattern, status), count in sorted(_request_count.items()):
            lbl = _render_labels({"method": method, "path": pattern, "status": status})
            lines.append(f"http_requests_total{lbl} {count}")

        # http_request_duration_ms histogram
        lines.append("# HELP http_request_duration_ms HTTP request latency in milliseconds.")
        lines.append("# TYPE http_request_duration_ms histogram")
        for (method, pattern), buckets in sorted(_duration_buckets.items()):
            lbl_base = {"method": method, "path": pattern}
            cumulative = 0
            for i, boundary in enumerate(_BUCKET_BOUNDARIES):
                cumulative += buckets[i]
                lbl = _render_labels({**lbl_base, "le": str(boundary)})
                lines.append(f"http_request_duration_ms_bucket{lbl} {cumulative}")
            total_count = _duration_count.get((method, pattern), 0)
            total_sum   = _duration_sum_ms.get((method, pattern), 0.0)
            inf_lbl = _render_labels({**lbl_base, "le": "+Inf"})
            lines.append(f"http_request_duration_ms_bucket{inf_lbl} {total_count}")
            lbl = _render_labels(lbl_base)
            lines.append(f"http_request_duration_ms_sum{lbl} {total_sum:.3f}")
            lines.append(f"http_request_duration_ms_count{lbl} {total_count}")

    # db_pool_connections_used gauge
    lines.append("# HELP db_pool_connections_used Approximate used DB connections.")
    lines.append("# TYPE db_pool_connections_used gauge")
    try:
        from db import _get_pool
        pool = _get_pool()
        used = pool.maxconn - len(getattr(pool, "_pool", []))
        lines.append(f'db_pool_connections_used{{pool="psycopg2"}} {used}')
    except Exception:
        lines.append('db_pool_connections_used{pool="psycopg2"} -1')

    try:
        import asyncio
        from async_db import _pool as _asyncpg_pool
        if _asyncpg_pool is not None:
            used = _asyncpg_pool.get_size() - _asyncpg_pool.get_idle_size()
            lines.append(f'db_pool_connections_used{{pool="asyncpg"}} {used}')
        else:
            lines.append('db_pool_connections_used{pool="asyncpg"} 0')
    except Exception:
        lines.append('db_pool_connections_used{pool="asyncpg"} -1')

    lines.append("")  # trailing newline required by Prometheus
    return "\n".join(lines)


# ── FastAPI router ────────────────────────────────────────────────────

metrics_router = APIRouter(tags=["metrics"])


@metrics_router.get(
    "/metrics",
    name="prometheus_metrics",
    include_in_schema=False,
    response_class=PlainTextResponse,
)
async def metrics_endpoint():
    """
    Prometheus-compatible text-format metrics endpoint.

    Scraped by Prometheus / Grafana Agent.
    Add to prometheus.yml:
        scrape_configs:
          - job_name: 'erp'
            static_configs:
              - targets: ['your-host:8000']
    """
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


# ── Middleware ────────────────────────────────────────────────────────

class MetricsMiddleware:
    """ASGI middleware that records per-request latency and status."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path   = scope.get("path", "")
        method = scope.get("method", "")

        # Skip metrics endpoint itself to avoid self-reporting noise
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        t0 = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            record_request(method, path, status_code, elapsed_ms)
