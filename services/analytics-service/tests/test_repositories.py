import pytest
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models import Base as EventBase
from app.models.dsar import Base as DSARBase, AnalyticsDSARExport
from app.repositories import EventRepository


@pytest.fixture(scope="function")
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True, echo=False)
    async with engine.begin() as conn:
        # create tables for both Base metadata
        await conn.run_sync(EventBase.metadata.create_all)
        await conn.run_sync(DSARBase.metadata.create_all)
    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_event_repository_create_and_query(async_session: AsyncSession):
    repo = EventRepository(async_session)
    user_id = uuid.uuid4()
    await repo.create(user_id=user_id, event_type="playback_started", event_data={"pos": 10})
    await repo.create(user_id=user_id, event_type="playback_paused", event_data={"pos": 20})
    await repo.session.flush()
    events = await repo.get_by_user(user_id, limit=10)
    assert len(events) == 2
    types = {e.event_type for e in events}
    assert types == {"playback_started", "playback_paused"}


@pytest.mark.asyncio
async def test_analytics_dsar_export_query(async_session: AsyncSession):
    uid = uuid.uuid4()
    exp1 = AnalyticsDSARExport(user_id=uid, dsar_id=uuid.uuid4(), data="[]")
    exp2 = AnalyticsDSARExport(user_id=uid, dsar_id=uuid.uuid4(), data="[]", export_format="csv")
    other = AnalyticsDSARExport(user_id=uuid.uuid4(), dsar_id=uuid.uuid4(), data="[]")
    async_session.add_all([exp1, exp2, other])
    await async_session.flush()
    stmt = select(AnalyticsDSARExport).where(AnalyticsDSARExport.user_id == uid)
    result = await async_session.execute(stmt)
    exports = result.scalars().all()
    assert len(exports) == 2
    formats = {e.export_format for e in exports}
    assert formats == {"json", "csv"}
