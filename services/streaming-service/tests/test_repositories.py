import pytest
from uuid import uuid4
from datetime import datetime, UTC
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models import Base, PlaybackSession, PlaybackSessionStatus
from app.models.drm import DRMConfig
from app.models.maturity import ContentMaturity
from app.repositories import PlaybackSessionRepository

@pytest.fixture(scope="session")
async def engine(tmp_path_factory):
    """Async SQLite engine for tests."""
    path = tmp_path_factory.mktemp("db") / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest.fixture
async def session(engine):
    """Async session per test, rolled back after."""
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_factory() as s:
        yield s
        await s.rollback()

@pytest.mark.asyncio
async def test_playback_session_crud(session: AsyncSession):
    repo = PlaybackSessionRepository(session)
    user_id = uuid4()
    content_id = uuid4()
    sess = await repo.create(
        user_id=user_id,
        content_id=content_id,
        episode_id=None,
        device_id="dev-1",
        protocol="hls",
        resolution="1080p",
        bitrate_kbps=5000,
        total_duration_seconds=3600,
    )
    await repo.commit()
    assert sess.id is not None
    fetched = await repo.get_by_id(sess.id)
    assert fetched and fetched.user_id == user_id
    await repo.update(sess.id, resolution="720p")
    await repo.commit()
    updated = await repo.get_by_id(sess.id)
    assert updated.resolution == "720p"
    await repo.mark_completed(sess.id)
    await repo.commit()
    completed = await repo.get_by_id(sess.id)
    assert completed.status == PlaybackSessionStatus.COMPLETED

@pytest.mark.asyncio
async def test_drm_config(session: AsyncSession):
    drm = DRMConfig(content_id=uuid4(), fairplay_enabled=True, widevine_enabled=False)
    session.add(drm)
    await session.flush()
    await session.refresh(drm)
    assert drm.id and drm.fairplay_enabled and not drm.widevine_enabled
    assert drm.device_limit == 3 and drm.expiry_hours == 48

@pytest.mark.asyncio
async def test_content_maturity(session: AsyncSession):
    m = ContentMaturity(
        content_id=uuid4(),
        maturity_rating="PG",
        min_age=7,
        requires_parental_consent=False,
        purchase_restricted=False,
    )
    session.add(m)
    await session.flush()
    await session.refresh(m)
    assert m.id and m.maturity_rating == "PG" and m.min_age == 7
