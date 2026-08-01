import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from wildframe_observability.wire import wire_observability

from app.api.routes.admin import router as admin_router
from app.core.database import DatabaseManager
from app.core.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await DatabaseManager.init()
    db_healthy = await DatabaseManager.health_check()
    if not db_healthy:
        logger.warning("Database health check failed on startup")
    else:
        logger.info("Database connection established")
    yield
    await DatabaseManager.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Admin Service",
        description="Netflix-like platform admin and moderation service",
        version=settings.SERVICE_VERSION,
        lifespan=lifespan,
    )

    app.include_router(admin_router)

    wire_observability(app, service_name=settings.SERVICE_NAME, log_level=settings.LOG_LEVEL)

    @app.get("/health")
    async def health_check():
        db_healthy = await DatabaseManager.health_check()
        return {
            "status": "healthy" if db_healthy else "unhealthy",
            "service": "admin-service",
            "database": "connected" if db_healthy else "disconnected",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
