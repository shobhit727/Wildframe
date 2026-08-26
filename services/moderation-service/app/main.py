import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.api.moderation_routes import router as moderation_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)


async def _drain_outbox_worker() -> None:
    """Publish PENDING transactional-outbox rows to the event bus."""
    from app.repositories import (
        ContentFlagRepository,
        CreatorStrikeRepository,
        ModerationDecisionRepository,
    )
    from app.services import ModerationService

    while True:
        try:
            assert DatabaseManager.session_factory is not None
            async with DatabaseManager.session_factory() as session:
                service = ModerationService(
                    flag_repo=ContentFlagRepository(session),
                    decision_repo=ModerationDecisionRepository(session),
                    strike_repo=CreatorStrikeRepository(session),
                )
                await service.drain_outbox()
        except Exception:  # noqa: BLE001 - worker must survive transient errors
            logger.exception("outbox drain iteration failed")
        await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Bounded retention on DLQ topics (#553) — best-effort, never blocks.
    if settings.EVENT_PUBLISHER == "kafka":
        from wildframe_events.dlq_retention import apply_dlq_retention

        asyncio.create_task(
            apply_dlq_retention(settings.KAFKA_BOOTSTRAP_SERVERS, settings.SERVICE_NAME)
        )

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    logger.info("All startup checks passed")

    workers_task = asyncio.create_task(_drain_outbox_worker())
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

    # Request body size cap (#517): reject oversized payloads before parsing.
    MAX_BODY_SIZE = 1048576  # bytes

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_SIZE:
                    return JSONResponse(
                        content={"detail": "Request body too large"},
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except ValueError:
                pass
        return await call_next(request)

    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    # Opaque 500 handler (#557) — never leak exception internals.
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status_code": 500, "message": "Internal server error"},
        )

    return app


app = create_app()
