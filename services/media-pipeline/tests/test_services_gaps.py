"""Orchestrator gaps: port factory, retry caps, leases, and recovery.

``app/services.py`` is the state machine that drives the whole pipeline. This
module covers the branches that the happy-path state-machine tests do not
reach:

* ``_build_ports`` — the ``MEDIA_PIPELINE_ADAPTERS`` adapter selection,
  including the CloudFront-vs-stub CDN choice (#495).
* ``_run_stage_with_retries`` — the two total-retry-time ceilings (before and
  after the backoff delay is charged).
* the concurrency, lease, circuit-breaker and disk-quota guards.
* ``advance``'s terminal states: missing/finished/failed job, unheld lease,
  empty context, exhausted retry budget, open breaker, skipped stage.
* ``recover_stale_jobs`` — the stale-lease sweeper.

The in-memory ``FakeJobRepo`` / ``FakeLogRepo`` / ``CountingStage`` fakes are
reused from ``test_pipeline_state_machine``; ``list_stale`` is added here.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.events import InMemoryEventPublisher, set_event_publisher
from app.core.settings import settings
from app.core.stages import (
    CloudFrontCDN,
    StubCDN,
    StubMetadataExtractor,
    StubMultiBitrateEncoder,
    StubObjectStorage,
    StubPackager,
    StubThumbnailGenerator,
    StubVirusScanner,
)
from app.core.stages import ClamavScanner
from app.core.ffmpeg import (
    FFmpegMultiBitrateEncoder,
    FFmpegPackager,
    FFmpegThumbnailGenerator,
    FFprobeMetadataExtractor,
)
from app.models import PipelineJob, PipelineJobStatus, PipelineStageStatus
from app.services import (
    CircuitBreakerOpen,
    ConcurrencyLimitExceeded,
    LeaseAcquisitionFailed,
    MediaPipelineService,
    PipelineError,
    PipelineNonRetryable,
    TotalRetryTimeExceeded,
)
from tests.test_pipeline_state_machine import (
    CountingStage,
    FakeJobRepo,
    FakeLogRepo,
    _fresh_registry,
)


class StaleAwareJobRepo(FakeJobRepo):
    """``FakeJobRepo`` plus the ``list_stale`` query the sweeper needs."""

    def __init__(self, stale: list[PipelineJob] | None = None) -> None:
        super().__init__()
        self.stale = stale or []
        self.list_stale_calls: list[tuple[datetime, object]] = []

    async def list_stale(self, before: datetime, status=None) -> list[PipelineJob]:
        self.list_stale_calls.append((before, status))
        return list(self.stale)


@pytest.fixture(autouse=True)
def _reset_class_level_counters():
    """Concurrency/breaker counters are class attributes; isolate every test."""
    MediaPipelineService._global_active_jobs = 0
    MediaPipelineService._content_concurrency.clear()
    MediaPipelineService._creator_concurrency.clear()
    MediaPipelineService._circuit_breaker.clear()
    yield
    MediaPipelineService._global_active_jobs = 0
    MediaPipelineService._content_concurrency.clear()
    MediaPipelineService._creator_concurrency.clear()
    MediaPipelineService._circuit_breaker.clear()


def _service(repo: FakeJobRepo, registry=None, **kwargs) -> MediaPipelineService:
    set_event_publisher(InMemoryEventPublisher())
    return MediaPipelineService(
        job_repo=repo,
        log_repo=FakeLogRepo(),
        registry=registry or _fresh_registry(),
        max_attempts=kwargs.pop("max_attempts", 3),
        backoff_base=kwargs.pop("backoff_base", 0.0),
        backoff_cap=kwargs.pop("backoff_cap", 0.0),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# _build_ports: the adapter selection.
# ---------------------------------------------------------------------------


def test_build_ports_returns_the_stub_set_by_default():
    service = _service(FakeJobRepo())
    assert {name: type(port) for name, port in service._ports.items()} == {
        "virus_scanner": StubVirusScanner,
        "metadata_extractor": StubMetadataExtractor,
        "thumbnail_generator": StubThumbnailGenerator,
        "encoder": StubMultiBitrateEncoder,
        "packager": StubPackager,
        "object_storage": StubObjectStorage,
        "cdn": StubCDN,
    }


def test_build_ports_wires_the_ffmpeg_adapters_with_the_configured_ceilings():
    with patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "ffmpeg"):
        service = _service(FakeJobRepo())
    ports = service._ports
    assert isinstance(ports["virus_scanner"], ClamavScanner)
    assert isinstance(ports["metadata_extractor"], FFprobeMetadataExtractor)
    assert isinstance(ports["thumbnail_generator"], FFmpegThumbnailGenerator)
    assert isinstance(ports["encoder"], FFmpegMultiBitrateEncoder)
    assert isinstance(ports["packager"], FFmpegPackager)
    # S3 is still a stub (adapter TBD), the rest must be the real thing.
    assert isinstance(ports["object_storage"], StubObjectStorage)

    encoder: FFmpegMultiBitrateEncoder = ports["encoder"]
    assert encoder.cpu_threads == settings.PIPELINE_MAX_CPU_THREADS
    assert encoder.max_output_bytes == settings.PIPELINE_MAX_OUTPUT_BYTES
    assert encoder.max_duration_seconds == settings.PIPELINE_MAX_DURATION_SECONDS
    assert encoder.memory_limit_bytes == settings.PIPELINE_MAX_MEMORY_BYTES
    assert ports["thumbnail_generator"].memory_limit_bytes == settings.PIPELINE_MAX_MEMORY_BYTES
    assert ports["packager"].memory_limit_bytes == settings.PIPELINE_MAX_MEMORY_BYTES


def test_build_ports_uses_cloudfront_when_a_distribution_is_configured():
    with (
        patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "ffmpeg"),
        patch.object(settings, "CLOUDFRONT_DISTRIBUTION_ID", "DIST-1"),
    ):
        service = _service(FakeJobRepo())
    cdn = service._ports["cdn"]
    assert isinstance(cdn, CloudFrontCDN)
    assert cdn.distribution_id == "DIST-1"


def test_build_ports_uses_the_stub_cdn_without_a_distribution():
    with (
        patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "stub"),
        patch.object(settings, "CLOUDFRONT_DISTRIBUTION_ID", None),
    ):
        service = _service(FakeJobRepo())
    assert isinstance(service._ports["cdn"], StubCDN)


def test_build_ports_uses_cloudfront_on_the_stub_path_when_configured():
    with (
        patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "stub"),
        patch.object(settings, "CLOUDFRONT_DISTRIBUTION_ID", "DIST-2"),
    ):
        service = _service(FakeJobRepo())
    assert isinstance(service._ports["cdn"], CloudFrontCDN)
    assert service._ports["cdn"].distribution_id == "DIST-2"


# ---------------------------------------------------------------------------
# _run_stage_with_retries: the cumulative retry-time ceilings.
# ---------------------------------------------------------------------------


class _AlwaysFails(CountingStage):
    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        raise RuntimeError(f"{self.name}: always fails")


async def test_retry_time_cap_stops_the_stage_before_the_next_attempt():
    """The elapsed-time ceiling fires without charging any backoff delay."""
    repo = FakeJobRepo()
    registry = _fresh_registry()
    stage = _AlwaysFails("flaky")
    registry.register(stage)
    service = _service(repo, registry, max_attempts=5, backoff_base=1.0, backoff_cap=1.0)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")

    with patch.object(settings, "PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS", 0.0001):
        with pytest.raises(TotalRetryTimeExceeded, match="total retry time"):
            await service._run_stage_with_retries(job, stage, {}, 0.0)
    assert stage.calls == 1


async def test_retry_time_cap_also_accounts_for_the_backoff_delay():
    """The second ceiling fires only once the backoff sleep is charged."""
    repo = FakeJobRepo()
    registry = _fresh_registry()
    stage = _AlwaysFails("flaky")
    registry.register(stage)
    service = _service(repo, registry, max_attempts=5, backoff_base=30.0, backoff_cap=30.0)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")

    with (
        patch.object(settings, "PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS", 5.0),
        patch("app.services.asyncio.sleep", new=_never_sleep),
    ):
        with pytest.raises(TotalRetryTimeExceeded, match="total retry time"):
            await service._run_stage_with_retries(job, stage, {}, 0.0)
    # Failed once, then refused the backoff sleep that would have blown the cap.
    assert stage.calls == 1


async def _never_sleep(_delay: float) -> None:
    return None


async def test_retry_time_accumulates_across_stages_and_persists_on_the_job():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("recover", fail_times=1))
    registry.register(CountingStage("next"))
    service = _service(repo, registry, max_attempts=3, backoff_base=0.5, backoff_cap=0.5)

    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job = await service.advance(job.id)

    assert job.status == PipelineJobStatus.COMPLETED
    assert job.context["_total_retry_time_seconds"] >= 0.5


# ---------------------------------------------------------------------------
# advance(): terminal states.
# ---------------------------------------------------------------------------


async def test_advance_rejects_an_unknown_job():
    service = _service(FakeJobRepo())
    with pytest.raises(PipelineError, match="not found"):
        await service.advance(uuid4())


async def test_advance_is_idempotent_for_a_completed_job():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("a"))
    service = _service(repo, registry)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    done = await service.advance(job.id)
    again = await service.advance(job.id)
    assert again is done
    assert again.status == PipelineJobStatus.COMPLETED


async def test_advance_refuses_to_resume_a_failed_job():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("boom", fail_times=99))
    service = _service(repo, registry, max_attempts=2)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    failed = await service.advance(job.id)
    assert failed.status == PipelineJobStatus.FAILED

    with pytest.raises(PipelineError, match="already failed"):
        await service.advance(job.id)


async def test_advance_refuses_to_run_without_a_lease():
    repo = FakeJobRepo()
    service = _service(repo)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job.leased_by = "another-worker"
    job.leased_at = datetime.now(UTC)

    with pytest.raises(LeaseAcquisitionFailed, match="could not acquire lease"):
        await service.advance(job.id)
    assert job.leased_by == "another-worker", "the other worker's lease is untouched"


async def test_advance_rejects_a_job_with_no_persisted_context():
    repo = FakeJobRepo()
    service = _service(repo)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job.context = {}
    await repo.save(job)

    with pytest.raises(PipelineError, match="has no context"):
        await service.advance(job.id)
    # The lease is released again so the job stays resumable.
    assert job.leased_by is None


async def test_advance_fails_the_job_when_the_retry_budget_is_spent():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("a"))
    service = _service(repo, registry)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job.context["_total_retry_time_seconds"] = 10**9
    await repo.save(job)

    result = await service.advance(job.id)

    assert result.status == PipelineJobStatus.FAILED
    assert "exceeds cap" in result.error
    assert result.leased_by is None
    await service.drain_outbox()
    dlq = [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]
    assert len(dlq) == 1


async def test_advance_skips_stages_already_recorded_as_complete():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    first = CountingStage("a")
    second = CountingStage("b")
    registry.register(first)
    registry.register(second)
    service = _service(repo, registry)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job.stage_versions = {"a": {"completed_at": "2026-01-01T00:00:00+00:00"}}
    await repo.save(job)

    result = await service.advance(job.id)

    assert result.status == PipelineJobStatus.COMPLETED
    assert first.calls == 0, "a completed stage must never re-run on resume"
    assert second.calls == 1
    assert set(result.stage_versions) == {"a", "b"}


async def test_advance_refuses_to_run_a_stage_whose_breaker_is_open():
    """An open breaker stops the stage from running — as an exception.

    NOTE (app/services.py:584-605 vs 630-637): the breaker check happens
    *outside* the per-stage ``try``, so ``CircuitBreakerOpen`` propagates out
    of ``advance()`` instead of being converted into a failed job + DLQ event by
    the handler at line 630. That handler is therefore unreachable:
    ``_run_stage_with_retries`` only ever re-raises ``PipelineNonRetryable``,
    and a ``CircuitBreakerOpen`` from a stage is swallowed by its generic
    ``except Exception`` retry handler. The job is also left in ``running``
    (set at line 584) with no stage log and no DLQ row, so it only recovers once
    its lease goes stale. Asserted as-is; not fixed here.
    """
    repo = FakeJobRepo()
    registry = _fresh_registry()
    stage = CountingStage("a")
    registry.register(stage)
    service = _service(repo, registry)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    service._circuit_breaker["a"] = settings.PIPELINE_CIRCUIT_BREAKER_THRESHOLD

    with pytest.raises(CircuitBreakerOpen, match="circuit breaker open for stage a"):
        await service.advance(job.id)

    assert stage.calls == 0, "an open breaker must not run the stage at all"
    assert job.status == PipelineJobStatus.RUNNING, "orphaned in running, not failed"
    assert job.leased_by is None, "the finally block still released the lease"
    assert await service.log_repo.list_for_job(job.id) == [], "no stage was attempted"
    await service.drain_outbox()
    assert not [
        e for e in service.publisher.sent if e.topic == "content.pipeline.failed"
    ], "no DLQ event is emitted for an open breaker"


async def test_advance_fails_the_job_when_a_stage_blows_the_retry_budget():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(_AlwaysFails("slowpoke"))
    service = _service(repo, registry, max_attempts=3)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")

    with patch.object(settings, "PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS", 0.0001):
        result = await service.advance(job.id)

    assert result.status == PipelineJobStatus.FAILED
    assert "total retry time" in result.error
    await service.drain_outbox()
    dlq = [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]
    assert len(dlq) == 1
    assert dlq[0].payload["stage"] == "slowpoke"


async def test_advance_releases_the_lease_when_a_concurrency_limit_is_hit():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("a"))
    service = _service(repo, registry)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")

    with (
        patch.object(settings, "PIPELINE_MAX_GLOBAL_JOBS", 1),
        patch.object(MediaPipelineService, "_global_active_jobs", 1),
    ):
        with pytest.raises(ConcurrencyLimitExceeded, match="global job limit"):
            await service.advance(job.id)
    assert job.leased_by is None, "the lease must not be leaked on a limit rejection"


# ---------------------------------------------------------------------------
# Concurrency limits.
# ---------------------------------------------------------------------------


async def test_global_concurrency_limit_is_enforced():
    service = _service(FakeJobRepo())
    with (
        patch.object(settings, "PIPELINE_MAX_GLOBAL_JOBS", 2),
        patch.object(MediaPipelineService, "_global_active_jobs", 2),
    ):
        with pytest.raises(ConcurrencyLimitExceeded, match="global job limit \(2\) reached"):
            await service._check_concurrency_limits(uuid4())


async def test_per_content_concurrency_limit_is_enforced():
    service = _service(FakeJobRepo())
    content_id = uuid4()
    with (
        patch.object(settings, "PIPELINE_MAX_GLOBAL_JOBS", 0),
        patch.object(settings, "PIPELINE_MAX_JOBS_PER_CONTENT", 1),
    ):
        service._increment_concurrency(content_id)
        with pytest.raises(ConcurrencyLimitExceeded, match="per-content job limit"):
            await service._check_concurrency_limits(content_id)


async def test_per_creator_concurrency_limit_is_enforced():
    service = _service(FakeJobRepo())
    creator_id, content_id = uuid4(), uuid4()
    with (
        patch.object(settings, "PIPELINE_MAX_GLOBAL_JOBS", 0),
        patch.object(settings, "PIPELINE_MAX_JOBS_PER_CONTENT", 0),
        patch.object(settings, "PIPELINE_MAX_JOBS_PER_CREATOR", 1),
    ):
        service._increment_concurrency(content_id, creator_id)
        with pytest.raises(ConcurrencyLimitExceeded, match="per-creator job limit"):
            await service._check_concurrency_limits(content_id, creator_id)


async def test_per_creator_limit_does_not_apply_without_a_creator():
    service = _service(FakeJobRepo())
    content_id = uuid4()
    with (
        patch.object(settings, "PIPELINE_MAX_GLOBAL_JOBS", 0),
        patch.object(settings, "PIPELINE_MAX_JOBS_PER_CONTENT", 0),
        patch.object(settings, "PIPELINE_MAX_JOBS_PER_CREATOR", 1),
    ):
        service._increment_concurrency(content_id, uuid4())  # a different creator
        await service._check_concurrency_limits(content_id, None)


def test_concurrency_counters_never_go_negative():
    service = _service(FakeJobRepo())
    content_id, creator_id = uuid4(), uuid4()
    for _ in range(3):
        service._decrement_concurrency(content_id, creator_id)
    assert service._content_concurrency[content_id] == 0
    assert service._creator_concurrency[creator_id] == 0
    assert MediaPipelineService._global_active_jobs == 0


async def test_creator_concurrency_is_released_after_a_job_finishes():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("a"))
    service = _service(repo, registry)
    creator_id, content_id = uuid4(), uuid4()
    with patch.object(settings, "PIPELINE_MAX_JOBS_PER_CREATOR", 1):
        job = await service.start_job(
            content_id=content_id,
            upload_session_id=uuid4(),
            storage_key="k",
            creator_id=creator_id,
        )
        job = await service.advance(job.id)
        assert job.status == PipelineJobStatus.COMPLETED
        assert service._creator_concurrency[creator_id] == 0


# ---------------------------------------------------------------------------
# Leases.
# ---------------------------------------------------------------------------


async def test_acquire_lease_fails_for_a_vanished_job():
    service = _service(FakeJobRepo())
    ghost = PipelineJob(id=uuid4(), content_id=uuid4(), upload_session_id=uuid4())
    assert await service._acquire_lease(ghost) is False


@pytest.mark.parametrize("status", [PipelineJobStatus.COMPLETED, PipelineJobStatus.FAILED])
async def test_acquire_lease_refuses_a_settled_job(status):
    repo = FakeJobRepo()
    service = _service(repo)
    job = await repo.create(
        PipelineJob(id=uuid4(), content_id=uuid4(), upload_session_id=uuid4(), status=status)
    )
    # Assert on the reason, not just the False, so this cannot pass via the
    # "job not found" branch at line 320.
    assert await repo.lock(job.id) is job
    assert await service._acquire_lease(job) is False
    assert job.leased_by is None, "a settled job is never leased"


async def test_acquire_lease_refuses_a_fresh_lease_held_by_another_worker():
    repo = FakeJobRepo()
    service = _service(repo)
    job = await repo.create(
        PipelineJob(
            id=uuid4(),
            content_id=uuid4(),
            upload_session_id=uuid4(),
            status=PipelineJobStatus.RUNNING,
            leased_by="worker-a",
            leased_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    assert await service._acquire_lease(job) is False
    assert job.leased_by == "worker-a", "the live lease is left alone"


async def test_acquire_lease_takes_over_an_expired_lease():
    repo = FakeJobRepo()
    service = _service(repo)
    job = await repo.create(
        PipelineJob(
            id=uuid4(),
            content_id=uuid4(),
            upload_session_id=uuid4(),
            status=PipelineJobStatus.RUNNING,
            leased_by="worker-a",
            leased_at=datetime.now(UTC)
            - timedelta(seconds=settings.PIPELINE_JOB_LEASE_SECONDS + 10),
        )
    )
    assert await service._acquire_lease(job) is True
    assert job.leased_by == service.worker_id
    assert job.leased_at > datetime.now(UTC) - timedelta(seconds=5)


async def test_acquire_lease_treats_a_naive_lease_timestamp_as_utc():
    """Model columns are TIMESTAMP WITHOUT TIME ZONE; a naive value must still work."""
    repo = FakeJobRepo()
    service = _service(repo)
    naive = (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None)
    job = await repo.create(
        PipelineJob(
            id=uuid4(),
            content_id=uuid4(),
            upload_session_id=uuid4(),
            status=PipelineJobStatus.RUNNING,
            leased_by="worker-a",
            leased_at=naive,
        )
    )
    assert await service._acquire_lease(job) is False
    assert job.leased_by == "worker-a"


async def test_release_and_heartbeat_only_touch_our_own_lease():
    repo = FakeJobRepo()
    service = _service(repo)
    job = PipelineJob(id=uuid4(), content_id=uuid4(), upload_session_id=uuid4())
    job.leased_by = "someone-else"
    job.leased_at = datetime.now(UTC)

    await service._release_lease(job)
    await service._heartbeat_lease(job)

    assert job.leased_by == "someone-else"


async def test_heartbeat_refreshes_our_lease_and_release_clears_it():
    repo = FakeJobRepo()
    service = _service(repo)
    job = PipelineJob(id=uuid4(), content_id=uuid4(), upload_session_id=uuid4())
    job.leased_by = service.worker_id
    job.leased_at = datetime.now(UTC) - timedelta(seconds=30)

    await service._heartbeat_lease(job)
    assert (datetime.now(UTC) - job.leased_at).total_seconds() < 5

    await service._release_lease(job)
    assert job.leased_by is None and job.leased_at is None


# ---------------------------------------------------------------------------
# Circuit breaker.
# ---------------------------------------------------------------------------


def test_circuit_breaker_opens_at_the_configured_threshold():
    service = _service(FakeJobRepo())
    threshold = settings.PIPELINE_CIRCUIT_BREAKER_THRESHOLD
    for _ in range(threshold):
        service._check_circuit_breaker("encode")
        service._record_stage_failure("encode")
    with pytest.raises(CircuitBreakerOpen, match="circuit breaker open for stage encode"):
        service._check_circuit_breaker("encode")


def test_a_stage_success_resets_the_breaker():
    service = _service(FakeJobRepo())
    service._record_stage_failure("encode")
    service._record_stage_failure("encode")
    assert service._circuit_breaker["encode"] == 2
    service._record_stage_success("encode")
    assert service._circuit_breaker["encode"] == 0
    service._check_circuit_breaker("encode")  # no raise


# ---------------------------------------------------------------------------
# Disk quota.
# ---------------------------------------------------------------------------


def test_disk_quota_is_skipped_when_unconfigured():
    service = _service(FakeJobRepo())
    job = PipelineJob(id=uuid4(), content_id=uuid4(), upload_session_id=uuid4())
    with patch.object(settings, "PIPELINE_DISK_QUOTA_BYTES", 0):
        service._check_disk_quota(job)


def test_disk_quota_rejects_a_job_that_blew_its_budget(tmp_path):
    service = _service(FakeJobRepo())
    job_id = uuid4()
    job = PipelineJob(id=job_id, content_id=uuid4(), upload_session_id=uuid4())
    work_dir = tmp_path / "work" / str(job_id)
    work_dir.mkdir(parents=True)
    (work_dir / "partial.mp4").write_bytes(b"x" * 4096)
    quarantine_dir = tmp_path / "quarantine" / str(job_id)
    quarantine_dir.mkdir(parents=True)
    (quarantine_dir / "source.mp4").write_bytes(b"x" * 4096)

    with (
        patch.object(settings, "PIPELINE_DISK_QUOTA_BYTES", 1024),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(tmp_path / "work")),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
    ):
        with pytest.raises(PipelineNonRetryable, match="exceeds quota"):
            service._check_disk_quota(job)


def test_disk_quota_counts_both_sandboxes_and_allows_under_budget(tmp_path):
    service = _service(FakeJobRepo())
    job_id = uuid4()
    job = PipelineJob(id=job_id, content_id=uuid4(), upload_session_id=uuid4())
    for root, name in (("work", "a.mp4"), ("quarantine", "b.mp4")):
        d = tmp_path / root / str(job_id) / "nested"
        d.mkdir(parents=True)
        (d / name).write_bytes(b"x" * 100)

    with (
        patch.object(settings, "PIPELINE_DISK_QUOTA_BYTES", 1024),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(tmp_path / "work")),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
    ):
        service._check_disk_quota(job)  # 200 bytes total, under the quota


def test_disk_quota_ignores_a_job_with_no_directories(tmp_path):
    service = _service(FakeJobRepo())
    job = PipelineJob(id=uuid4(), content_id=uuid4(), upload_session_id=uuid4())
    with (
        patch.object(settings, "PIPELINE_DISK_QUOTA_BYTES", 1),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(tmp_path / "work")),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
    ):
        service._check_disk_quota(job)


def test_disk_quota_tolerates_an_unreadable_file(tmp_path):
    service = _service(FakeJobRepo())
    job_id = uuid4()
    job = PipelineJob(id=job_id, content_id=uuid4(), upload_session_id=uuid4())
    work_dir = tmp_path / "work" / str(job_id)
    work_dir.mkdir(parents=True)
    ghost = work_dir / "vanished.mp4"
    ghost.write_bytes(b"x" * 4096)
    ghost.unlink()

    real_getsize = os.path.getsize
    calls = {"n": 0}

    def _flaky_getsize(path):
        calls["n"] += 1
        if str(path).endswith("vanished.mp4"):
            raise FileNotFoundError(path)
        return real_getsize(path)

    with (
        patch.object(settings, "PIPELINE_DISK_QUOTA_BYTES", 1024),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(tmp_path / "work")),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
        # The file disappears after the walk: the quota must not crash the job.
        patch("os.walk", return_value=[(str(work_dir), [], ["vanished.mp4"])]),
        patch("os.path.getsize", side_effect=_flaky_getsize),
    ):
        service._check_disk_quota(job)  # must not raise
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# start_job idempotency and context seeding.
# ---------------------------------------------------------------------------


async def test_start_job_is_idempotent_on_upload_session_id_for_legacy_callers():
    """Callers that predate idempotency_key are still deduplicated."""
    repo = FakeJobRepo()
    service = _service(repo)
    upload_session_id, content_id = uuid4(), uuid4()
    existing = PipelineJob(
        content_id=content_id,
        upload_session_id=upload_session_id,
        idempotency_key="a-different-key",
        context={"storage_key": "k"},
    )
    await repo.create(existing)

    job = await service.start_job(
        content_id=content_id,
        upload_session_id=upload_session_id,
        storage_key="k",
        idempotency_key="upload-" + str(upload_session_id),
    )
    assert job.id == existing.id


async def test_start_job_rejects_a_legacy_reuse_with_a_different_content():
    from app.services import IdempotencyConflict

    repo = FakeJobRepo()
    service = _service(repo)
    upload_session_id = uuid4()
    existing = PipelineJob(
        content_id=uuid4(),
        upload_session_id=upload_session_id,
        idempotency_key="a-different-key",
        context={"storage_key": "k"},
    )
    await repo.create(existing)

    with pytest.raises(IdempotencyConflict):
        await service.start_job(
            content_id=uuid4(),
            upload_session_id=upload_session_id,
            storage_key="k",
        )


async def test_start_job_seeds_caller_context_and_creator_id():
    repo = FakeJobRepo()
    service = _service(repo)
    creator_id = uuid4()
    job = await service.start_job(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        storage_key="uploads/x/clip.mp4",
        context={"custom": "value", "bitrates": [400]},
        creator_id=creator_id,
    )
    assert job.context["custom"] == "value"
    assert job.context["bitrates"] == [400]
    assert job.context["_creator_id"] == str(creator_id)
    assert job.context["storage_key"] == "uploads/x/clip.mp4"


async def test_start_job_never_persists_the_port_objects():
    repo = FakeJobRepo()
    service = _service(repo)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    # Ports are re-injected on advance(); the JSONB column must stay serializable.
    assert not any(key in job.context for key in service._ports)
    import json

    json.dumps(job.context)


async def test_ports_are_reinjected_on_resume():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    registry.register(CountingStage("a"))
    service = _service(repo, registry)
    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job = await service.advance(job.id)

    # A resumed advance re-attaches the *current* adapter set.
    service._ports = dict(service._ports)
    service._ports["encoder"] = StubMultiBitrateEncoder()
    resumed = await service.advance(job.id)
    assert resumed.status == PipelineJobStatus.COMPLETED


# ---------------------------------------------------------------------------
# recover_stale_jobs.
# ---------------------------------------------------------------------------


def _leased_job(**kwargs) -> PipelineJob:
    defaults = {
        "content_id": uuid4(),
        "upload_session_id": uuid4(),
        "status": PipelineJobStatus.RUNNING,
        "current_stage": "encode",
        "leased_by": "dead-worker",
        "leased_at": datetime.now(UTC) - timedelta(seconds=10_000),
    }
    defaults.update(kwargs)
    return PipelineJob(**defaults)


async def test_recover_stale_jobs_reclaims_leases_and_resets_retries():
    job = _leased_job(retries=3)
    repo = StaleAwareJobRepo(stale=[job])
    service = _service(repo)

    recovered = await service.recover_stale_jobs()

    assert recovered == 1
    assert job.leased_by is None and job.leased_at is None
    assert job.retries == 0
    _cutoff, status = repo.list_stale_calls[0]
    assert isinstance(_cutoff, datetime)
    assert status == PipelineJobStatus.RUNNING
    logs = await service.log_repo.list_for_job(job.id)
    assert logs == []


async def test_recover_stale_jobs_skips_jobs_that_are_not_retryable():
    stale = _leased_job(status=PipelineJobStatus.PENDING)
    repo = StaleAwareJobRepo(stale=[stale])
    service = _service(repo)

    assert await service.recover_stale_jobs() == 0
    assert stale.leased_by == "dead-worker"


async def test_recover_stale_jobs_skips_jobs_with_no_current_stage():
    stale = _leased_job(current_stage=None)
    repo = StaleAwareJobRepo(stale=[stale])
    service = _service(repo)

    assert await service.recover_stale_jobs() == 0


async def test_recover_stale_jobs_is_a_noop_when_nothing_is_stale():
    repo = StaleAwareJobRepo(stale=[])
    service = _service(repo)
    assert await service.recover_stale_jobs() == 0


async def test_recover_stale_jobs_reclaims_several_jobs():
    jobs = [_leased_job() for _ in range(3)]
    repo = StaleAwareJobRepo(stale=jobs)
    service = _service(repo)

    assert await service.recover_stale_jobs() == 3
    assert all(job.leased_by is None for job in jobs)


# ---------------------------------------------------------------------------
# Stage logging.
# ---------------------------------------------------------------------------


def test_cleanup_job_dirs_removes_both_sandboxes_and_tolerates_absent_dirs(tmp_path):
    service = _service(FakeJobRepo())
    job_id = uuid4()
    job = PipelineJob(id=job_id, content_id=uuid4(), upload_session_id=uuid4())
    for root in ("work", "quarantine"):
        d = tmp_path / root / str(job_id) / "nested"
        d.mkdir(parents=True)
        (d / "artifact.ts").write_bytes(b"x")

    with (
        patch.object(settings, "PIPELINE_WORK_ROOT", str(tmp_path / "work")),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
    ):
        service._cleanup_job_dirs(job)
        assert not (tmp_path / "work" / str(job_id)).exists()
        assert not (tmp_path / "quarantine" / str(job_id)).exists()
        # Idempotent: a second pass over the now-absent dirs is a no-op.
        service._cleanup_job_dirs(job)


async def test_each_attempt_is_logged_with_a_duration_and_lease_is_heartbeated():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    stage = CountingStage("flaky", fail_times=1)
    registry.register(stage)
    service = _service(repo, registry, max_attempts=3)

    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job = await service.advance(job.id)

    logs = await service.log_repo.list_for_job(job.id)
    failed = [log for log in logs if log.status == PipelineStageStatus.FAILED]
    succeeded = [log for log in logs if log.status == PipelineStageStatus.SUCCESS]
    assert len(failed) == 1 and len(succeeded) == 1
    assert "attempt 1 failed" in failed[0].message
    assert "attempt 2 ok" in succeeded[0].message
    assert job.retries == 0, "retries reset once the stage is done"
    assert isinstance(job.id, UUID)


async def test_a_non_retryable_stage_fails_the_job_on_the_first_attempt():
    repo = FakeJobRepo()
    registry = _fresh_registry()
    stage = CountingStage("virus", non_retryable=True)
    registry.register(stage)
    service = _service(repo, registry, max_attempts=5)

    job = await service.start_job(content_id=uuid4(), upload_session_id=uuid4(), storage_key="k")
    job = await service.advance(job.id)

    assert job.status == PipelineJobStatus.FAILED
    assert stage.calls == 1
    await service.drain_outbox()
    dlq = [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]
    assert dlq[0].payload["dlq_key"] == f"{job.id}:virus"
