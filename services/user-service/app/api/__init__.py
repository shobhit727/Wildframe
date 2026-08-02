"""API routes initialization."""

from fastapi import APIRouter

router = APIRouter()

# Import route modules
from app.api.routes import router as routes_router

# Include endpoint routers
router.include_router(routes_router)
