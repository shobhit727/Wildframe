import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.api.search_routes import close_es_client, es_client, router as search_router
from app.core.database import DatabaseManager
from app.core.events import start_event_subscriber, stop_event_subscriber
from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository
from app.services import SearchService

logger = logging.getLogger(__name__)


async def _warm_search_index() -> None:
    """Create the ES index and backfill from content-service, tolerantly."""
    try:
        async with DatabaseManager.session_factory() as session:  # type: ignore[misc]
            service = SearchService(
                es_client(), SearchQueryRepository(session), SearchIndexRepository(session)
            )
            await service.ensure_index()
            await service.reindex_catalog()
    except Exception:
        logger.warning("Elasticsearch warm-up failed; run POST /api/v1/search/reindex to retry")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Bounded retention on DLQ topics (#553) — best-effort, never blocks.
    # Incremental index sync: content.published/deleted/unpublished (#96).
    from app.core.event_consumer import run_content_sync_consumer
    from app.api.search_routes import es_client

    consumer_task = asyncio.create_task(run_content_sync_consumer(es_client()))

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

    # Warm the Elasticsearch index with the published catalog (tolerant).
    await _warm_search_index()

    # Consume content.deleted / content.unpublished so removed content
    # disappears from search without a manual reindex (#227).
    await start_event_subscriber()

    logger.info("All startup checks passed")

    yield

    consumer_task.cancel()

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await stop_event_subscriber()
    await DatabaseManager.close()
    await close_es_client()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Search Service",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        """Liveness probe: cheap, no external dependencies.

        Kubernetes should use this to detect dead processes.
        """
        return {
            "status": "ok",
            "service": "search",
            "version": settings.SERVICE_VERSION,
        }

    @app.get("/ready")
    async def ready() -> Response:
        """Readiness probe: verifies database and Elasticsearch connectivity.

        Returns 200 when healthy, 503 when degraded.
        """
        db_ok = await DatabaseManager.health_check()
        es_ok = False
        try:
            # Bounded ES ping to avoid hanging the probe.
            es_ok = bool(await asyncio.wait_for(es_client().ping(), timeout=3.0))
        except Exception:
            es_ok = False

        status_code = 200 if (db_ok and es_ok) else 503
        payload = {
            "status": "ready" if (db_ok and es_ok) else "not_ready",
            "service": "search",
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else "unavailable",
            "elasticsearch": "ok" if es_ok else "unavailable",
        }
        return Response(
            content=json.dumps(payload),
            status_code=status_code,
            media_type="application/json",
        )

    app.include_router(search_router)

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
