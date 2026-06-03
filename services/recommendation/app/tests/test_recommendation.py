"""Recommendation service tests."""
import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import RecommendationService

@pytest.mark.asyncio
async def test_get_recommendations(db: AsyncSession):
    """Test getting recommendations."""
    user_id = uuid4()
    service = RecommendationService(None, None)
    
    recs = await service.get_recommendations(user_id, 20)
    assert isinstance(recs, list)

@pytest.mark.asyncio
async def test_update_preferences(db: AsyncSession):
    """Test updating user preferences."""
    user_id = uuid4()
    genres = ["Action", "Thriller"]
    service = RecommendationService(None, None)
    
    await service.update_preferences(user_id, genres)
