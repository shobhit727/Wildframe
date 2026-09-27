"""API endpoints for Auth Service."""

from fastapi import APIRouter

from app.api.routes.age import router as age_router
from app.api.routes.auth import router as auth_router
from app.api.routes.dsar_verify import router as dsar_verify_router
from app.api.routes.privacy import router as privacy_router

# Keep the top-level composition to the two canonical route groups. Privacy
# owns the compliance-oriented subrouters without changing their prefixes.
privacy_router.include_router(age_router)
privacy_router.include_router(dsar_verify_router)

router = APIRouter()
router.include_router(auth_router)
router.include_router(privacy_router)
