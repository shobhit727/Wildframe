import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability

from app.api.uploads_routes import router as uploads_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)


async def _background_workers() -> None:
    """Drain the transactional outbox and reap expired upload sessions."""
    from app.repositories import UploadChunkRepository
    from app.services import UploadService

    last_reap = 0.0
    while True:
        try:
            assert DatabaseManager.session_factory is not None
            async with DatabaseManager.session_factory() as session:
                service = UploadService(UploadChunkRepository(session))
                await service.drain_outbox()
                if time.monotonic() - last_reap >= settings.REAPER_INTERVAL_SECONDS:
                    await service.reap_expired()
                    last_reap = time.monotonic()
        except Exception:  # noqa: BLE001 - workers must survive transient errors
            logger.exception("background worker iteration failed")
        await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_SECONDS)


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

    workers_task = asyncio.create_task(_background_workers())
    yield

    # Shutdown
    workers_task.cancel()
    try:
        await workers_task
    except asyncio.CancelledError:
        pass
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Uploads Service",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        db_ok = await DatabaseManager.health_check()
        return {
            "status": "healthy" if db_ok else "degraded",
            "service": "uploads",
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else "unavailable",
        }

    app.include_router(uploads_router)

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
