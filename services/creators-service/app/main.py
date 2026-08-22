import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.api.creators_routes import admin_router as creators_admin_router
from app.api.creators_routes import router as creators_router
from app.core.database import DatabaseManager, get_db
from app.core.settings import settings
from app.repositories import InboundEventRepository
from app.services import CreatorService

logger = logging.getLogger(__name__)

# Graceful shutdown state (#426)
_shutdown_event: asyncio.Event | None = None
_in_flight_requests = 0
_in_flight_lock = asyncio.Lock()
_MAX_DRAIN_SECONDS = 30
# Inbound event consumer
_inbound_consumer_task: asyncio.Task | None = None


async def _drain_inbound_events_worker() -> None:
    """Background worker to process inbound creator.suspended events."""
    # Import settings here to get poll interval
    from app.core.settings import settings

    poll_interval = getattr(settings, "INBOUND_EVENT_POLL_INTERVAL_SECONDS", 30)
    logger.info("Inbound event consumer worker started, poll interval=%ds", poll_interval)

    while True:
        try:
            await asyncio.sleep(poll_interval)
            # Use a new session for each iteration
            async for db in get_db():
                inbound_repo = InboundEventRepository(db)
                service = CreatorService(
                    acct_repo=None,  # type: ignore[arg-type]
                    floor_repo=None,  # type: ignore[arg-type]
                    pool_repo=None,  # type: ignore[arg-type]
                    milestone_repo=None,  # type: ignore[arg-type]
                    ledger_repo=None,  # type: ignore[arg-type]
                    inbound_repo=inbound_repo,
                )
                # We need the other repos - create them
                from app.repositories import (
                    CreatorAccountRepository,
                    CreatorPoolBalanceRepository,
                    EffectiveFloorRepository,
                    MilestoneRepository,
                    PayoutLedgerRepository,
                )

                service.acct_repo = CreatorAccountRepository(db)
                service.floor_repo = EffectiveFloorRepository(db)
                service.pool_repo = CreatorPoolBalanceRepository(db)
                service.milestone_repo = MilestoneRepository(db)
                service.ledger_repo = PayoutLedgerRepository(db)

                await service.drain_inbound_events(limit=100)
                await db.commit()
        except asyncio.CancelledError:
            logger.info("Inbound event consumer worker cancelled")
            raise
        except Exception:  # noqa: BLE001 - worker must survive transient errors
            logger.exception("inbound event drain iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    global _shutdown_event, _inbound_consumer_task
    # Startup
    _shutdown_event = asyncio.Event()
    app.state.shutting_down = False
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Verify database connectivity
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.error("Database health check failed")
        raise RuntimeError("Database is not healthy on startup")

    # Start inbound event consumer worker
    _inbound_consumer_task = asyncio.create_task(_drain_inbound_events_worker())
    logger.info("Inbound event consumer started")

    logger.info("All startup checks passed")

    yield

    # Shutdown (#426): stop accepting new requests, drain in-flight, close DB
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    app.state.shutting_down = True
    _shutdown_event.set()

    # Stop inbound event consumer
    if _inbound_consumer_task is not None:
        _inbound_consumer_task.cancel()
        try:
            await _inbound_consumer_task
        except asyncio.CancelledError:
            pass
        logger.info("Inbound event consumer stopped")

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

    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    # Disable docs in production (#468)
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Creators Service",
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
        return {"status": "ok"}

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
            "service": "creators",
            "version": settings.SERVICE_VERSION,
            "checks": checks,
        }
        if overall != "ready":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=payload,
            )
        return payload

    app.include_router(creators_router)
    app.include_router(creators_admin_router)

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
