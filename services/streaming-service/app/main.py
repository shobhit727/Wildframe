"""
Streaming service main application.
FastAPI app factory with lifespan management.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from wildframe_observability.wire import wire_observability

from app.api.routes import router as api_router
from app.core.database import db_manager
from app.core.settings import settings
from app.schemas import HealthCheckResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan management."""
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")

    # Database health check
    db_healthy = await db_manager.health_check()
    if not db_healthy:
        logger.warning("Database health check failed on startup")
    else:
        logger.info("Database connection established")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await db_manager.close()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="Streaming Service",
        description="Netflix-like streaming service",
        version=settings.SERVICE_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routes with /api/v1 prefix
    app.include_router(api_router)

    # Self-hosted demo HLS asset so the player plays without external streams.
    app.mount(
        "/static",
        StaticFiles(directory="app/static"),
        name="static",
    )

    # Health check endpoint
    @app.get("/health", tags=["Health"], response_model=HealthCheckResponse)
    async def health_check() -> HealthCheckResponse:
        """Health check endpoint.

        Returns:
            HealthCheckResponse: Service health status
        """
        db_health = await db_manager.health_check()
        return HealthCheckResponse(
            status="healthy" if db_health else "unhealthy",
            version=settings.SERVICE_VERSION,
            database="healthy" if db_health else "unhealthy",
            redis="healthy",
        )

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics)

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


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
