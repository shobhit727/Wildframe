from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.routes.admin import router as admin_router
from app.core.database import DatabaseManager
from app.core.settings import settings
from wildframe_observability.wire import wire_observability


@asynccontextmanager
async def lifespan(app: FastAPI):
    await DatabaseManager.init()
    await DatabaseManager.health_check()
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
        return {"status": "ok", "service": "admin-service"}

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8006)
