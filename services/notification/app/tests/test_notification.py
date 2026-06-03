"""Notification service tests."""
import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import NotificationService

@pytest.mark.asyncio
async def test_send_notification(db: AsyncSession):
    """Test sending notification."""
    user_id = uuid4()
    service = NotificationService(None)
    
    await service.send_notification(user_id, "New Content", "Check out new releases!", "in-app")

@pytest.mark.asyncio
async def test_get_unread_notifications(db: AsyncSession):
    """Test getting unread notifications."""
    user_id = uuid4()
    service = NotificationService(None)
    
    notifs = await service.get_unread_notifications(user_id)
    assert isinstance(notifs, list)
