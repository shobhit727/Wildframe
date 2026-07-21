"""Main FastAPI application for User Service."""
from contextlib import asynccontextmanager
from datetime import datetime
import logging
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.core.settings import settings
from app.core.database import DatabaseManager
from app.core.logging import setup_logging, set_request_id, set_correlation_id
from app.schemas import ErrorResponse, HealthCheckResponse
from wildframe_observability.wire import wire_observability

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

    yield

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
        allowed_hosts=["localhost", "127.0.0.1", "*.wildframe.com"],
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

    # Global exception handler for validation errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="VALIDATION_ERROR",
                message="Request validation failed",
                details={"errors": exc.errors()},
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
            "timestamp": datetime.utcnow(),
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
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
