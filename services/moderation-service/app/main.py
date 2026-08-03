import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability

from app.api.moderation_routes import router as moderation_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

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
        description="Moderation Service",
        lifespan=lifespan,
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

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
