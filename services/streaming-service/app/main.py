"""Streaming service main application."""
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from wildframe_observability.wire import wire_observability
from app.core.settings import settings
from app.core.database import get_db, DatabaseManager
from app.api.streaming_routes import router as streaming_router
import logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Streaming Service",
    description="Netflix-like streaming service",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(streaming_router)

wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)


@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint. Pings DB with text('SELECT 1')."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "unhealthy"
    return {"status": "healthy", "service": "streaming", "database": db_status}


@app.on_event("startup")
async def startup_event():
    """Application startup."""
    logger.info("Streaming service starting up")
    manager = DatabaseManager()
    if not await manager.health_check():
        logger.warning("Database health check failed on startup")


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown."""
    logger.info("Streaming service shutting down")
    manager = DatabaseManager()
    await manager.close()


if __name__ == "__main__":
    import uvicorn
    # Bind the port declared in settings so ``python -m app`` matches the
    # Docker ``CMD``/gunicorn (both honor ``settings.SERVER_PORT``).
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
