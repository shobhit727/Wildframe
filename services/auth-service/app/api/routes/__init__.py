"""API endpoints for Auth Service."""

from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.privacy import router as privacy_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(privacy_router)
