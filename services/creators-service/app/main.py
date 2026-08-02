"""Main FastAPI application for Creators Service."""

import logging

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability

from app.api.routes.creators import admin_router, router
from app.core.settings import settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Creators Service — onboarding, KYC/Stripe Connect, floor, pool, milestones/tranches, payouts ledger",
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "healthy", "service": "creators", "version": settings.SERVICE_VERSION}

    app.include_router(router, prefix="/api/v1/creators")
    app.include_router(admin_router, prefix="/api/v1/admin/creators")
    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
