"""Search service tests."""
import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import SearchService

@pytest.mark.asyncio
async def test_search_content(db: AsyncSession):
    """Test content search."""
    user_id = uuid4()
    service = SearchService(None, None, None)
    
    results = await service.search(user_id, "action", "movie", 20)
    assert isinstance(results, list)

@pytest.mark.asyncio
async def test_search_trending():
    """Test trending search."""
    service = SearchService(None, None, None)
    results = await service.get_trending("movie", 10)
    assert isinstance(results, list)
