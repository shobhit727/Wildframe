"""Analytics service tests."""
import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import AnalyticsService

@pytest.mark.asyncio
async def test_log_event(db: AsyncSession):
    """Test logging event."""
    user_id = uuid4()
    content_id = uuid4()
    service = AnalyticsService(None)
    
    await service.log_event(user_id, "play_started", {"quality": "1080p"}, content_id)

@pytest.mark.asyncio
async def test_get_user_events(db: AsyncSession):
    """Test getting user events."""
    user_id = uuid4()
    service = AnalyticsService(None)
    
    events = await service.get_user_events(user_id, 100)
    assert isinstance(events, list)
