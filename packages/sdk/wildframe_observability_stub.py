"""Stub for wildframe_observability - satisfies imports without real SDK."""
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import logging
import contextvars
import time

# Context vars for correlation IDs
_correlation_id = contextvars.ContextVar("correlation_id", default=None)
_request_id = contextvars.ContextVar("request_id", default=None)

def setup_logging(service_name: str = "unknown", log_level: str = "INFO") -> None:
    """Stub: configure JSON logging."""
    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
    logging.info(f"Observability stub: setup_logging for {service_name}")

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        corr = request.headers.get("X-Correlation-ID")
        req_id = request.headers.get("X-Request-ID")
        if corr: _correlation_id.set(corr)
        if req_id: _request_id.set(req_id)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = _correlation_id.get() or ""
        response.headers["X-Request-ID"] = _request_id.get() or ""
        return response

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        return response

class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str = "unknown"):
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        return await call_next(request)

def create_health_response(**kwargs):
    return {"status": "healthy", **kwargs}

def wire_observability(
    app: FastAPI,
    service_name: str,
    log_level: str = "INFO",
) -> None:
    """Stub: add no-op observability middleware and /metrics endpoint."""
    setup_logging(service_name=service_name, log_level=log_level)
    app.add_middleware(MetricsMiddleware, service_name=service_name)
    app.add_middleware(RequestLoggingMiddleware, service_name=service_name)
    app.add_middleware(CorrelationMiddleware)

    @app.get("/metrics")
    async def metrics() -> Response:
        from prometheus_client import generate_latest
        return Response(content=generate_latest(), media_type="text/plain")