import sys
from pathlib import Path
import uuid
from datetime import datetime
from sqlalchemy import select
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.models import Base, Notification
import json
from app.repositories import NotificationRepository
from app.models import NotificationPreference


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
@pytest.mark.asyncio
async def test_notification_event_id_uniqueness(session: AsyncSession):
    """event_id is unique; duplicate raises IntegrityError on flush."""
    evt = uuid.uuid4()
    n1 = Notification(user_id=uuid.uuid4(), title="A", message="B", event_id=evt)
    n2 = Notification(user_id=uuid.uuid4(), title="C", message="D", event_id=evt)
    session.add_all([n1, n2])
    import pytest, sqlalchemy
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await session.flush()

@pytest.mark.asyncio
async def test_notification_parse_delivery_errors(session: AsyncSession):
    """delivery_errors JSON is parsed correctly via repository helper."""
    errors = {"email": "failed: smtp down", "in-app": "sent"}
    notif = Notification(
        user_id=uuid.uuid4(),
        title="X",
        message="Y",
        delivery_errors=json.dumps(errors),
    )
    session.add(notif)
    await session.flush()
    parsed = NotificationRepository.parse_delivery_errors(notif)
    assert parsed == errors

@pytest.mark.asyncio
async def test_notification_preference_defaults(session: AsyncSession):
    """Default NotificationPreference fields are all enabled (True)."""
    pref = NotificationPreference(user_id=uuid.uuid4())
    session.add(pref)
    await session.flush()
    assert pref.in_app_enabled is True
    assert pref.email_enabled is True
    assert pref.push_enabled is True
    assert pref.sms_enabled is True

@pytest.mark.asyncio
async def test_notification_repository_deduplication(session: AsyncSession):
    """Creating twice with same event_id returns the same notification row."""
    repo = NotificationRepository(session)
    event = uuid.uuid4()
    n1 = await repo.create(uuid.uuid4(), "A", "B", channel="email", event_id=event)
    n2 = await repo.create(uuid.uuid4(), "C", "D", channel="email", event_id=event)
    assert n1.id == n2.id
    # Ensure only one row persisted
    result = await session.execute(
        select(Notification).where(Notification.event_id == event)
    )
    rows = result.scalars().all()
    assert len(rows) == 1

@pytest.mark.asyncio
async def test_notification_delivery_status_transition(session: AsyncSession):
    """delivery_status transitions persist correctly."""
    notif = Notification(user_id=uuid.uuid4(), title="T", message="M")
    session.add(notif)
    await session.flush()
    notif.delivery_status = "failed"
    await session.commit()
    loaded = await session.get(Notification, notif.id)
    assert loaded is not None and loaded.delivery_status == "failed"

@pytest.mark.asyncio
async def test_preference_update_invalid_field(session: AsyncSession):
    """Updating unknown preference field raises ValueError."""
    repo = NotificationRepository(session)
    user = uuid.uuid4()
    # Ensure a pref row exists
    _ = await repo.get_preference(user)
    with pytest.raises(ValueError):
        await repo.update_preference(user, unknown_field=True)