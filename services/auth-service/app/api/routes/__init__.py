"""API endpoints for Auth Service."""

from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.privacy import router as privacy_router
from app.api.routes.age import router as age_router
from app.api.routes.dsar_verify import router as dsar_verify_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(privacy_router)
router.include_router(age_router)
router.include_router(dsar_verify_router)
