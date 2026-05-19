"""
FastAPI application factory and configuration.
Sets up middleware, exception handlers, health checks, and lifespan events.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uuid
import logging

from app.core.settings import settings
from app.core.database import db_manager
from app.core.logging import setup_logging, set_correlation_id, set_request_id
from app.api.routes import router
from app.schemas import ErrorResponse, HealthCheckResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    logger.info(f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    setup_logging()
    
    # Database health check
    db_healthy = await db_manager.health_check()
    if not db_healthy:
        logger.warning("Database health check failed at startup")
    else:
        logger.info("Database connection established")
    
    yield
    
    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    await db_manager.close()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Content management service for streaming platform",
        lifespan=lifespan,
    )
    
    # Middleware
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Trusted host middleware
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
    
    # Request tracing middleware
    @app.middleware("http")
    async def request_tracing_middleware(request: Request, call_next):
        """Add correlation ID and request ID to all requests."""
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        set_correlation_id(correlation_id)
        set_request_id(request_id)
        
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        
        return response
    
    # Exception handlers
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                status_code=422,
                message="Request validation failed",
                detail=str(exc)
            ).model_dump()
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle general exceptions."""
        logger.error(f"Unhandled exception: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                status_code=500,
                message="Internal server error",
                detail=str(exc) if settings.DEBUG else None
            ).model_dump()
        )
    
    # Routes
    
    @app.get("/", tags=["root"])
    async def root():
        """Root endpoint."""
        return {
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "status": "running"
        }
    
    @app.get("/health", tags=["health"], response_model=HealthCheckResponse)
    async def health_check():
        """Health check endpoint."""
        db_healthy = await db_manager.health_check()
        return HealthCheckResponse(
            status="healthy" if db_healthy else "degraded",
            version=settings.SERVICE_VERSION,
            database="connected" if db_healthy else "disconnected"
        )
    
    @app.get("/ready", tags=["health"])
    async def readiness_check():
        """Kubernetes readiness probe."""
        db_healthy = await db_manager.health_check()
        if not db_healthy:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"ready": False, "reason": "database_unavailable"}
            )
        return {"ready": True}
    
    # Include API routes
    app.include_router(router)
    
    return app


# Create app instance
app = create_app()
