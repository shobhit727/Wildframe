"""
Main FastAPI application for Auth Service.
Entry point with lifespan management, middleware, and route configuration.
"""

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from app.core.database import DatabaseManager
from app.core.logging import set_correlation_id, set_request_id, setup_logging
from app.core.settings import settings
from app.schemas import ErrorResponse, HealthCheckResponse
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Manage FastAPI application lifespan.

    Handles startup and shutdown events:
    - Initialize logging
    - Setup observability (tracing, metrics)
    - Perform health checks
    - Graceful shutdown

    Args:
        app: FastAPI application instance

    Yields:
        Control back to FastAPI
    """
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Initialize logging
    setup_logging()

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    logger.info("All startup checks passed")

    # Consume user.moderated events so suspensions/bans are enforced at the
    # login boundary (admin-service publishes; this applies is_active).
    import asyncio

    from app.core.event_consumer import run_user_moderation_consumer

    consumer_task = asyncio.create_task(
        run_user_moderation_consumer(DatabaseManager.get_session_factory)
    )

    yield

    consumer_task.cancel()
    logger.info(f"Shutting down {settings.SERVICE_NAME}")

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")

    # Close database connections
    await DatabaseManager.close()

    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application.

    Returns:
        FastAPI: Configured application instance
    """
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Authentication and authorization service",
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Trusted host middleware — wildcard outside production so LAN-IP access
    # (e.g. containerized browsers hitting http://<host-ip>:8080) works in
    # dev; production pins the real hostnames via TRUSTED_HOSTS.
    trusted = (
        ["*"]
        if settings.ENVIRONMENT != "production"
        else getattr(settings, "TRUSTED_HOSTS", ["localhost", "*.wildframe.com"])
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted,
    )

    # Custom middleware for request tracing
    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        """Add request context for tracing and logging.

        Args:
            request: Incoming request
            call_next: Next middleware/endpoint

        Returns:
            Response with added headers
        """
        # Extract or generate correlation ID
        correlation_id = request.headers.get(
            "X-Correlation-ID",
            set_correlation_id(),
        )
        set_correlation_id(correlation_id)

        # Generate request ID
        request_id = set_request_id()

        # Add to response headers
        request.state.correlation_id = correlation_id
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        return response

    def _serializable_errors(errors: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitize Pydantic error ctx so JSON responses never carry non-serializable objects."""
        cleaned: list[dict[str, Any]] = []
        for error in errors:
            error = dict(error)
            ctx = error.get("ctx")
            if isinstance(ctx, dict):
                error["ctx"] = {
                    key: (
                        str(value)
                        if not isinstance(value, (str, int, float, bool, type(None)))
                        else value
                    )
                    for key, value in ctx.items()
                }
            cleaned.append(error)
        return cleaned

    # Global exception handler for validation errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors.

        Args:
            request: The request
            exc: The validation error

        Returns:
            JSONResponse: Error response
        """
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="VALIDATION_ERROR",
                message="Request validation failed",
                details={"errors": _serializable_errors(exc.errors())},
            ).model_dump(),
        )

    # Health check endpoint
    @app.get("/health", tags=["Health"], response_model=HealthCheckResponse)
    async def health_check() -> HealthCheckResponse:
        """Health check endpoint.

        Returns:
            HealthCheckResponse: Service health status
        """
        db_health = await DatabaseManager.health_check()

        return HealthCheckResponse(
            status="healthy" if db_health else "unhealthy",
            service=settings.SERVICE_NAME,
            version=settings.SERVICE_VERSION,
            timestamp=datetime.now(UTC),
            checks={
                "database": {
                    "status": "healthy" if db_health else "unhealthy",
                },
            },
        )

    # Ready check endpoint (for Kubernetes)
    @app.get("/ready", tags=["Health"])
    async def readiness_check():
        """Readiness check endpoint for Kubernetes.

        Returns:
            dict: Readiness status
        """
        db_health = await DatabaseManager.health_check()

        if not db_health:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not ready", "reason": "database unhealthy"},
            )

        return {"status": "ready"}

    # API root endpoint
    @app.get("/", tags=["Info"])
    async def root():
        """API root information.

        Returns:
            dict: Service information
        """
        return {
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "environment": settings.ENVIRONMENT,
            "docs": "/docs",
            "openapi": "/openapi.json",
        }

    # Include API routes
    from app.api.routes import router as api_router

    app.include_router(api_router, prefix="/api/v1")

    logger.info(f"FastAPI app created: {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).

    # Request body size cap (#517): reject oversized payloads before parsing.
    MAX_BODY_SIZE = 1048576  # bytes

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_SIZE:
                    return JSONResponse(
                        content={"detail": "Request body too large"},
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except ValueError:
                pass
        return await call_next(request)

    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    # Opaque 500 handler (#557) — never leak exception internals.
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status_code": 500, "message": "Internal server error"},
        )

    return app


# Create application instance
app = create_app()
