"""Main FastAPI application for the Moderation Service.

Handles content review queue, flag decisions, escalation, and
creator strike management. Part of the Wildframe Sustenance Engine platform.
"""

import logging

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability

from app.api.moderation_routes import router as moderation_router
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.core.settings import settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the Moderation Service FastAPI application."""
    setup_logging()

    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description=(
            "Moderation Service — content review queue, flag decisions, "
            "escalation, and creator strikes."
        ),
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        db_ok = await DatabaseManager.health_check()
        return {
            "status": "healthy" if db_ok else "degraded",
            "service": "moderation",
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else "unavailable",
        }

    app.include_router(moderation_router)

    @app.on_event("startup")
    async def startup() -> None:
        """Initialize database connection pool on startup."""
        await DatabaseManager.init()
        logger.info("Moderation service started")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        """Close database connections on shutdown."""
        await DatabaseManager.close()
        logger.info("Moderation service stopped")

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
