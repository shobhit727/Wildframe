"""Wire observability into any FastAPI app with a single function call.

Usage in any service's main.py::

    from wildframe_observability.wire import wire_observability

    # Inside create_app(), after app = FastAPI(...):
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

This adds:
  - CorrelationMiddleware (X-Request-ID, X-Correlation-ID propagation)
  - RequestLoggingMiddleware (structured request logging)
  - MetricsMiddleware (Prometheus counters + histograms)
  - /metrics endpoint (Prometheus scrape)
  - Structured JSON logging setup
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import Response

from wildframe_observability.logging import setup_logging as obs_setup_logging
from wildframe_observability.middleware import (
    CorrelationMiddleware,
    RequestLoggingMiddleware,
)
from wildframe_observability.metrics import MetricsMiddleware


def wire_observability(
    app: FastAPI,
    service_name: str,
    log_level: str = "INFO",
) -> None:
    """Add all observability middleware and endpoints to a FastAPI app.

    Call this inside create_app() after the FastAPI instance is created.
    It adds three middleware layers and a /metrics endpoint.

    Args:
        app: The FastAPI application instance.
        service_name: Used as a label on all Prometheus metrics and log entries.
        log_level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    # Set up structured JSON logging.
    obs_setup_logging(service_name=service_name, log_level=log_level)

    # Add middleware (order matters: last added = first executed).
    # CorrelationMiddleware runs first (outermost) to set contextvars
    # before request logging and metrics see the request.
    app.add_middleware(MetricsMiddleware, service_name=service_name)
    app.add_middleware(RequestLoggingMiddleware, service_name=service_name)
    app.add_middleware(CorrelationMiddleware)

    # Add Prometheus scrape endpoint.
    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        from prometheus_client import generate_latest
        return Response(content=generate_latest(), media_type="text/plain")
