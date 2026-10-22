import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import app.services as services_mod
from app.models import Base
from app.repositories import RecommendationRepository, UserPreferencesRepository
from app.services import RecommendationService


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session(tmp_path):
    """Async SQLite DB session fixture isolated per test file."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db", echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_user_preferences_get_or_create(db_session: AsyncSession):
    repo = UserPreferencesRepository(db_session)
    uid = uuid4()
    pref = await repo.get_or_create(uid)
    await db_session.commit()
    fetched = await repo.get_or_create(uid)
    assert fetched.id == pref.id
    assert fetched.user_id == uid


@pytest.mark.asyncio
async def test_recommendation_crud(db_session: AsyncSession):
    repo = RecommendationRepository(db_session)
    uid = uuid4()
    cid = uuid4()
    await repo.create(uid, cid, 0.9, reason="test", algorithm="cf")
    await db_session.commit()
    fetched = await repo.get_for_user(uid)
    assert len(fetched) == 1
    assert fetched[0].content_id == cid
    await repo.clear_for_user(uid)
    await db_session.commit()
    empty = await repo.get_for_user(uid)
    assert empty == []


@pytest.mark.asyncio
async def test_get_for_user_limit_and_order(db_session: AsyncSession):
    repo = RecommendationRepository(db_session)
    uid = uuid4()
    # create three recommendations with different scores
    await repo.create(uid, uuid4(), 0.5)
    await repo.create(uid, uuid4(), 0.9)
    await repo.create(uid, uuid4(), 0.7)
    await db_session.commit()
    results = await repo.get_for_user(uid, limit=2)
    assert len(results) == 2
    # highest scores first
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_latest_created_at(db_session: AsyncSession):
    repo = RecommendationRepository(db_session)
    uid = uuid4()
    await repo.create(uid, uuid4(), 0.1)
    await db_session.commit()
    ts = await repo.latest_created_at(uid)
    assert isinstance(ts, datetime)


@pytest.mark.asyncio
async def test_latest_created_at_returns_the_newest_row_not_the_oldest(db_session: AsyncSession):
    """``latest_created_at`` is the staleness oracle for ``get_recommendations``.

    It must be the *newest* stored row. The existing test inserted a single row,
    so ``order_by(desc(created_at), ...)`` and ``order_by(created_at, ...)``
    were indistinguishable and the suite passed with ascending order -- which
    would make a freshly generated rail look permanently stale and regenerate
    on every read.

    Note the secondary ``id.desc()`` is deliberately *not* asserted here:
    ``latest_created_at`` projects ``created_at`` only, so no caller can
    observe which of several tied rows it picked. Pinning it would require
    compiling a second ``select()`` in the test, which proves the test's own
    query rather than the repository's.
    """
    repo = RecommendationRepository(db_session)
    uid = uuid4()
    await repo.create(uid, uuid4(), 0.1)
    await repo.create(uid, uuid4(), 0.2)
    await repo.create(uid, uuid4(), 0.3)
    await db_session.commit()

    # Pin the timestamps: the column default is "now" for all three, so
    # ordering by it would otherwise be decided by insertion order.
    stamps = {
        0.1: datetime(2024, 1, 1, 0, 0, 0),
        0.2: datetime(2024, 6, 1, 0, 0, 0),
        0.3: datetime(2024, 3, 1, 0, 0, 0),
    }
    for row in await repo.get_for_user(uid, limit=10):
        row.created_at = stamps[row.score]
    await db_session.commit()

    assert (await repo.latest_created_at(uid)) == datetime(2024, 6, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_delete_for_content(db_session: AsyncSession):
    repo = RecommendationRepository(db_session)
    uid = uuid4()
    cid = uuid4()
    await repo.create(uid, cid, 0.3)
    await db_session.commit()
    # Returns the affected user ids so the event handler can evict their caches.
    deleted = await repo.delete_for_content(cid)
    assert deleted == [uid]
    # second delete is idempotent
    deleted_again = await repo.delete_for_content(cid)
    assert deleted_again == []


# ----------------------------------------------------------------------
# Preference persistence: a dislike has to survive the write (BUG #1)
# ----------------------------------------------------------------------


def _empty_catalog() -> MagicMock:
    catalog = MagicMock()
    catalog.fetch_genres = AsyncMock(return_value=[])
    catalog.fetch_global = AsyncMock(return_value=[])
    catalog.aclose = AsyncMock()
    return catalog


@pytest.mark.asyncio
async def test_update_preferences_persists_disliked_genres(tmp_path):
    """A stated dislike must be stored, not accepted and dropped.

    ``PUT /preferences/{user_id}`` validates ``disliked_genres`` and hands it to
    ``update_preferences``, which returned 200 either way. The assertions below
    run the real service over a real session and then re-read through a
    *separate* session, so a value that only lived on the writer's identity map
    cannot pass.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/prefs.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user_id = uuid4()

    try:
        async with maker() as writer:
            service = RecommendationService(
                UserPreferencesRepository(writer), RecommendationRepository(writer)
            )
            with (
                patch.object(services_mod, "get_catalog_client", lambda: _empty_catalog()),
                patch.object(services_mod, "_cache_invalidate", AsyncMock()),
            ):
                await service.update_preferences(user_id, ["action"], ["horror", "slasher"])
            await writer.commit()

        async with maker() as reader:
            stored = await UserPreferencesRepository(reader).get_or_create(user_id)
            assert stored.liked_genres == ["action"]
            assert stored.disliked_genres == ["horror", "slasher"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_dislike_survives_a_later_liked_only_update(tmp_path):
    """Sending ``liked_genres`` alone must not wipe an earlier dislike.

    ``None`` means "leave untouched" for both arguments; a caller that has
    never expressed a dislike must not silently acquire one, and one that has
    must not lose it by updating an unrelated field.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/prefs.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user_id = uuid4()

    try:
        async with maker() as writer:
            service = RecommendationService(
                UserPreferencesRepository(writer), RecommendationRepository(writer)
            )
            with (
                patch.object(services_mod, "get_catalog_client", lambda: _empty_catalog()),
                patch.object(services_mod, "_cache_invalidate", AsyncMock()),
            ):
                await service.update_preferences(user_id, ["action"], ["horror"])
                await writer.commit()
                await service.update_preferences(user_id, ["comedy"])
            await writer.commit()

        async with maker() as reader:
            stored = await UserPreferencesRepository(reader).get_or_create(user_id)
            assert stored.liked_genres == ["comedy"]
            assert stored.disliked_genres == ["horror"]
    finally:
        await engine.dispose()
