from typing import Any

"""Media pipeline service tests.

Two layers:

1. Pure in-memory tests of the pipeline *state machine* (no DB, no network).
   These run anywhere and cover: the happy path advancing through all stages,
   per-stage success events, the ``content.published`` terminal event, the
   retry-then-fail path emitting ``content.pipeline.failed`` (DLQ), and the
   non-critical-stage skip path. They use in-memory stubs for the event
   publisher and a fake repository, and inject a ``Stage`` whose run() can be
   made to fail a controllable number of times.

2. A thin DB-backed smoke test mirroring billing/streaming that constructs
   the real ``MediaPipelineService`` against a ``db`` session.
"""

from uuid import UUID, uuid4

import pytest

from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.stages import Stage, StageRegistry, install_default_stages
from app.models import (
    PipelineJob,
    PipelineJobStatus,
    PipelineStageLog,
    PipelineStageStatus,
)
from app.repositories import PipelineJobRepository, PipelineStageLogRepository
from app.services import MediaPipelineService, PipelineNonRetryable

# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------


class FakeJobRepo:
    def __init__(self) -> None:
        self.jobs: dict[UUID, PipelineJob] = {}

    async def create(self, job: PipelineJob) -> PipelineJob:
        self.jobs[job.id] = job  # type: ignore[index]
        return job

    async def get(self, job_id: UUID):
        return self.jobs.get(job_id)

    async def get_by_upload_session(self, upload_session_id: UUID):
        for job in self.jobs.values():
            if job.upload_session_id == upload_session_id:
                return job
        return None

    async def save(self, job: PipelineJob) -> PipelineJob:
        self.jobs[job.id] = job  # type: ignore[index]
        return job

    async def list_by_status(self, status, limit: int = 50):
        return [j for j in self.jobs.values() if j.status == status][:limit]


class FakeLogRepo:
    def __init__(self) -> None:
        self.logs: list[PipelineStageLog] = []

    async def record(self, log: PipelineStageLog) -> PipelineStageLog:
        self.logs.append(log)
        return log

    async def list_for_job(self, job_id: UUID) -> list[PipelineStageLog]:
        return [log for log in self.logs if log.job_id == job_id]


class CountingStage(Stage):
    """A stage that fails ``fail_times`` times then succeeds (or always fails).

    Used to exercise the retry + DLQ policy deterministically.
    """

    def __init__(
        self,
        name: str,
        *,
        fail_times: int = 0,
        success_event: str = "",
        critical: bool = True,
        non_retryable: bool = False,
    ) -> None:
        self.name = name
        self.success_event = success_event
        self.critical = critical
        self._fail_times = fail_times
        self._non_retryable = non_retryable
        self.calls = 0

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self._non_retryable:
            raise PipelineNonRetryable(f"{self.name}: fatal")
        if self.calls <= self._fail_times:
            raise RuntimeError(f"{self.name}: synthetic failure #{self.calls}")
        ctx[f"{self.name}_done"] = self.calls
        return ctx


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _fresh_registry() -> StageRegistry:
    return StageRegistry()


def make_service(registry: StageRegistry, max_attempts: int = 3, backoff_base: float = 0.0):
    set_event_publisher(InMemoryEventPublisher())
    return MediaPipelineService(
        job_repo=FakeJobRepo(),  # type: ignore[arg-type]
        log_repo=FakeLogRepo(),  # type: ignore[arg-type]
        registry=registry,
        max_attempts=max_attempts,
        backoff_base=backoff_base,
        backoff_cap=0.0,
    )


# ---------------------------------------------------------------------------
# 1. Pure state-machine tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_advances_through_all_stages_and_publishes():
    reg = _fresh_registry()
    # Two trivial stages is enough to prove ordering + terminal event.
    reg.register(CountingStage("a", success_event="content.a_done"))
    reg.register(CountingStage("b", success_event="content.b_done"))
    service = make_service(reg)

    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    job = await service.advance(job.id)

    assert job.status == PipelineJobStatus.COMPLETED
    assert "a" in job.stage_versions
    assert "b" in job.stage_versions
    topics = [e.topic for e in service.publisher.sent]
    assert "content.a_done" in topics
    assert "content.b_done" in topics
    assert topics[-1] == "content.published"


