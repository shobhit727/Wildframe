import pytest
import pytest_asyncio
from uuid import uuid4
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.models import Base
from app.repositories import RecommendationRepository, UserPreferencesRepository


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
async def test_delete_for_content(db_session: AsyncSession):
    repo = RecommendationRepository(db_session)
    uid = uuid4()
    cid = uuid4()
    await repo.create(uid, cid, 0.3)
    await db_session.commit()
    deleted = await repo.delete_for_content(cid)
    assert deleted == 1
    # second delete is idempotent
    deleted_again = await repo.delete_for_content(cid)
    assert deleted_again == 0
