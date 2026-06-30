"""Main FastAPI application for Creators Service."""
import logging
from fastapi import FastAPI
from app.core.settings import settings
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.api.routes.creators import router, admin_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Creators Service — onboarding, KYC/Stripe Connect, floor, pool, milestones/tranches, payouts ledger"
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "healthy", "service": "creators", "version": settings.SERVICE_VERSION}

    app.include_router(router, prefix="/api/v1/creators")
    app.include_router(admin_router, prefix="/api/v1/admin/creators")
    return app


app = create_app()
