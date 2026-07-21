"""Main FastAPI application for the Media Pipeline Service."""
import logging
from fastapi import FastAPI
from sqlalchemy import text
from app.core.settings import settings
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.api.media_pipeline_routes import router as pipeline_router
from app.api.media_pipeline_routes import legacy_router as media_router
from wildframe_observability.wire import wire_observability

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    setup_logging()
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description=(
            "Media Pipeline Service — the animation upload → encode → package "
            "pipeline orchestrator."
        ),
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

    # Canonical pipeline routes (/pipeline) plus the legacy /media routes kept
    # for backward compatibility.
    app.include_router(pipeline_router)
    app.include_router(media_router)
    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
