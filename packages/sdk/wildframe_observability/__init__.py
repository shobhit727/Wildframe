"""Wildframe Observability SDK — structured logging, metrics, and correlation.

Provides:
  - Structured JSON logging with request_id and correlation_id propagation
  - FastAPI middleware for correlation ID injection and request logging
  - Prometheus metrics middleware (request count, duration, active gauge)
  - Standardized health check response builder
"""

from wildframe_observability.logging import (
    setup_logging,
    get_logger,
    get_correlation_id,
    get_request_id,
    REDACT_FIELDS,
)
from wildframe_observability.middleware import (
    CorrelationMiddleware,
    RequestLoggingMiddleware,
)
from wildframe_observability.metrics import MetricsMiddleware
from wildframe_observability.health import create_health_response

__all__ = [
    "setup_logging",
    "get_logger",
    "get_correlation_id",
    "get_request_id",
    "REDACT_FIELDS",
    "CorrelationMiddleware",
    "RequestLoggingMiddleware",
    "MetricsMiddleware",
    "create_health_response",
]
