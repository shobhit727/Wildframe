import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from wildframe_observability.logging import get_correlation_id
from wildframe_observability.wire import wire_observability

from app.api.recommendation_routes import router as recommendation_router
from app.core.database import DatabaseManager
from app.core.events import start_event_subscriber, stop_event_subscriber
from app.core.settings import settings
from app.services import close_catalog_client

logger = logging.getLogger(__name__)

# Graceful shutdown state (#426)
_shutdown_event: asyncio.Event | None = None
_in_flight_requests = 0
_in_flight_lock: asyncio.Lock | None = None
_MAX_DRAIN_SECONDS = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    global _shutdown_event, _in_flight_lock
    # Startup
    _shutdown_event = asyncio.Event()
    _in_flight_lock = asyncio.Lock()
    app.state.shutting_down = False
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    logger.info("All startup checks passed")

    # Consume content.deleted / content.unpublished so stored
    # recommendations never reference removed or unpublished titles (#228).
    await start_event_subscriber()

    yield

    # Shutdown (#426): stop accepting new requests, drain in-flight, close clients
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    app.state.shutting_down = True
    _shutdown_event.set()

    # Wait for in-flight requests to complete (bounded)
    try:
        async with asyncio.timeout(_MAX_DRAIN_SECONDS):
            while True:
                async with _in_flight_lock:
                    if _in_flight_requests == 0:
                        break
                await asyncio.sleep(0.1)
    except asyncio.TimeoutError:
        logger.warning(
            "Shutdown drain timeout after %ds; %d requests still in flight",
            _MAX_DRAIN_SECONDS,
            _in_flight_requests,
        )

    await stop_event_subscriber()
    await close_catalog_client()
    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    # Disable docs in production (#468)
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Recommendation Service",
        lifespan=lifespan,
        docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
        redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
        openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
    )

    # CORS middleware (#68): production must not combine wildcard origins with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # In-flight request tracking for graceful shutdown (#426)
    @app.middleware("http")
    async def track_in_flight(request: Request, call_next):
        global _in_flight_requests, _in_flight_lock
        if getattr(app.state, "shutting_down", False):
            return JSONResponse(
                content={"detail": "Service shutting down"},
                status_code=503,
                headers={"Retry-After": str(_MAX_DRAIN_SECONDS)},
            )
        # Initialize lock lazily for test environments without lifespan
        if _in_flight_lock is None:
            _in_flight_lock = asyncio.Lock()
        async with _in_flight_lock:
            _in_flight_requests += 1
        try:
            response = await call_next(request)
            return response
        finally:
            async with _in_flight_lock:
                _in_flight_requests -= 1

    # Status-only health endpoint (#628)
    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint — liveness only, no dependency topology."""
        db_ok = await DatabaseManager.health_check()
        return {
            "status": "healthy" if db_ok else "degraded",
            "service": "recommendation",
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else "unavailable",
        }

    @app.get("/ready", response_model=None)
    async def ready() -> dict | JSONResponse:
        checks: dict[str, str] = {}
        overall = "ready"

        db_ok = await DatabaseManager.health_check()
        checks["database"] = "ok" if db_ok else "down"
        if not db_ok:
            overall = "not_ready"
        try:
            redis_client = await redis.from_url(settings.REDIS_URL)
            await asyncio.wait_for(redis_client.ping(), timeout=2.0)
            await redis_client.close()
            checks["redis"] = "ok"
        except Exception as e:  # noqa: BLE001
            logger.error("Redis readiness check failed: %s", e)
            checks["redis"] = "down"
            overall = "not_ready"

        payload = {
            "status": overall,
            "service": "recommendation",
            "version": settings.SERVICE_VERSION,
            "checks": checks,
        }
        if overall != "ready":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=payload,
            )
        return payload

    app.include_router(recommendation_router)

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    # Gate /metrics behind admin token (#469)
    from fastapi import Depends, Header, HTTPException

    async def require_metrics_token(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> None:
        if settings.ENVIRONMENT == "production":
            expected = (
                f"Bearer {settings.METRICS_TOKEN}"
                if hasattr(settings, "METRICS_TOKEN") and settings.METRICS_TOKEN
                else None
            )
            if expected is None or authorization != expected:
                raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/metrics", dependencies=[Depends(require_metrics_token)])
    async def gated_metrics():
        from prometheus_client import generate_latest
        from starlette.responses import Response

        return Response(content=generate_latest(), media_type="text/plain")

    # Opaque 500 handler (#557) with correlation ID (#466) — never leak exception internals.
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        corr_id = get_correlation_id()
        logger.exception("Unhandled exception (corr=%s): %s", corr_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status_code": 500,
                "message": "Internal server error",
                "correlation_id": corr_id,
            },
        )

    return app


app = create_app()
