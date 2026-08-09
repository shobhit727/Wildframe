"""Recommendation service API routes."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import RecommendationRepository, UserPreferencesRepository
from app.services import RecommendationService

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


async def get_rec_service(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> RecommendationService:
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
    body: dict | list | None = Body(None),  # noqa: B008
):
    """Update user preferences.

    Body may be a raw list of liked genre slugs (legacy) or an object with
    ``liked_genres`` / ``disliked_genres`` arrays. Recommendations are
    regenerated afterwards.
    """
    if isinstance(body, list):
        liked_genres = body or None
        disliked_genres = None
    elif isinstance(body, dict):
        liked_genres = body.get("liked_genres")
        disliked_genres = body.get("disliked_genres")
    else:
        liked_genres = disliked_genres = None
    await service.update_preferences(user_id, liked_genres, disliked_genres)
    return {"status": "updated"}
