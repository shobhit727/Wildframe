"""FastAPI middleware for correlation IDs and request logging.

CorrelationMiddleware:
  - Reads or generates X-Request-ID and X-Correlation-ID headers
  - Stores them in contextvars so all log lines in a request are correlated
  - Adds them to the response headers

RequestLoggingMiddleware:
  - Logs every request with method, path, status_code, duration_ms
  - Uses structured JSON logging via the shared setup_logging
"""
from __future__ import annotations

import time
import uuid
import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from wildframe_observability.logging import (
    request_id_var,
    correlation_id_var,
    set_request_id,
    set_correlation_id,
)

logger = logging.getLogger(__name__)

# Header names (lowercase for case-insensitive matching).
REQUEST_ID_HEADER = "x-request-id"
CORRELATION_ID_HEADER = "x-correlation-id"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Inject and propagate X-Request-ID and X-Correlation-ID.

    If the client sends these headers, we honour them (trust boundary:
    the API gateway should strip/replace them at the edge). If not, we
    generate fresh UUIDs.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Read or generate IDs.
        req_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        corr_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())

        # Store in contextvars for downstream log correlation.
        set_request_id(req_id)
        set_correlation_id(corr_id)

        # Process the request.
        response = await call_next(request)

        # Propagate IDs in response headers.
        response.headers[REQUEST_ID_HEADER] = req_id
        response.headers[CORRELATION_ID_HEADER] = corr_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with structured fields: method, path, status, duration.

    Usage::

        app.add_middleware(RequestLoggingMiddleware, service_name="billing")
    """

    def __init__(self, app, service_name: str = "wildframe") -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        method = request.method
        path = request.url.path

        # Skip logging for health/metrics edges to reduce noise.
        if path in ("/health", "/metrics", "/favicon.ico"):
            return await call_next(request)

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "request completed",
            extra={
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "service_name": self.service_name,
            },
        )
        return response
