"""#1141: the job lease must survive a stage that outlasts the idle timeout.

Production engines set ``idle_in_transaction_session_timeout=30000``
(``app/core/database.py``) and ffmpeg stages run far longer. If ``advance()``
holds a transaction open across ``stage.run``, Postgres kills that backend: the
row lock and the never-committed lease vanish, another worker claims the job
(a duplicate transcode), and the first worker's FAILED + DLQ write is lost.

The timeout firing is simulated deterministically, with no sleeps: while the
stage is parked on an ``asyncio.Event``, every other backend of this database
that is ``idle in transaction`` is terminated with ``pg_terminate_backend`` —
exactly what the server-side timeout does to such a backend.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.events import InMemoryEventPublisher
from app.core.stages import Stage, StageRegistry
from app.models import PipelineJobStatus
from app.repositories import PipelineJobRepository, PipelineStageLogRepository
from app.services import LeaseAcquisitionFailed, MediaPipelineService, PipelineError
from tests.test_pipeline_state_machine import CountingStage

STEP_TIMEOUT = 10.0


class GatedStage(Stage):
    """A stage that parks until released, like a long ffmpeg run issuing no SQL."""

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.success_event = ""
        self.critical = True
        self.fail = fail
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("encoder crashed after a long run")
        return ctx


@pytest_asyncio.fixture
async def engines(postgres_url: str, db_session: AsyncSession) -> AsyncIterator[dict]:
    """Separate engines (separate backends) for worker A, worker B and admin.

    ``db_session`` is requested only for its schema creation + truncation.
    """
    url = make_url(postgres_url).set(drivername="postgresql+asyncpg")
    built = {role: create_async_engine(url, future=True) for role in ("a", "b", "admin")}
    try:
        yield built
    finally:
        for engine in built.values():
            await engine.dispose()


def _service(session: AsyncSession, stage: Stage, worker_id: str) -> MediaPipelineService:
    registry = StageRegistry()
    registry.register(stage)
    return MediaPipelineService(
        job_repo=PipelineJobRepository(session),
        log_repo=PipelineStageLogRepository(session),
        registry=registry,
        publisher=InMemoryEventPublisher(),
        max_attempts=1,
        backoff_base=0.0,
        backoff_cap=0.0,
        worker_id=worker_id,
    )


async def _fire_idle_in_transaction_timeout(admin: AsyncEngine) -> int:
    """Do what ``idle_in_transaction_session_timeout`` does, right now."""
    async with admin.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
            )
        )
        killed = int(result.scalar_one())
        await conn.commit()
        return killed


async def _row(admin: AsyncEngine, job_id: UUID) -> dict[str, Any]:
    async with admin.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT status, leased_by, leased_at, stage_versions, error "
                "FROM pipeline_jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
        return dict(result.mappings().one())


async def _outbox_topics(admin: AsyncEngine, job_id: UUID) -> list[str]:
    async with admin.connect() as conn:
        result = await conn.execute(
            text("SELECT topic FROM outbox_events WHERE event_key = :key ORDER BY created_at"),
            {"key": str(job_id)},
        )
        return [row[0] for row in result.all()]


async def _settle(task: asyncio.Task, stage: GatedStage) -> None:
    """Never leave worker A parked, whatever the test outcome."""
    stage.release.set()
    if not task.done():
        await asyncio.wait({task}, timeout=STEP_TIMEOUT)
    if task.done() and not task.cancelled():
        task.exception()  # mark retrieved; the test asserts on the outcome itself


async def test_a_second_worker_cannot_claim_a_job_whose_stage_outlasts_the_idle_timeout(
    engines: dict,
) -> None:
    stage = GatedStage("long_encode_1141_claim")
    factory_a = async_sessionmaker(engines["a"], expire_on_commit=False)
    factory_b = async_sessionmaker(engines["b"], expire_on_commit=False)
    async with factory_a() as session_a:
        worker_a = _service(session_a, stage, "worker-a")
        job = await worker_a.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k/clip.mp4"
        )
        task = asyncio.create_task(worker_a.advance(job.id))
        try:
            await asyncio.wait_for(stage.entered.wait(), STEP_TIMEOUT)
            killed = await _fire_idle_in_transaction_timeout(engines["admin"])

            probe = CountingStage("long_encode_1141_claim")
            async with factory_b() as session_b:
                worker_b = _service(session_b, probe, "worker-b")
                with pytest.raises(LeaseAcquisitionFailed):
                    await worker_b.advance(job.id)
            assert probe.calls == 0, "worker B must not re-run a stage worker A is running"
            assert killed == 0, "no transaction may stay open while a stage runs"

            stage.release.set()
            result = await asyncio.wait_for(task, STEP_TIMEOUT)
        finally:
            await _settle(task, stage)

    assert result.status == PipelineJobStatus.COMPLETED
    row = await _row(engines["admin"], job.id)
    assert row["status"] == PipelineJobStatus.COMPLETED.name
    assert "long_encode_1141_claim" in row["stage_versions"]
    assert row["leased_by"] is None and row["leased_at"] is None
    assert (await _outbox_topics(engines["admin"], job.id))[-1] == "content.published"


async def test_a_stage_failure_after_the_idle_timeout_still_fails_the_job_and_emits_dlq(
    engines: dict,
) -> None:
    stage = GatedStage("long_encode_1141_dlq", fail=True)
    factory_a = async_sessionmaker(engines["a"], expire_on_commit=False)
    async with factory_a() as session_a:
        worker_a = _service(session_a, stage, "worker-a")
        job = await worker_a.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k/clip.mp4"
        )
        task = asyncio.create_task(worker_a.advance(job.id))
        try:
            await asyncio.wait_for(stage.entered.wait(), STEP_TIMEOUT)
            await _fire_idle_in_transaction_timeout(engines["admin"])
            stage.release.set()
            result = await asyncio.wait_for(task, STEP_TIMEOUT)
        finally:
            await _settle(task, stage)

    assert result.status == PipelineJobStatus.FAILED
    row = await _row(engines["admin"], job.id)
    assert row["status"] == PipelineJobStatus.FAILED.name
    assert "encoder crashed" in row["error"]
    assert row["leased_by"] is None, "the failed job's lease is released"
    assert "content.pipeline.failed" in await _outbox_topics(engines["admin"], job.id)


async def test_a_worker_whose_lease_was_taken_writes_nothing(engines: dict) -> None:
    stage = GatedStage("long_encode_1141_fence")
    factory_a = async_sessionmaker(engines["a"], expire_on_commit=False)
    async with factory_a() as session_a:
        worker_a = _service(session_a, stage, "worker-a")
        job = await worker_a.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k/clip.mp4"
        )
        task = asyncio.create_task(worker_a.advance(job.id))
        try:
            await asyncio.wait_for(stage.entered.wait(), STEP_TIMEOUT)
            await _fire_idle_in_transaction_timeout(engines["admin"])
            # Worker A's lease expired and worker B legitimately took the job over.
            async with engines["admin"].begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE pipeline_jobs SET leased_by = 'worker-b', leased_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": job.id},
                )
            stage.release.set()
            with pytest.raises(PipelineError, match="lost its lease"):
                await asyncio.wait_for(task, STEP_TIMEOUT)
        finally:
            await _settle(task, stage)

    row = await _row(engines["admin"], job.id)
    assert row["leased_by"] == "worker-b", "worker B's lease must survive worker A"
    assert row["status"] == PipelineJobStatus.RUNNING.name
    assert "long_encode_1141_fence" not in row["stage_versions"]
    assert "content.published" not in await _outbox_topics(engines["admin"], job.id)


async def test_an_early_exit_releases_the_committed_lease(engines: dict) -> None:
    """The claim is committed up front, so every early exit must commit its release."""
    stage = GatedStage("long_encode_1141_no_ctx")
    factory_a = async_sessionmaker(engines["a"], expire_on_commit=False)
    async with factory_a() as session_a:
        worker_a = _service(session_a, stage, "worker-a")
        job = await worker_a.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k/clip.mp4"
        )
        job_id = job.id
        async with engines["admin"].begin() as conn:
            await conn.execute(
                text("UPDATE pipeline_jobs SET context = '{}'::jsonb WHERE id = :id"),
                {"id": job_id},
            )
        session_a.expire_all()
        with pytest.raises(PipelineError, match="has no context"):
            await worker_a.advance(job_id)

    row = await _row(engines["admin"], job_id)
    assert row["leased_by"] is None and row["leased_at"] is None
    assert not stage.entered.is_set()
