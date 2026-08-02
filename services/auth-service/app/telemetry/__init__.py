import re

"""OpenTelemetry tracing setup for distributed tracing."""

import logging

from app.core.settings import settings
from opentelemetry import trace
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)


def setup_tracing() -> None:
    """Initialize OpenTelemetry tracing with Jaeger exporter."""
    if not settings.JAEGER_ENABLED:
        logger.info("Jaeger tracing disabled")
        return

    try:
        # Create Jaeger exporter
        jaeger_exporter = JaegerExporter(
            agent_host_name=settings.JAEGER_AGENT_HOST,
            agent_port=settings.JAEGER_AGENT_PORT,
        )

        # Create trace provider
        trace_provider = TracerProvider()
        trace_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))

        # Set global trace provider
        trace.set_tracer_provider(trace_provider)

        # Instrument libraries
        FastAPIInstrumentor.instrument()
        SQLAlchemyInstrumentor().instrument()
        RedisInstrumentor().instrument()

        logger.info(
            f"Jaeger tracing initialized "
            f"({settings.JAEGER_AGENT_HOST}:{settings.JAEGER_AGENT_PORT})"
        )

    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to setup Jaeger tracing: {e}")
