"""Tests for Notification Service."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_health_check():
    """Test health check endpoint."""
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in ("healthy", "degraded")


@pytest.mark.asyncio
async def test_get_unread_delegates_to_repository():
    """Unread notifications must come from the repository rather than a stub."""
    from app.services import NotificationService

    repository = AsyncMock()
    user_id = uuid4()
    expected = [object(), object()]
    repository.get_unread.return_value = expected

    service = NotificationService(repository)

    result = await service.get_unread(user_id)

    assert result == expected
    repository.get_unread.assert_awaited_once_with(user_id)


@pytest.mark.asyncio
async def test_mark_as_read_is_scoped_to_user():
    """Mark-read must pass both notification and authenticated user ids."""
    from app.services import NotificationService

    repository = AsyncMock()
    notification_id = uuid4()
    user_id = uuid4()
    repository.mark_as_read.return_value = True

    service = NotificationService(repository)

    result = await service.mark_as_read(notification_id, user_id)

    assert result is True
    repository.mark_as_read.assert_awaited_once_with(notification_id, user_id)
