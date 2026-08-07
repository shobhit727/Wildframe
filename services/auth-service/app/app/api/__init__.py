"""API routes initialization."""

from app.api.routes import router as routes_router
from fastapi import APIRouter

router = APIRouter()

router.include_router(routes_router)
