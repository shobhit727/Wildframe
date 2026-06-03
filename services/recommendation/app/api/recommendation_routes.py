"""Recommendation service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.repositories import UserPreferencesRepository, RecommendationRepository
from app.services import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

async def get_rec_service(db: AsyncSession = Depends(get_db_session)) -> RecommendationService:
    return RecommendationService(UserPreferencesRepository(db), RecommendationRepository(db))

@router.get("/for-user/{user_id}")
async def get_recommendations(user_id: UUID, limit: int = 20, 
                             service: RecommendationService = Depends(get_rec_service)):
    """Get personalized recommendations."""
    recommendations = await service.get_recommendations(user_id, limit)
    return {"recommendations": recommendations, "total": len(recommendations)}

@router.put("/preferences/{user_id}")
async def update_preferences(user_id: UUID, liked_genres: list = Body(None),
                            service: RecommendationService = Depends(get_rec_service)):
    """Update user preferences."""
    await service.update_preferences(user_id, liked_genres)
    return {"status": "updated"}
