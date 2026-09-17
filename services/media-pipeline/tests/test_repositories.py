import uuid
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from app.models import (
    Base,
    PipelineJob,
    PipelineJobStatus,
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
        engine = create_async_engine(
            pg.get_connection_url().replace("postgresql://", "postgresql+asyncpg://"),
            echo=False,
            future=True,
            pool_pre_ping=True,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
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
    job = PipelineJob(
        id=job_id,
        content_id=uuid.uuid4(),
        upload_session_id=uuid.uuid4(),
        status=PipelineJobStatus.PENDING,
    )
    await repo.create(job)
    await db_session.flush()

    fetched = await repo.get(job_id)
    assert fetched is not None
    assert fetched.id == job_id
    assert fetched.status == PipelineJobStatus.PENDING

    listed = await repo.list_by_status(PipelineJobStatus.PENDING)
    assert any(j.id == job_id for j in listed)


# ---------------------------------------------------------------------------
# Outbox (transactional) helpers – exercised via PipelineJobRepository methods.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_outbox_enqueue_pending_and_mark(db_session: AsyncSession):
    repo = PipelineJobRepository(db_session)
    job = PipelineJob(
        content_id=uuid.uuid4(),
        upload_session_id=uuid.uuid4(),
        status=PipelineJobStatus.PENDING,
    )
    await repo.create(job)
    await db_session.flush()

    evt = await repo.enqueue_outbox(
        topic="pipeline.job.created",
        payload={"job_id": str(job.id)},
        event_key=str(job.id),
    )
    assert evt.status.value == "pending"

    pending_before = await repo.list_pending_outbox()
    assert any(e.id == evt.id for e in pending_before)

    await repo.mark_outbox_dispatched(evt.id)
    await db_session.flush()

    pending_after = await repo.list_pending_outbox()
    assert all(e.id != evt.id for e in pending_after)


# ---------------------------------------------------------------------------
# PipelineStageLogRepository tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stage_log_record_and_list(db_session: AsyncSession):
    job_repo = PipelineJobRepository(db_session)
    job = PipelineJob(
        content_id=uuid.uuid4(),
        upload_session_id=uuid.uuid4(),
        status=PipelineJobStatus.PENDING,
    )
    await job_repo.create(job)
    await db_session.flush()

    log_repo = PipelineStageLogRepository(db_session)
    log1 = PipelineStageLog(
        job_id=job.id,
        stage="ingest",
        status=PipelineStageStatus.SUCCESS,
        duration_ms=123,
        message="ingest ok",
    )
    log2 = PipelineStageLog(
        job_id=job.id,
        stage="encode",
        status=PipelineStageStatus.SUCCESS,
        duration_ms=456,
        message="encode ok",
    )
    await log_repo.record(log1)
    await log_repo.record(log2)
    await db_session.flush()
    logs = await log_repo.list_for_job(job.id)
    assert len(logs) == 2
    stages = {log_entry.stage for log_entry in logs}
    assert stages == {"ingest", "encode"}
