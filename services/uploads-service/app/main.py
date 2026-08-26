import asyncio
import logging
import time
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.api.uploads_routes import router as uploads_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)

# Graceful shutdown state (#426)
_shutdown_event: asyncio.Event | None = None
_in_flight_requests = 0
_in_flight_lock: asyncio.Lock | None = None
_fallback_in_flight_lock: asyncio.Lock | None = None


def _fallback_lock() -> asyncio.Lock:
    """Return a process-wide lock when the lifespan has not run yet."""
    global _fallback_in_flight_lock
    if _fallback_in_flight_lock is None:
        _fallback_in_flight_lock = asyncio.Lock()
    return _fallback_in_flight_lock


_MAX_DRAIN_SECONDS = 30


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
    global _shutdown_event, _in_flight_lock
    # Startup
    _shutdown_event = asyncio.Event()
    _in_flight_lock = asyncio.Lock()
    app.state.shutting_down = False

    # Bounded retention on DLQ topics (#553) — best-effort, never blocks.
    if settings.EVENT_PUBLISHER == "kafka":
        from wildframe_events.dlq_retention import apply_dlq_retention

        asyncio.create_task(
            apply_dlq_retention(settings.KAFKA_BOOTSTRAP_SERVERS, settings.SERVICE_NAME)
        )
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

    # Shutdown (#426): stop accepting new requests, drain in-flight, close DB
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

    workers_task.cancel()
    try:
        await workers_task
    except asyncio.CancelledError:
        pass
    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    # Disable docs in production (#468)
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Uploads Service",
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
        global _in_flight_requests
        if getattr(app.state, "shutting_down", False):
            return JSONResponse(
                content={"detail": "Service shutting down"},
                status_code=503,
                headers={"Retry-After": str(_MAX_DRAIN_SECONDS)},
            )
        lock = _in_flight_lock or _fallback_lock()
        async with lock:
            _in_flight_requests += 1
        try:
            response = await call_next(request)
            return response
        finally:
            async with lock:
                _in_flight_requests -= 1

    # Status-only health endpoint (#628)
    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint — liveness only, no dependency topology."""
        db_ok = await DatabaseManager.health_check()
        return {"status": "healthy" if db_ok else "degraded"}

    # Readiness endpoint with DB + Redis checks (#124)
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
            "service": "uploads",
            "version": settings.SERVICE_VERSION,
            "checks": checks,
        }
        if overall != "ready":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=payload,
            )
        return payload

    app.include_router(uploads_router)

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).

    # Request body size cap (#517): reject oversized payloads before parsing.
    MAX_BODY_SIZE = 6291456  # bytes

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

    # Gate /metrics behind admin token (#469)
    from fastapi import Depends, Header, HTTPException
    from wildframe_observability.logging import get_correlation_id

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
        from fastapi import Response

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
