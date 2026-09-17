import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.models import Base, Notification
from app.repositories import NotificationRepository


@pytest.fixture
async def session(tmp_path) -> AsyncSession:
    """Async SQLite session for isolated repository tests."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/repo_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_retrieve(session: AsyncSession):
    repo = NotificationRepository(session)
    user = uuid4()
    notif = await repo.create(user, "Title", "Msg", channel="in_app")
    fetched = await repo.get_by_id(notif.id, user)
    assert fetched is not None and fetched.title == "Title"
    # deduplication via event_id
    event = uuid4()
    n1 = await repo.create(user, "A", "B", event_id=event)
    n2 = await repo.create(user, "C", "D", event_id=event)
    assert n1.id == n2.id


@pytest.mark.asyncio
async def test_unread_and_count(session: AsyncSession):
    repo = NotificationRepository(session)
    user = uuid4()
    await repo.create(user, "U1", "M1")
    await repo.create(user, "U2", "M2")
    unread = await repo.get_unread(user)
    assert len(unread) == 2
    count = await repo.count_unread(user)
    assert count == 2
    # mark one as read
    await repo.mark_as_read(unread[0].id, user)
    assert await repo.count_unread(user) == 1


@pytest.mark.asyncio
async def test_mark_as_read_scoped(session: AsyncSession):
    repo = NotificationRepository(session)
    user = uuid4()
    other = uuid4()
    notif = await repo.create(user, "T", "M")
    assert await repo.mark_as_read(notif.id, user) is True
    # other user cannot mark
    assert await repo.mark_as_read(notif.id, other) is False


@pytest.mark.asyncio
async def test_soft_delete_idempotent(session: AsyncSession):
    repo = NotificationRepository(session)
    user = uuid4()
    other = uuid4()
    notif = await repo.create(user, "Del", "Msg")
    assert await repo.soft_delete(notif.id, user) is True
    # second call still True (idempotent)
    assert await repo.soft_delete(notif.id, user) is True
    # other user cannot delete
    assert await repo.soft_delete(notif.id, other) is False


@pytest.mark.asyncio
async def test_preference_default_and_update(session: AsyncSession):
    repo = NotificationRepository(session)
    user = uuid4()
    pref = await repo.get_preference(user)
    assert pref.in_app_enabled is True
    updated = await repo.update_preference(user, in_app_enabled=False, email_enabled=False)
    assert updated.in_app_enabled is False and updated.email_enabled is False
    with pytest.raises(ValueError):
        await repo.update_preference(user, unknown_field=True)


def test_parse_delivery_errors_static():
    notif = Notification(delivery_errors='{"email":"failed"}')
    parsed = NotificationRepository.parse_delivery_errors(notif)
    assert parsed == {"email": "failed"}
    assert NotificationRepository.parse_delivery_errors(Notification()) == {}
