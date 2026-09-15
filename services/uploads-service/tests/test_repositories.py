import pytest
import datetime
from uuid import uuid4
import sys, os

# Ensure the uploads-service app package is first on PYTHONPATH
service_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
sys.path.insert(0, service_dir)

from app.models import Base, UploadSession, UploadChunk, OutboxEvent, OutboxEventStatus, UploadSessionStatus
from app.repositories import UploadChunkRepository

@pytest.fixture(scope="function")
async def async_db():
    """Async in‑memory SQLite DB for repository tests."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session
    await engine.dispose()

@pytest.mark.asyncio
async def test_create_and_get_session(async_db: AsyncSession):
    repo = UploadChunkRepository(async_db)
    user_id = uuid4()
    session_obj = UploadSession(
        creator_id=user_id,
        total_chunks=3,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    )
    await repo.create(session_obj)
    fetched = await repo.get(session_obj.id)
    assert fetched is not None
    assert fetched.creator_id == user_id
    assert fetched.total_chunks == 3
    assert fetched.status == UploadSessionStatus.INITIATED

@pytest.mark.asyncio
async def test_list_by_creator_limit(async_db: AsyncSession):
    repo = UploadChunkRepository(async_db)
    creator = uuid4()
    for _ in range(3):
        sess = UploadSession(
            creator_id=creator,
            total_chunks=1,
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        )
        await repo.create(sess)
    result = await repo.list_by_creator(creator, limit=2)
    assert len(result) == 2

@pytest.mark.asyncio
async def test_chunk_operations(async_db: AsyncSession):
    repo = UploadChunkRepository(async_db)
    creator = uuid4()
    sess = UploadSession(
        creator_id=creator,
        total_chunks=2,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    )
    await repo.create(sess)
    chunk = UploadChunk(session_id=sess.id, index=0, size=1024, checksum="abc")
    await repo.add_chunk(chunk)
    count = await repo.count_chunks(sess.id)
    assert count == 1
    indices = await repo.received_indices(sess.id)
    assert indices == [0]

@pytest.mark.asyncio
async def test_outbox_event_flow(async_db: AsyncSession):
    repo = UploadChunkRepository(async_db)
    ev = await repo.enqueue_event(topic="test", event_key="key", payload={"a": 1})
    pending = await repo.pending_events(limit=1)
    assert any(p.id == ev.id for p in pending)
    await repo.mark_dispatched(ev.id)
    pending_after = await repo.pending_events(limit=1)
    assert all(p.id != ev.id for p in pending_after)

@pytest.mark.asyncio
async def test_expired_and_aborted_sessions(async_db: AsyncSession):
    repo = UploadChunkRepository(async_db)
    creator = uuid4()
    expired = UploadSession(
        creator_id=creator,
        total_chunks=1,
        expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1),
        status=UploadSessionStatus.UPLOADING,
    )
    await repo.create(expired)
    aborted = UploadSession(
        creator_id=creator,
        total_chunks=1,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        status=UploadSessionStatus.ABORTED,
        storage_cleaned_at=None,
    )
    await repo.create(aborted)
    now = datetime.datetime.utcnow()
    exp = await repo.expired_sessions(now)
    assert any(s.id == expired.id for s in exp)
    unclean = await repo.uncleaned_aborted(now, datetime.timedelta(minutes=5))
    assert any(s.id == aborted.id for s in unclean)
