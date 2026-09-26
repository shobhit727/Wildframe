import os
import uuid
from collections.abc import AsyncIterator
from contextlib import ExitStack

from sqlalchemy.engine import make_url
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
async def db_session() -> AsyncIterator[AsyncSession]:
    """Use disposable PostgreSQL; TEST_DATABASE_URL must name a test database."""
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            postgres = stack.enter_context(PostgresContainer("postgres:15"))
            url = postgres.get_connection_url()
        engine = create_async_engine(
            make_url(url).set(drivername="postgresql+asyncpg"),
            echo=False,
            future=True,
            pool_pre_ping=True,
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()


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

    evt = await repo.enqueue_event(
        topic="pipeline.job.created",
        payload={"job_id": str(job.id)},
        event_key=str(job.id),
    )
    assert evt.status.value == "pending"

    pending_before = await repo.pending_events()
    assert any(e.id == evt.id for e in pending_before)

    await repo.mark_dispatched(evt.id)
    await db_session.flush()
    await db_session.refresh(evt)
    assert evt.status.value == "dispatched"
    assert evt.dispatched_at is not None

    pending_after = await repo.pending_events()
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
        status=PipelineStageStatus.FAILED,
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
    assert {
        log_entry.stage: (log_entry.status, log_entry.duration_ms, log_entry.message)
        for log_entry in logs
    } == {
        "ingest": (PipelineStageStatus.SUCCESS, 123, "ingest ok"),
        "encode": (PipelineStageStatus.FAILED, 456, "encode ok"),
    }
