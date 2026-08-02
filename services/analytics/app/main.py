"""Main FastAPI application for Analytics Service."""

import logging

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability

from app.api.analytics_routes import router as analytics_router
from app.core.settings import settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Analytics Service",
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "healthy", "service": "analytics", "version": settings.SERVICE_VERSION}

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()


app.include_router(analytics_router)
