"""Streaming service tests."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import StreamingSessionRepository
from app.services import StreamingService


@pytest.mark.asyncio
async def test_start_streaming_session(db: AsyncSession):
    """Test starting a streaming session."""
    user_id = uuid4()
    content_id = uuid4()
    device_id = "device-001"

    repo = StreamingSessionRepository(db)
    service = StreamingService(repo, None, None, None)  # type: ignore[call-arg,arg-type]

    session = await service.start_session(user_id, content_id, device_id)  # type: ignore[attr-defined]
    assert session.user_id == user_id
    assert session.content_id == content_id
    assert session.device_id == device_id
    assert session.status == "active"


@pytest.mark.asyncio
async def test_get_watch_history(db: AsyncSession):
    """Test retrieving watch history."""
    user_id = uuid4()
    repo = StreamingSessionRepository(db)
    service = StreamingService(repo, None, None, None)  # type: ignore[call-arg,arg-type]

    history = await service.get_watch_history(user_id, 10)  # type: ignore[attr-defined]
    assert isinstance(history, list)


@pytest.mark.asyncio
async def test_update_watch_position(db: AsyncSession):
    """Test updating watch position."""
    session_id = uuid4()
    position = 1200

    repo = StreamingSessionRepository(db)
    service = StreamingService(repo, None, None, None)  # type: ignore[call-arg,arg-type]

    await service.update_position(session_id, position)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_end_session(db: AsyncSession):
    """Test ending streaming session."""
    session_id = uuid4()
    repo = StreamingSessionRepository(db)
    service = StreamingService(repo, None, None, None)  # type: ignore[call-arg,arg-type]

    await service.end_session(session_id)  # type: ignore[attr-defined]
