"""Main FastAPI application for the Media Pipeline Service."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from sqlalchemy import text

from app.core.settings import settings
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.api.media_pipeline_routes import router as pipeline_router
from app.api.media_pipeline_routes import legacy_router as media_router
from wildframe_observability.wire import wire_observability

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan management."""
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")

    setup_logging()

    # Verify DB connectivity on startup
    await DatabaseManager.init()
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.warning("Database health check failed on startup")
    else:
        logger.info("Database connection established")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await DatabaseManager.close()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description=(
            "Media Pipeline Service — the animation upload → encode → package "
            "pipeline orchestrator."
        ),
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint. Verifies DB connectivity."""
        db_ok = False
        db_error: str | None = None
        try:
            if DatabaseManager.engine is None:
                await DatabaseManager.init()
            async with DatabaseManager.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception as exc:
            db_error = str(exc)
        return {
            "status": "healthy" if db_ok else "degraded",
            "service": "media-pipeline",
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else f"error: {db_error}",
        }

    @app.get("/ready")
    async def ready() -> dict:
        """Readiness probe (Kubernetes)."""
        db_ok = await DatabaseManager.health_check()
        if not db_ok:
            from fastapi.responses import JSONResponse
            from fastapi import status
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"ready": False, "reason": "database_unavailable"},
            )
        return {"ready": True}

    # Canonical pipeline routes (/pipeline) plus the legacy /media routes kept
    # for backward compatibility.
    app.include_router(pipeline_router)
    app.include_router(media_router)
    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()