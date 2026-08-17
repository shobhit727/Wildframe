import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from wildframe_observability.wire import wire_observability

from app.api.search_routes import close_es_client, es_client, router as search_router
from app.core.database import DatabaseManager
from app.core.settings import settings
from app.repositories import SearchIndexRepository, SearchQueryRepository
from app.services import SearchService

logger = logging.getLogger(__name__)


async def _warm_search_index() -> None:
    """Create the ES index and backfill from content-service, tolerantly."""
    try:
        async for session in DatabaseManager.session_factory():  # type: ignore[misc,union-attr]
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

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    # Warm the Elasticsearch index with the published catalog (tolerant).
    await _warm_search_index()

    logger.info("All startup checks passed")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
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
    async def ready() -> dict:
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
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
