"""OpenTelemetry tracing setup for distributed tracing.

Spans are exported over OTLP. The Jaeger exporter was discontinued upstream at
1.21.0 and cannot be used with the SDK release this service requires; Jaeger has
accepted OTLP natively since 1.35, so the same Jaeger instance still receives the
spans. See issue #978.
"""

import logging

from app.core.settings import settings

logger = logging.getLogger(__name__)


def setup_tracing() -> None:
    """Initialize OpenTelemetry tracing, exporting spans over OTLP.

    Imports are lazy so that importing this module never fails. They used to sit
    at module scope, which meant a single unavailable exporter broke the import
    of the whole module rather than just disabling tracing.
    """
    if not settings.JAEGER_ENABLED:
        logger.info("Tracing disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # The exporter reads OTEL_EXPORTER_OTLP_ENDPOINT from the environment.
        span_exporter = OTLPSpanExporter()

        # Create trace provider
        trace_provider = TracerProvider()
        trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))

        # Set global trace provider
        trace.set_tracer_provider(trace_provider)

        # Instrument libraries
        FastAPIInstrumentor.instrument()
        SQLAlchemyInstrumentor().instrument()
        RedisInstrumentor().instrument()

        logger.info(
            "Tracing initialized, exporting spans over OTLP to %s",
            settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        )

    except Exception:  # noqa: BLE001
        # Tracing is best-effort and must not stop the service from serving
        # traffic. Logged with a traceback so a total tracing outage is visible
        # instead of silent.
        logger.exception(
            "Failed to setup tracing; the service will run but will not emit spans. "
            "Check that OTEL_EXPORTER_OTLP_ENDPOINT is set and reachable."
        )
