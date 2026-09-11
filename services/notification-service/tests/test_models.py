import sys
from pathlib import Path
import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.models import Base, Notification


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/models.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_notification_defaults(session: AsyncSession):
    """Persisted notifications receive the documented unread pending defaults."""
    notification = Notification(user_id=uuid.uuid4(), title="Test", message="Body")
    session.add(notification)
    await session.flush()
    assert notification.delivery_status == "pending"
    assert notification.is_read is False
    assert isinstance(notification.created_at, datetime)
    assert notification.created_at.tzinfo is None
    assert notification.read_at is None
    assert notification.delivered_at is None
    assert notification.deleted_at is None


@pytest.mark.asyncio
async def test_notification_preserves_delivery_and_read_state(session: AsyncSession):
    """Notification state values remain intact after persistence."""
    user_id = uuid.uuid4()
    notification = Notification(
        user_id=user_id,
        title="Title",
        message="Message",
        channel="email",
        delivery_status="delivered",
        is_read=True,
    )
    session.add(notification)
    await session.flush()
    notification_id = notification.id
    await session.commit()
    loaded = await session.get(Notification, notification_id)
    assert loaded is not None
    assert loaded.user_id == user_id
    assert loaded.title == "Title"
    assert loaded.message == "Message"
    assert loaded.channel == "email"
    assert loaded.delivery_status == "delivered"
    assert loaded.is_read is True
