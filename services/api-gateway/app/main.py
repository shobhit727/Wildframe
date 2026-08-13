"""Main FastAPI application for Api Gateway Service."""

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from wildframe_observability.wire import wire_observability

from app.api.gateway_routes import router as gateway_router
from app.core.settings import settings
from app.middleware import AuthenticationMiddleware, RateLimiter
logger = logging.getLogger(__name__)

# Global middleware instances
auth_middleware = None
rate_limiter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifespan."""
    global auth_middleware, rate_limiter
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    auth_middleware = AuthenticationMiddleware(settings.JWT_SECRET_KEY)
    redis_client = await redis.from_url(settings.REDIS_URL)
    app.state.redis_client = redis_client
    rate_limiter = RateLimiter(redis_client)

    logger.info("API Gateway started successfully")

    # Apply the header-redaction + log-injection filter to whatever logger
    # handlers the observability SDK (or basicConfig) installed. Must run
    # after wire_observability in create_app; idempotent so the lifespan
    # invocation covers handlers added later by uvicorn.
    from app.middleware import install_header_redaction

    install_header_redaction()

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
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
    # body carries no connection strings or credentials.
    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "healthy",
            "service": "api-gateway",
            "version": settings.SERVICE_VERSION,
        }

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

    return app


app = create_app()
