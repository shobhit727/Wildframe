"""Recommendation service API routes."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import RecommendationRepository, UserPreferencesRepository
from app.services import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


async def get_rec_service(db: AsyncSession = Depends(get_db)) -> RecommendationService:  # noqa: B008
    return RecommendationService(UserPreferencesRepository(db), RecommendationRepository(db))


@router.get("/for-user/{user_id}")
async def get_recommendations(
    user_id: UUID,
    service: RecommendationService = Depends(get_rec_service),  # noqa: B008
    limit: int = 20,
):
    """Get personalized recommendations."""
    recommendations = await service.get_recommendations(user_id, limit)
    return {"recommendations": recommendations, "total": len(recommendations)}


@router.put("/preferences/{user_id}")
async def update_preferences(
    user_id: UUID,
    service: RecommendationService = Depends(get_rec_service),  # noqa: B008
    liked_genres: list | None = Body(None),  # noqa: B008
):
    """Update user preferences."""
    await service.update_preferences(user_id, liked_genres)
    return {"status": "updated"}
