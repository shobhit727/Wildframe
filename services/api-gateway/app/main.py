"""Main FastAPI application for Api Gateway Service."""

import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI
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
    rate_limiter = RateLimiter(redis_client)

    logger.info("API Gateway started successfully")

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

    # Health check
    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "healthy", "service": "api-gateway", "version": settings.SERVICE_VERSION}

    # Include gateway routes
    app.include_router(gateway_router)

    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app


app = create_app()
