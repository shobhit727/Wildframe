"""Main FastAPI application for Notification Service."""
import logging
from fastapi import FastAPI
from app.core.settings import settings
from app.core.database import DatabaseManager
from app.core.logging import setup_logging
from app.api.notification_routes import router as notification_router
from wildframe_observability.wire import wire_observability

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description="Notification Service"
    )
    
    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "healthy", "service": "notification", "version": settings.SERVICE_VERSION}
    
    # Wire observability (structured JSON logs, correlation IDs, Prometheus metrics + /metrics).
    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    return app

app = create_app()


app.include_router(notification_router)
