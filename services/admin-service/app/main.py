import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.api.routes.admin import router as admin_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)

# Graceful shutdown state (#426)
_shutdown_event: asyncio.Event | None = None
_in_flight_requests = 0
_in_flight_lock = asyncio.Lock()
_MAX_DRAIN_SECONDS = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _shutdown_event
    # Startup
    _shutdown_event = asyncio.Event()
    app.state.shutting_down = False
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    await DatabaseManager.init()
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.warning("Database health check failed on startup")
    else:
        logger.info("Database connection established")

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

    await DatabaseManager.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    # Disable docs in production (#468)
    app = FastAPI(
        title="Admin Service",
        description="Netflix-like platform admin and moderation service",
        version=settings.SERVICE_VERSION,
        lifespan=lifespan,
        docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
        redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
        openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
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

    app.include_router(admin_router)

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

    # Status-only health endpoint (#628)
    @app.get("/health")
    async def health_check():
        """Liveness only, no dependency topology."""
        return {"status": "ok"}

    # Readiness endpoint with DB + Redis checks (#124)
    @app.get("/ready")
    async def readiness_check():
        checks: dict[str, str] = {}
        overall = "ready"

        db_healthy = await DatabaseManager.health_check()
        checks["database"] = "ok" if db_healthy else "down"
        if not db_healthy:
            overall = "not_ready"

        if settings.REDIS_URL:
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
            "service": "admin-service",
            "version": settings.SERVICE_VERSION,
            "checks": checks,
        }
        if overall != "ready":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=payload,
            )
        return payload

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
