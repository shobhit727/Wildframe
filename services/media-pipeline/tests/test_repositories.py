import uuid
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models import (
    Base,
    PipelineJob,
    PipelineJobStatus,
    OutboxEvent,
    OutboxEventStatus,
    PipelineStageLog,
    PipelineStageStatus,
)
from app.repositories import (
    PipelineJobRepository,
    PipelineStageLogRepository,
)

@pytest_asyncio.fixture
async def db_session(tmp_path) -> AsyncSession:
    """Create a fresh async PostgreSQL DB per test file using testcontainers."""
    with PostgresContainer("postgres:15") as pg:
        engine = create_async_engine(pg.get_connection_url().replace("postgresql://", "postgresql+asyncpg://"), echo=False, future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async_session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session_factory() as session:
            yield session
        await engine.dispose()
    # End of fixture

# ---------------------------------------------------------------------------
# PipelineJobRepository tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pipeline_job_create_get_and_list(db_session: AsyncSession):
    repo = PipelineJobRepository(db_session)
    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, upload_session_id=uuid.uuid4(), status=PipelineJobStatus.PENDING)
    await repo.create(job)
    # flush to persist
    await db_session.flush()
    fetched = await repo.get(job_id)
    assert fetched is not None
    assert fetched.id == job_id
    # list by status
    listed = await repo.list_by_status(PipelineJobStatus.PENDING, limit=10)
    assert any(j.id == job_id for j in listed)

# ---------------------------------------------------------------------------
# Outbox (transactional) helpers – exercised via PipelineJobRepository methods.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_outbox_enqueue_pending_and_mark(db_session: AsyncSession):
    repo = PipelineJobRepository(db_session)
    # enqueue an event
    evt = await repo.enqueue_event(topic="media.job", event_key="evt-1", payload={"job": "test"})
    await db_session.flush()
    assert isinstance(evt, OutboxEvent)
    # pending events should include it
    pending = await repo.pending_events(limit=5)
    assert any(e.id == evt.id for e in pending)
    # mark dispatched
    await repo.mark_dispatched(evt.id)
    await db_session.flush()
    # after dispatch, pending should not contain it
    pending_after = await repo.pending_events(limit=5)
    assert all(e.id != evt.id for e in pending_after)

# ---------------------------------------------------------------------------
# PipelineStageLogRepository tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stage_log_record_and_list(db_session: AsyncSession):
    job_repo = PipelineJobRepository(db_session)
    job = PipelineJob(id=uuid.uuid4(), upload_session_id=uuid.uuid4(), status=PipelineJobStatus.PENDING)
    await job_repo.create(job)
    await db_session.flush()
    log_repo = PipelineStageLogRepository(db_session)
    # record two stages for the same job
    log1 = PipelineStageLog(job_id=job.id, stage="ingest", status=PipelineStageStatus.SUCCESS, details={})
    log2 = PipelineStageLog(job_id=job.id, stage="encode", status=PipelineStageStatus.FAILED, details={"error": "boom"})
    await log_repo.record(log1)
    await log_repo.record(log2)
    await db_session.flush()
    logs = await log_repo.list_for_job(job.id)
    assert len(logs) == 2
    stages = {l.stage for l in logs}
    assert stages == {"ingest", "encode"}
