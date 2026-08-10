"""Main FastAPI application for the Billing Service (Sustenance Engine core).

This service implements the economic model that makes WildFrame different:
  - AVOD/SVOD/TVOD revenue tiers with >=55% creator share
  - Living-wage floor per region
  - Creator Pool redistribution (15% of net)
  - Milestone-tranched funding with kill clauses
  - Idempotent payout ledger through Stripe Connect
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability  # type: ignore[import-untyped]

from app.api.billing_routes import router as billing_router
from app.api.webhook_routes import router as webhook_router
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.core.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    # Startup
    setup_logging()
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    await DatabaseManager.init()
    logger.info("All startup checks passed")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the Billing Service FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description=(
            "Billing Service — the Sustenance Engine core. Manages revenue "
            "tiers (AVOD/SVOD/TVOD), living-wage floors, Creator Pool "
            "redistribution, milestone-tranched funding, and payout ledger."
        ),
        lifespan=lifespan,
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
    app.include_router(webhook_router)

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
