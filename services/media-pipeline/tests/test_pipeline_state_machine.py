from typing import Any

"""Media pipeline service tests.

Pure in-memory tests of the pipeline *state machine* (no DB, no network).
They cover: the happy path advancing through all stages, per-stage success
events, the ``content.published`` terminal event, the retry-then-fail path
emitting ``content.pipeline.failed`` (DLQ), and the non-critical-stage skip
path. They use in-memory stubs for the event publisher and a stub
``PipelineJobRepository``, and inject a ``Stage`` whose run() can be made to
fail a controllable number of times.
"""

from uuid import UUID, uuid4

import pytest

from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.stages import Stage, StageRegistry, install_default_stages
from app.models import (
    OutboxEventStatus,
    PipelineJob,
    PipelineJobStatus,
    PipelineStageLog,
    PipelineStageStatus,
)
from app.services import MediaPipelineService, PipelineNonRetryable

# ---------------------------------------------------------------------------
# In-memory fakes.
# ---------------------------------------------------------------------------


class _NoopSession:
    async def commit(self) -> None:
        return None


class FakeOutboxRow:
    """In-memory stand-in for the outbox_events row."""

    def __init__(self, topic: str, event_key: str, payload: dict) -> None:
        from datetime import UTC, datetime
        from uuid import uuid4

        self.id = uuid4()
        self.topic = topic
        self.event_key = event_key
        self.payload = payload
        self.status = OutboxEventStatus.PENDING
        self.created_at = datetime.now(UTC)
        self.dispatched_at = None


class FakeJobRepo:
    def __init__(self) -> None:
        self.jobs: dict[UUID, PipelineJob] = {}
        self.events: list[FakeOutboxRow] = []
        self.session = _NoopSession()

    async def create(self, job: PipelineJob) -> PipelineJob:
        if job.id is None:
            from uuid import uuid4

            job.id = uuid4()
        self.jobs[job.id] = job
        return job

    async def get(self, job_id: UUID):
        return self.jobs.get(job_id)

    async def get_by_upload_session(self, upload_session_id: UUID):
        for job in self.jobs.values():
            if job.upload_session_id == upload_session_id:
                return job
        return None

    async def get_by_idempotency_key(self, key: str):
        for job in self.jobs.values():
            if getattr(job, "idempotency_key", None) == key:
                return job
        return None

    async def save(self, job: PipelineJob) -> PipelineJob:
        self.jobs[job.id] = job
        return job

    # -- Transactional outbox (in-memory stand-in) ---------------------------

    async def enqueue_event(self, topic: str, event_key: str, payload: dict) -> FakeOutboxRow:
        row = FakeOutboxRow(topic=topic, event_key=event_key, payload=payload)
        self.events.append(row)
        return row

    async def pending_events(self, limit: int = 100) -> list[FakeOutboxRow]:
        return [e for e in self.events if e.status == OutboxEventStatus.PENDING][:limit]

    async def mark_dispatched(self, event_id: UUID) -> None:
        from datetime import UTC, datetime

        for row in self.events:
            if row.id == event_id:
                row.status = OutboxEventStatus.DISPATCHED
                row.dispatched_at = datetime.now(UTC)


class FakeLogRepo:
    def __init__(self) -> None:
        self.logs: list[PipelineStageLog] = []

    async def record(self, log: PipelineStageLog) -> PipelineStageLog:
        self.logs.append(log)
        return log

    async def list_for_job(self, job_id: UUID) -> list[PipelineStageLog]:
        return [l for l in self.logs if l.job_id == job_id]


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
        job_repo=FakeJobRepo(),
        log_repo=FakeLogRepo(),
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
    await service.drain_outbox()

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
    await service.drain_outbox()
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
    assert stage.calls == 3
    # The DLQ event was emitted (after the outbox drain).
    await service.drain_outbox()
    dlq = [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]
    assert len(dlq) == 1
    assert dlq[0].payload["stage"] == "flaky"
    # A failure was logged for each attempt.
    logs = await service.log_repo.list_for_job(job.id)
    assert len([l for l in logs if l.status == PipelineStageStatus.FAILED]) == 3


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
    await service.drain_outbox()
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
    skipped = [l for l in logs if l.status == PipelineStageStatus.SKIPPED]
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
    assert reg.get("fatal").calls == 1
    await service.drain_outbox()
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


# ---------------------------------------------------------------------------
# Transactional outbox drain (at-least-once delivery).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_publishes_pending_rows_and_marks_dispatched():
    reg = _fresh_registry()
    reg.register(CountingStage("a", success_event="content.a_done"))
    service = make_service(reg)

    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/clip.mp4"
    )
    job = await service.advance(job.id)
    assert job.status == PipelineJobStatus.COMPLETED
    pending = await service.job_repo.pending_events()
    assert len(pending) == 2  # content.a_done + content.published

    processed = await service.drain_outbox()

    assert processed == 2
    assert len(service.publisher.sent) == 2
    assert all(
        row.status == OutboxEventStatus.DISPATCHED and row.dispatched_at is not None
        for row in pending
    )
    # Already-dispatched rows are not re-published.
    assert await service.drain_outbox() == 0
    assert len(service.publisher.sent) == 2


@pytest.mark.asyncio
async def test_drain_keeps_row_pending_on_publish_failure():
    """Broker failure leaves the row PENDING so the next drain retries it."""
    reg = _fresh_registry()
    reg.register(CountingStage("a", success_event="content.a_done"))
    service = make_service(reg)

    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/clip.mp4"
    )
    job = await service.advance(job.id)
    rows = await service.job_repo.pending_events()
    row = rows[0]

    class FailingPublisher:
        async def publish(self, event):
            raise OSError("broker unreachable")

    service.publisher = FailingPublisher()
    processed = await service.drain_outbox()

    assert processed == len(rows)
    assert all(
        r.status == OutboxEventStatus.PENDING and r.dispatched_at is None for r in rows
    )

    # Broker recovers: the same row now goes out.
    service.publisher = InMemoryEventPublisher()
    await service.drain_outbox()
    assert row.status == OutboxEventStatus.DISPATCHED
    assert service.publisher.sent[0].topic == "content.a_done"


@pytest.mark.asyncio
async def test_drain_publishes_in_fifo_order():
    """PENDING rows are drained oldest-first (out-of-order prevention)."""
    reg = _fresh_registry()
    reg.register(CountingStage("a", success_event="content.a_done"))
    service = make_service(reg)

    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/clip.mp4"
    )
    await service.advance(job.id)

    rows = await service.job_repo.pending_events()
    assert rows[0].created_at <= rows[1].created_at
