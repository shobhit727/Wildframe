"""Main FastAPI application for Api Gateway Service."""

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from wildframe_observability.wire import wire_observability

from app.api.gateway_routes import router as gateway_router
# from app.core.privacy_proxy import resolve_jurisdiction
from app.core.settings import settings
from app.middleware import (
    AuthenticationMiddleware,
    BodyLimitMiddleware,
    HeaderSanitizerMiddleware,
    RateLimiter,
    install_header_redaction,
    shared_client_lifespan,
)

logger = logging.getLogger(__name__)

# Global middleware instances
auth_middleware = None
rate_limiter = None

# Graceful shutdown state (#426)
_in_flight_requests = 0
_in_flight_lock = asyncio.Lock()
_MAX_DRAIN_SECONDS = 30  # bounded drain timeout


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    global auth_middleware, rate_limiter, _in_flight_requests
    # Startup
    _in_flight_requests = 0
    app.state.shutting_down = False
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    auth_middleware = AuthenticationMiddleware(settings.JWT_SECRET_KEY)
    redis_client = await redis.from_url(settings.REDIS_URL)
    app.state.redis_client = redis_client
    rate_limiter = RateLimiter(redis_client)

    # Start shared AsyncClient lifespan (#123)
    client_cm = shared_client_lifespan()
    await client_cm.__aenter__()
    app.state._shared_client_cm = client_cm

    logger.info("API Gateway started successfully")

    # Apply the header-redaction + log-injection filter to whatever logger
    # handlers the observability SDK (or basicConfig) installed. Must run
    # after wire_observability in create_app; idempotent so the lifespan
    # invocation covers handlers added later by uvicorn.
    install_header_redaction()

    yield

    # Shutdown (#426): stop accepting new requests, drain in-flight, close client
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    app.state.shutting_down = True

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

    # Close shared client
    if hasattr(app.state, "_shared_client_cm"):
        await app.state._shared_client_cm.__aexit__(None, None, None)

    await redis_client.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="API Gateway Service - Request routing, authentication, rate limiting",
        lifespan=lifespan,
    )

    # Middleware order (last added = first executed):
    # 1. BodyLimitMiddleware (enforces limits before routing)
    # 2. HeaderSanitizerMiddleware (strips/rewrites headers)
    # 3. CORSMiddleware
    # 4. Observability (added by wire_observability)

    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(HeaderSanitizerMiddleware)

    # CORS middleware — credentials require explicit origins, never "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Liveness probe — process/event loop is alive and serving HTTP. Does NOT
    # verify downstream dependencies; use /ready for that. Keeping liveness
    # independent of Redis prevents Kubernetes from restarting a gateway that
    # can still route while a dependency is briefly unavailable. Response
    # body carries no connection strings or credentials (#628).
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    # Readiness probe — required dependencies are reachable and initialized.
    # Verifies Redis with a bounded timeout so a hung dependency cannot stall
    # the readiness signal. Returns HTTP 503 when any dependency is down so
    # load balancers stop routing traffic. No connection strings or creds.
    @app.get("/ready")
    async def ready() -> dict:
        checks: dict[str, str] = {}
        overall = "ready"
        redis = app.state.redis_client
        if redis is None:
            checks["redis"] = "down"
            overall = "not_ready"
        else:
            try:
                await asyncio.wait_for(redis.ping(), timeout=2.0)
                checks["redis"] = "ok"
            except asyncio.TimeoutError:
                checks["redis"] = "timeout"
                overall = "not_ready"
            except Exception as e:  # noqa: BLE001
                checks["redis"] = "down"
                overall = "not_ready"
                logger.error("Redis readiness check failed: %s", e)
        payload = {
            "status": overall,
            "service": "api-gateway",
            "version": settings.SERVICE_VERSION,
            "checks": checks,
        }
        if overall != "ready":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=payload,
            )
        return payload

    # Include gateway routes
    app.include_router(gateway_router)

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    # Middleware to track in-flight requests for graceful shutdown (#426)
    @app.middleware("http")
    async def track_in_flight(request: Request, call_next):
        global _in_flight_requests
        if getattr(app.state, "shutting_down", False):
            return Response(
                content="Service shutting down",
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
