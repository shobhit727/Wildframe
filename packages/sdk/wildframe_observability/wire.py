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

import logging
import os

from fastapi import FastAPI
from fastapi.responses import Response

from wildframe_observability.logging import setup_logging as obs_setup_logging
from wildframe_observability.middleware import (
    CorrelationMiddleware,
    RequestLoggingMiddleware,
)
from wildframe_observability.metrics import MetricsMiddleware

_logger = logging.getLogger(__name__)


def _log_tracing_failure() -> None:
    """Report a tracing initialisation failure without raising.

    Traces are best-effort, so this never propagates. The exception is logged at
    ERROR with a traceback because the failure is otherwise invisible: the
    service keeps answering requests while emitting no spans at all.
    """
    _logger.exception(
        "OpenTelemetry tracing failed to initialise; the service will run but "
        "will not emit spans. Traces are exported over OTLP, so check that "
        "OTEL_EXPORTER_OTLP_ENDPOINT is set and reachable."
    )


def _setup_tracing(app: FastAPI, service_name: str) -> None:
    """Optionally init OpenTelemetry tracing, exporting spans over OTLP.

    Gated on the JAEGER_ENABLED env var, whose name is retained because compose
    and Helm already set it, so enabling tracing stays a one-variable change.
    Imports stay lazy so a service without the OTel extras still boots.

    Traces go to OTEL_EXPORTER_OTLP_ENDPOINT rather than the Jaeger agent port.
    The Jaeger exporter was discontinued upstream at 1.21.0 and cannot be used
    with the SDK release this package requires; Jaeger has accepted OTLP natively
    since 1.35, so the same Jaeger instance still receives the spans.
    """
    if os.getenv("JAEGER_ENABLED", "false").lower() != "true":
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore[import-not-found]
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[attr-defined]
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[attr-defined]

        # The exporter reads OTEL_EXPORTER_OTLP_ENDPOINT itself, so no endpoint
        # plumbing is duplicated here.
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    except Exception:  # noqa: BLE001 - observability must never crash the app
        # Deliberately still non-fatal: a telemetry failure must not stop a
        # service from serving traffic. But it is no longer silent, because a
        # bare `pass` here is what let a total tracing outage -- the Jaeger
        # exporter failing to import against the current SDK -- go unnoticed in
        # every service at once.
        _log_tracing_failure()


def wire_observability(
    app: FastAPI,
    service_name: str,
    log_level: str = "INFO",
    *,
    register_metrics: bool = True,
) -> None:
    """Add all observability middleware and endpoints to a FastAPI app.

    Call this inside create_app() after the FastAPI instance is created.
    It adds three middleware layers and a /metrics endpoint.

    Args:
        app: The FastAPI application instance.
        service_name: Used as a label on all Prometheus metrics and log entries.
        log_level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        register_metrics: Disable when the service owns an authenticated scrape route.
    """
    # Set up structured JSON logging.
    obs_setup_logging(service_name=service_name, log_level=log_level)

    # Distributed tracing (Jaeger), gated on JAEGER_ENABLED env var.
    _setup_tracing(app, service_name=service_name)

    # Add middleware (order matters: last added = first executed).
    # CorrelationMiddleware runs first (outermost) to set contextvars
    # before request logging and metrics see the request.
    app.add_middleware(MetricsMiddleware, service_name=service_name)
    app.add_middleware(RequestLoggingMiddleware, service_name=service_name)
    app.add_middleware(CorrelationMiddleware)

    # Add Prometheus scrape endpoint.
    if not register_metrics:
        return

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        from prometheus_client import generate_latest

        return Response(content=generate_latest(), media_type="text/plain")
