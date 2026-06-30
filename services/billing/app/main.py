"""Main FastAPI application for the Billing Service (Sustenance Engine core).

This service implements the economic model that makes WildFrame different:
  - AVOD/SVOD/TVOD revenue tiers with >=55% creator share
  - Living-wage floor per region
  - Creator Pool redistribution (15% of net)
  - Milestone-tranched funding with kill clauses
  - Idempotent payout ledger through Stripe Connect
"""
import logging

from fastapi import FastAPI

from app.core.settings import settings
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.api.billing_routes import router as billing_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the Billing Service FastAPI application."""
    setup_logging()

    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description=(
            "Billing Service — the Sustenance Engine core. Manages revenue "
            "tiers (AVOD/SVOD/TVOD), living-wage floors, Creator Pool "
            "redistribution, milestone-tranched funding, and payout ledger."
        ),
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        db_ok = await DatabaseManager.health_check()
        return {
            "status": "healthy" if db_ok else "degraded",
            "service": "billing",
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else "unavailable",
        }

    app.include_router(billing_router)

    @app.on_event("startup")
    async def startup() -> None:
        """Initialize database connection pool on startup."""
        await DatabaseManager.init()
        logger.info("Billing service started — Sustenance Engine active")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        """Close database connections on shutdown."""
        await DatabaseManager.close()
        logger.info("Billing service stopped")

    return app


app = create_app()
