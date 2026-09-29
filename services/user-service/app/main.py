"""Main FastAPI application for User Service."""

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.core.database import DatabaseManager
from app.core.logging import set_correlation_id, set_request_id, setup_logging
from app.core.settings import settings
from app.schemas import ErrorResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Manage FastAPI application lifespan."""
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    setup_logging()

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    logger.info("All startup checks passed")

    # Provision default profiles for freshly registered accounts
    # (auth-service publishes user.registered; this applies it).
    import asyncio

    from app.core.event_consumer import run_user_registered_consumer

    consumer_task = asyncio.create_task(
        run_user_registered_consumer(DatabaseManager.get_session_factory())
    )

    yield

    consumer_task.cancel()
    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="User profile and device management service",
        lifespan=lifespan,
        docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
        redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
        openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Trusted host middleware
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=(
            ["*"]
            if settings.ENVIRONMENT != "production"
            else ["localhost", "127.0.0.1", "*.wildframe.com"]
        ),
    )

    # Custom middleware for request tracing
    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        """Add request context for tracing and logging."""
        correlation_id = request.headers.get(
            "X-Correlation-ID",
            set_correlation_id(),
        )
        set_correlation_id(correlation_id)
        request_id = set_request_id()

        request.state.correlation_id = correlation_id
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        return response

    def _serializable_errors(errors: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitize Pydantic error entries so JSON responses never carry
        non-serializable objects.

        Pydantic puts the offending value in ``input`` and the failing
        constraint in ``ctx``. Either can be an arbitrary object -- notably
        ``input`` holds the raw request body (bytes) whenever a client sends a
        body that cannot be parsed into the expected model, e.g. a JSON body
        without ``Content-Type: application/json``. Passing ``exc.errors()``
        straight to ``JSONResponse`` made ``json.dumps`` raise inside this
        handler and turned the intended 422 into a 500.
        """
        json_safe = (str, int, float, bool, type(None))

        def coerce(value: Any) -> Any:
            return value if isinstance(value, json_safe) else str(value)

        cleaned: list[dict[str, Any]] = []
        for error in errors:
            error = dict(error)
            if "input" in error:
                error["input"] = coerce(error["input"])
            ctx = error.get("ctx")
            if isinstance(ctx, dict):
                error["ctx"] = {key: coerce(value) for key, value in ctx.items()}
            cleaned.append(error)
        return cleaned

    # Global exception handler for validation errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="VALIDATION_ERROR",
                message="Request validation failed",
                details={"errors": _serializable_errors(exc.errors())},
            ).model_dump(),
        )

    # Health check endpoint
    @app.get("/health", tags=["Health"])
    async def health_check() -> dict:
        """Health check endpoint."""
        db_health = await DatabaseManager.health_check()
        return {
            "status": "healthy" if db_health else "unhealthy",
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "timestamp": datetime.now(UTC),
        }

    # Ready check endpoint (for Kubernetes)
    @app.get("/ready", tags=["Health"])
    async def readiness_check():
        """Readiness check endpoint for Kubernetes."""
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
        """API root information."""
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

    # register_metrics=False: this service registers its own token-gated
    # /metrics below, and the SDK's public route would shadow it.
    wire_observability(
        app,
        service_name=settings.SERVICE_NAME,
        log_level=settings.LOG_LEVEL,
        register_metrics=False,
    )

    # Gate /metrics behind admin token (#469)
    from fastapi import Depends, Header, HTTPException

    async def require_metrics_token(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> None:
        if settings.ENVIRONMENT == "production":
            expected = (
                f"Bearer {settings.METRICS_TOKEN}"
                if hasattr(settings, "METRICS_TOKEN") and settings.METRICS_TOKEN
                else None
            )
            if expected is None or authorization != expected:
                raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/metrics", dependencies=[Depends(require_metrics_token)])
    async def gated_metrics():
        from prometheus_client import generate_latest
        from fastapi import Response

        return Response(content=generate_latest(), media_type="text/plain")

    # Opaque 500 handler (#557) — never leak exception internals.
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status_code": 500, "message": "Internal server error"},
        )

    return app


app = create_app()
