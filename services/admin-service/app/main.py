import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from wildframe_observability.wire import wire_observability

from app.api.routes.admin import router as admin_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)


# Graceful shutdown state (#426)
_shutdown_event: asyncio.Event | None = None
_in_flight_requests = 0
_in_flight_lock: asyncio.Lock | None = None
_MAX_DRAIN_SECONDS = 30

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _shutdown_event, _in_flight_lock
    import asyncio
    # Startup
    _shutdown_event = asyncio.Event()
    _in_flight_lock = asyncio.Lock()
    _in_flight_requests = 0
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
    docs_url = None if settings.ENVIRONMENT == "production" else "/docs"
    redoc_url = None if settings.ENVIRONMENT == "production" else "/redoc"
    openapi_url = None if settings.ENVIRONMENT == "production" else "/openapi.json"

    app = FastAPI(
        title="Admin Service",
        description="Netflix-like platform admin and moderation service",
        version=settings.SERVICE_VERSION,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    # CORS middleware with validator (#68)
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
        if app.state.shutting_down:
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

    # Opaque 500 handler (#557)
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status_code": 500, "message": "Internal server error"},
        )

    # Status-only health endpoint (#628)
    @app.get("/health")
    async def health_check():
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

        # Check Redis if configured
        if settings.REDIS_URL:
            try:
                redis_client = await redis.from_url(settings.REDIS_URL)
                await asyncio.wait_for(redis_client.ping(), timeout=2.0)
                await redis_client.close()
                checks["redis"] = "ok"
            except Exception as e:
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
