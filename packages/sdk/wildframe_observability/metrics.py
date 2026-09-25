"""Prometheus metrics middleware for Wildframe services.

Exposes these metrics (all prefixed with ``http_``):
  - ``http_requests_total``      — Counter, labelled by method/endpoint/status/service
  - ``http_request_duration_seconds`` — Histogram, labelled by method/endpoint/service
  - ``http_active_requests``     — Gauge, labelled by service

Usage::

    from wildframe_observability import MetricsMiddleware

    app.add_middleware(MetricsMiddleware, service_name="billing")

    # Add metrics endpoint:
    @app.get("/metrics")
    async def metrics():
        from prometheus_client import generate_latest
        from fastapi.responses import Response
        return Response(content=generate_latest(), media_type="text/plain")
"""

from __future__ import annotations

import time
from typing import Callable

from prometheus_client import Counter, Histogram, Gauge
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Metric definitions (shared across all services in-process).
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "service"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "service"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ACTIVE_REQUESTS = Gauge(
    "http_active_requests",
    "Currently active HTTP requests",
    ["service"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Collect Prometheus metrics for every HTTP request.

    Increments REQUEST_COUNT, observes REQUEST_DURATION, and tracks
    ACTIVE_REQUESTS. Labels include method, endpoint path, status code,
    and service name.
    """

    def __init__(self, app, service_name: str = "wildframe") -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        method = request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"} else "OTHER"
        path = request.url.path

        # Skip metrics for the /metrics endpoint itself to avoid recursion.
        if path == "/metrics":
            return await call_next(request)  # type: ignore[no-any-return]

        ACTIVE_REQUESTS.labels(service=self.service_name).inc()
        start = time.monotonic()

        try:
            response = await call_next(request)
        finally:
            duration = time.monotonic() - start
            ACTIVE_REQUESTS.labels(service=self.service_name).dec()

            # Routing populates the template only after dispatch. Unmatched paths
            # share one label, never a client-controlled URL segment.
            endpoint = getattr(request.scope.get("route"), "path", "unmatched")

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status_code=response.status_code if "response" in dir() else 500,
                service=self.service_name,
            ).inc()

            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
                service=self.service_name,
            ).observe(duration)

        return response  # type: ignore[no-any-return]

