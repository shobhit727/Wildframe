"""Streaming service main application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.settings import settings
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

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "streaming"}

@app.on_event("startup")
async def startup_event():
    """Application startup."""
    logger.info("Streaming service starting up")

@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown."""
    logger.info("Streaming service shutting down")

if __name__ == "__main__":
    import uvicorn
    # Bind the port declared in settings so ``python -m app`` matches the
    # Docker ``CMD``/gunicorn (both honor ``settings.SERVER_PORT``).
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