@pytest.mark.asyncio
async def test_per_stage_success_events_for_full_pipeline():
    """With the real default registry, every stage emits its success event."""
    install_default_stages()
    from app.core.stages import registry as default_registry

    service = make_service(default_registry)
    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    job = await service.advance(job.id)
    assert job.status == PipelineJobStatus.COMPLETED
    topics = {e.topic for e in service.publisher.sent}
    for expected in (
        "content.quarantined",
        "content.scanned",
        "content.metadata_extracted",
        "content.thumbnailed",
        "content.audio_extracted",
        "content.subtitle_extracted",
        "content.encoded",
        "content.packaged",
        "content.uploaded_to_storage",
        "content.cdn_invalidated",
        "content.published",
    ):
        assert expected in topics, f"missing event {expected}"


@pytest.mark.asyncio
async def test_retry_then_fail_emits_pipeline_failed_dlq():
    reg = _fresh_registry()
    # A critical stage that always fails -> exhausts retries -> DLQ.
    reg.register(CountingStage("flaky", success_event="x", critical=True, fail_times=99))
    service = make_service(reg, max_attempts=3, backoff_base=0.0)

    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    job = await service.advance(job.id)

    assert job.status == PipelineJobStatus.FAILED
    assert job.current_stage == "flaky"
    # 3 attempts were made.
    stage = reg.get("flaky")
    assert stage.calls == 3  # type: ignore[attr-defined]
    # The DLQ event was emitted.
    dlq = [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]
    assert len(dlq) == 1
    assert dlq[0].payload["stage"] == "flaky"
    # A failure was logged for each attempt.
    logs = await service.log_repo.list_for_job(job.id)
    assert len([log for log in logs if log.status == PipelineStageStatus.FAILED]) == 3


@pytest.mark.asyncio
async def test_retry_then_succeed_does_not_fail():
    reg = _fresh_registry()
    # Fails twice, succeeds on the 3rd attempt (within max_attempts=3).
    reg.register(CountingStage("recover", success_event="x", fail_times=2))
    service = make_service(reg, max_attempts=3, backoff_base=0.0)

    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    job = await service.advance(job.id)
    assert job.status == PipelineJobStatus.COMPLETED
    assert job.stage_versions["recover"]["completed_at"]
    # No DLQ event.
    assert not [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]


@pytest.mark.asyncio
async def test_non_critical_stage_skip_continues_pipeline():
    reg = _fresh_registry()
    reg.register(CountingStage("must", success_event="x", critical=True))
    # Non-critical stage that always fails -> skipped, not failed.
    reg.register(CountingStage("besteffort", success_event="y", critical=False, fail_times=99))
    service = make_service(reg, max_attempts=2, backoff_base=0.0)

    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    job = await service.advance(job.id)
    assert job.status == PipelineJobStatus.COMPLETED
    # The non-critical stage was recorded as skipped.
    logs = await service.log_repo.list_for_job(job.id)
    skipped = [log for log in logs if log.status == PipelineStageStatus.SKIPPED]
    assert skipped[0].stage == "besteffort"
    assert len(skipped) == 1
    assert skipped[0].stage == "besteffort"


@pytest.mark.asyncio
async def test_non_retryable_fails_immediately_without_retries():
    reg = _fresh_registry()
    reg.register(CountingStage("fatal", success_event="x", non_retryable=True))
    service = make_service(reg, max_attempts=5, backoff_base=0.0)

    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    job = await service.advance(job.id)
    assert job.status == PipelineJobStatus.FAILED
    # Only one attempt — non-retryable failures don't consume retries.
    assert reg.get("fatal").calls == 1  # type: ignore[attr-defined]
    assert any(e.topic == "content.pipeline.failed" for e in service.publisher.sent)


@pytest.mark.asyncio
async def test_start_job_is_idempotent_per_upload_session():
    reg = _fresh_registry()
    service = make_service(reg)
    up = uuid4()
    job1 = await service.start_job(content_id=uuid4(), upload_session_id=up, storage_key="k")
    job2 = await service.start_job(content_id=uuid4(), upload_session_id=up, storage_key="k")
    assert job1.id == job2.id


# ---------------------------------------------------------------------------
# 2. DB-backed smoke test (mirrors billing/streaming style).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_job_persists_to_db(db):
    """Starting a job persists a row via the real repository."""
    service = MediaPipelineService(PipelineJobRepository(db), PipelineStageLogRepository(db))
    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
    )
    assert job.id is not None
    fetched = await service.job_repo.get(job.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.status == PipelineJobStatus.PENDING
