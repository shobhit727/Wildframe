from __future__ import annotations

from typing import Any

"""Media pipeline service business logic — the pipeline ORCHESTRATOR.

The pipeline is an event-driven state machine over ``PipelineJob``. The
orchestrator walks an ordered list of stages (the ``Stage`` registry, see
``app/core/stages.py``), running each via the ``Stage`` port, recording a
``PipelineStageLog`` per attempt, and emitting a domain event on success.

Retry strategy & dead-letter queue (DLQ)
----------------------------------------
Each stage is independently retryable. The policy is *exponential backoff with
a cap and a per-stage maximum attempt count*::

    attempt  →  sleep before next attempt
    1        →  BACKOFF_BASE_SECONDS * 2**0   (1x)
    2        →  BACKOFF_BASE_SECONDS * 2**1   (2x)
    3        →  BACKOFF_BASE_SECONDS * 2**2   (x)
    ...
    up to MAX_STAGE_ATTEMPTS

``PipelineJob.retries`` counts attempts at the *current* stage and is reset to
0 whenever the job advances to a new stage. When ``retries`` reaches
``MAX_STAGE_ATTEMPTS`` the stage has *exhausted retries*:

    * if the stage is **critical** → the job is marked ``failed`` and a
      ``content.pipeline.failed`` event is emitted (the DLQ signal). A
      downstream operator / moderation-service can inspect the job's
      ``pipeline_stage_logs`` to decide replay vs. reject.
    * if the stage is **non-critical** (best-effort, e.g. thumbnail) → the
      failure is logged, the stage is recorded as ``skipped``, and the job
      continues. This keeps a flaky best-effort step from killing the whole
      pipeline.

Rejected (non-retryable) failures — e.g. a virus detected in ``virus_scan`` —
fail the job immediately without consuming retries, because retrying cannot
fix them. Stages signal this by raising ``PipelineNonRetryable``.

The orchestrator is idempotent: ``advance`` resumes from the last completed
stage (tracked in ``stage_versions``) and is safe to call repeatedly, which is
what makes at-least-once event delivery safe.

Hardening (new in v2)
---------------------
* **Lease-based concurrency** — a worker must acquire a lease (``leased_by``,
  ``leased_at``) before advancing a job. Stale leases are recovered via
  ``PipelineJobRepository.list_stale``.
* **Idempotency key** — external callers *must* supply an ``idempotency_key``.
  The unique index on ``PipelineJob.idempotency_key`` prevents duplicate jobs.
* **Circuit breaker** — after ``PIPELINE_CIRCUIT_BREAKER_THRESHOLD`` consecutive
  failures of the *same stage across all jobs*, new work for that stage is
  blocked until an operator resets it.
* **Per-job total retry time cap** — ``PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS``
  bounds the cumulative time a job may spend retrying across all stages.
* **Global & per-content concurrency limits** — ``PIPELINE_MAX_GLOBAL_JOBS``
  and ``PIPELINE_MAX_JOBS_PER_CONTENT`` cap parallelism.
* **Disk quota** — ``PIPELINE_DISK_QUOTA_BYTES`` limits per-job temp storage.
* **Adapter selection** — ``MEDIA_PIPELINE_ADAPTERS=stub|ffmpeg`` selects
  real vs. stub ports at startup.
* **Temp-file cleanup** — per-job work/quarantine dirs are removed on failure
  or completion.
* **DLQ dedup** — ``content.pipeline.failed`` events carry a job-scoped
  ``dlq_key`` so downstream consumers can deduplicate.
"""

import asyncio
import logging
import os
import shutil
import time
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from app.core.events import Event, EventPublisher, get_event_publisher
from app.core.settings import settings
from app.core.stages import Stage, StageRegistry
from app.core.stages import registry as default_registry
from app.models import (
    PipelineJob,
    PipelineJobStatus,
    PipelineStageLog,
    PipelineStageStatus,
)
from app.repositories import PipelineJobRepository, PipelineStageLogRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context (de)serialization.
#
# ``PipelineJob.context`` is a JSONB column. The orchestrator must persist it
# after every stage so ``advance()`` can resume across requests/workers.
# Stage ports (VirusScanner, MetadataExtractor, ...) are not JSON-serializable,
# so we strip them before save and re-inject on load. This keeps the
# persisted payload small and JSONB-safe.
# ---------------------------------------------------------------------------


def _is_port(value: Any) -> bool:
    """True if ``value`` is a stage port instance (non-serializable)."""
    from app.core.stages import (
        CDN,
        MetadataExtractor,
        MultiBitrateEncoder,
        ObjectStorage,
        Packager,
        ThumbnailGenerator,
        VirusScanner,
    )

    return isinstance(
        value,
        (
            VirusScanner,
            MetadataExtractor,
            ThumbnailGenerator,
            MultiBitrateEncoder,
            Packager,
            ObjectStorage,
            CDN,
        ),
    )


def _rehydrate_context(
    job: PipelineJob, ctx: dict[str, Any], ports: dict[str, Any]
) -> dict[str, Any]:
    """Re-inject ports into a deserialized context dict.

    On a fresh start the orchestrator populates ports in ``_build_context``.
    On resume, ``ctx`` came from JSONB and ports were stripped. Re-add the
    *current* ports (which reflect the active adapter selection) so the same
    stage code works in both cases.
    """
    for k, v in ports.items():
        ctx.setdefault(k, v)
    ctx.setdefault("job_id", str(job.id))
    ctx.setdefault("content_id", str(job.content_id))
    ctx.setdefault("upload_session_id", str(job.upload_session_id))
    ctx.setdefault("quarantine_root", settings.PIPELINE_QUARANTINE_ROOT)
    ctx.setdefault("bitrates", [400, 800, 1200, 2400, 4800])
    return ctx


# ---------------------------------------------------------------------------
# Retry / DLQ tuning (overridable via settings).
# ---------------------------------------------------------------------------

MAX_STAGE_ATTEMPTS: int = settings.PIPELINE_MAX_STAGE_ATTEMPTS
BACKOFF_BASE_SECONDS: float = settings.PIPELINE_BACKOFF_BASE_SECONDS
BACKOFF_CAP_SECONDS: float = settings.PIPELINE_BACKOFF_CAP_SECONDS


class PipelineError(Exception):
    """Base pipeline error."""


class PipelineNonRetryable(PipelineError):
    """A failure that cannot be fixed by retrying (e.g. virus detected)."""


class CircuitBreakerOpen(PipelineError):
    """Raised when a stage's circuit breaker is open."""


class ConcurrencyLimitExceeded(PipelineError):
    """Raised when global or per-content concurrency limit is exceeded."""


class LeaseAcquisitionFailed(PipelineError):
    """Raised when a worker cannot acquire a lease on the job."""


class IdempotencyConflict(PipelineError):
    """Raised when an idempotency key conflicts with an existing job."""


class TotalRetryTimeExceeded(PipelineError):
    """Raised when a job's cumulative retry time exceeds the cap."""


class MediaPipelineService:
    """Orchestrates the animation pipeline as a retryable state machine."""

    # Circuit breaker state: stage_name -> consecutive_failures
    _circuit_breaker: dict[str, int] = defaultdict(int)
    # Per-content active job count: content_id -> count
    _content_concurrency: dict[UUID, int] = defaultdict(int)
    # Global active job count
    _global_active_jobs: int = 0

    def __init__(
        self,
        job_repo: PipelineJobRepository,
        log_repo: PipelineStageLogRepository,
        registry: StageRegistry | None = None,
        publisher: EventPublisher | None = None,
        # Retry knobs are injectable so tests can run them with zero delay.
        max_attempts: int = MAX_STAGE_ATTEMPTS,
        backoff_base: float = BACKOFF_BASE_SECONDS,
        backoff_cap: float = BACKOFF_CAP_SECONDS,
        # Worker identity for leases
        worker_id: str | None = None,
    ) -> None:
        self.job_repo = job_repo
        self.log_repo = log_repo
        self.registry = registry or default_registry
        self.publisher = publisher or get_event_publisher()
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.worker_id = worker_id or f"worker-{os.getpid()}"

        # Build the port instances once, selected by settings.
        self._ports = self._build_ports()
        # Ensure work root exists
        os.makedirs(settings.PIPELINE_WORK_ROOT, exist_ok=True)
        os.makedirs(settings.PIPELINE_QUARANTINE_ROOT, exist_ok=True)

    # ------------------------------------------------------------------
    # Port factory (selected by MEDIA_PIPELINE_ADAPTERS).
    # ------------------------------------------------------------------
    def _build_ports(self) -> dict[str, Any]:
        """Build the port instances based on the active adapter selection."""
        if settings.MEDIA_PIPELINE_ADAPTERS == "ffmpeg":
            from app.core.ffmpeg import (  # type: ignore[attr-defined]
                FFmpegMetadataExtractor,
                FFmpegMultiBitrateEncoder,
                FFmpegPackager,
                FFmpegThumbnailGenerator,
            )
            from app.core.stages import ClamavScanner, StubCDN, StubObjectStorage

            return {
                "virus_scanner": ClamavScanner(),
                "metadata_extractor": FFmpegMetadataExtractor(),
                "thumbnail_generator": FFmpegThumbnailGenerator(
                    memory_limit_bytes=settings.PIPELINE_MAX_MEMORY_BYTES,
                ),
                "encoder": FFmpegMultiBitrateEncoder(
                    cpu_threads=settings.PIPELINE_MAX_CPU_THREADS,
                    max_output_bytes=settings.PIPELINE_MAX_OUTPUT_BYTES,
                    max_duration_seconds=settings.PIPELINE_MAX_DURATION_SECONDS,
                    memory_limit_bytes=settings.PIPELINE_MAX_MEMORY_BYTES,
                ),
                "packager": FFmpegPackager(
                    memory_limit_bytes=settings.PIPELINE_MAX_MEMORY_BYTES,
                ),
                "object_storage": StubObjectStorage(),  # S3 adapter TBD
                "cdn": StubCDN(),
            }
        # Default: stubs
        from app.core.stages import (
            StubCDN,
            StubMetadataExtractor,
            StubMultiBitrateEncoder,
            StubObjectStorage,
            StubPackager,
            StubThumbnailGenerator,
            StubVirusScanner,
        )

        return {
            "virus_scanner": StubVirusScanner(),
            "metadata_extractor": StubMetadataExtractor(),
            "thumbnail_generator": StubThumbnailGenerator(),
            "encoder": StubMultiBitrateEncoder(),
            "packager": StubPackager(),
            "object_storage": StubObjectStorage(),
            "cdn": StubCDN(),
        }

    # ------------------------------------------------------------------
    # Concurrency / lease helpers.
    # ------------------------------------------------------------------
    async def _check_concurrency_limits(self, content_id: UUID) -> None:
        """Enforce global and per-content concurrency limits."""
        if settings.PIPELINE_MAX_GLOBAL_JOBS > 0:
            if self._global_active_jobs >= settings.PIPELINE_MAX_GLOBAL_JOBS:
                raise ConcurrencyLimitExceeded(
                    f"global job limit ({settings.PIPELINE_MAX_GLOBAL_JOBS}) reached"
                )
        if settings.PIPELINE_MAX_JOBS_PER_CONTENT > 0:
            if self._content_concurrency[content_id] >= settings.PIPELINE_MAX_JOBS_PER_CONTENT:
                raise ConcurrencyLimitExceeded(
                    f"per-content job limit ({settings.PIPELINE_MAX_JOBS_PER_CONTENT}) reached for content {content_id}"
                )

    async def _acquire_lease(self, job: PipelineJob) -> bool:
        """Try to acquire a lease on the job. Returns True on success."""
        now = datetime.now(UTC)
        # If we already hold the lease, refresh it.
        if job.leased_by == self.worker_id:
            job.leased_at = now  # type: ignore[assignment]
            await self.job_repo.save(job)
            return True

        # Check for stale lease (another worker died).
        if job.leased_at is not None:
            lease_age = (now - job.leased_at).total_seconds()
            if lease_age < settings.PIPELINE_JOB_LEASE_SECONDS:
                # Lease is still valid and held by someone else.
                return False
            # Lease is stale — we can steal it.
            logger.warning(
                "stealing stale lease on job %s (held by %s, age %.1fs)",
                job.id,
                job.leased_by,
                lease_age,
            )

        # Acquire the lease.
        job.leased_by = self.worker_id
        job.leased_at = now  # type: ignore[assignment]
        await self.job_repo.save(job)
        return True

    async def _release_lease(self, job: PipelineJob) -> None:
        """Release our lease on the job."""
        if job.leased_by == self.worker_id:
            job.leased_by = None
            job.leased_at = None  # type: ignore[assignment]
            await self.job_repo.save(job)

    async def _heartbeat_lease(self, job: PipelineJob) -> None:
        """Refresh the lease timestamp."""
        if job.leased_by == self.worker_id:
            job.leased_at = datetime.now(UTC)  # type: ignore[assignment]
            await self.job_repo.save(job)

    def _increment_concurrency(self, content_id: UUID) -> None:
        self._global_active_jobs += 1
        self._content_concurrency[content_id] += 1

    def _decrement_concurrency(self, content_id: UUID) -> None:
        self._global_active_jobs = max(0, self._global_active_jobs - 1)
        self._content_concurrency[content_id] = max(0, self._content_concurrency[content_id] - 1)

    # ------------------------------------------------------------------
    # Circuit breaker helpers.
    # ------------------------------------------------------------------
    def _check_circuit_breaker(self, stage_name: str) -> None:
        """Raise if the circuit breaker is open for this stage."""
        threshold = settings.PIPELINE_CIRCUIT_BREAKER_THRESHOLD
        if self._circuit_breaker[stage_name] >= threshold:
            raise CircuitBreakerOpen(
                f"circuit breaker open for stage {stage_name} "
                f"({self._circuit_breaker[stage_name]} consecutive failures)"
            )

    def _record_stage_success(self, stage_name: str) -> None:
        """Reset the circuit breaker counter on success."""
        self._circuit_breaker[stage_name] = 0

    def _record_stage_failure(self, stage_name: str) -> None:
        """Increment the circuit breaker counter on failure."""
        self._circuit_breaker[stage_name] += 1

    # ------------------------------------------------------------------
    # Disk quota helper.
    # ------------------------------------------------------------------
    def _check_disk_quota(self, job: PipelineJob) -> None:
        """Best-effort check of per-job disk usage against quota."""
        if settings.PIPELINE_DISK_QUOTA_BYTES <= 0:
            return
        work_root = settings.PIPELINE_WORK_ROOT
        quarantine_root = settings.PIPELINE_QUARANTINE_ROOT
        job_id_str = str(job.id)
        total = 0
        for root in (work_root, quarantine_root):
            job_dir = os.path.join(root, job_id_str)
            if os.path.isdir(job_dir):
                for dirpath, _, filenames in os.walk(job_dir):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        try:
                            total += os.path.getsize(fp)
                        except OSError:
                            pass
        if total > settings.PIPELINE_DISK_QUOTA_BYTES:
            # We don't fail the job here — just log. The real enforcement
            # happens in the ffmpeg adapters via output size caps.
            logger.warning(
                "job %s disk usage %d bytes exceeds quota %d",
                job.id,
                total,
                settings.PIPELINE_DISK_QUOTA_BYTES,
            )

    # ------------------------------------------------------------------
    # Cleanup helper.
    # ------------------------------------------------------------------
    def _cleanup_job_dirs(self, job: PipelineJob) -> None:
        """Best-effort removal of per-job work and quarantine directories."""
        job_id_str = str(job.id)
        for root in (settings.PIPELINE_WORK_ROOT, settings.PIPELINE_QUARANTINE_ROOT):
            job_dir = os.path.join(root, job_id_str)
            if os.path.isdir(job_dir):
                shutil.rmtree(job_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # start_job
    # ------------------------------------------------------------------
    async def start_job(
        self,
        *,
        content_id: UUID,
        upload_session_id: UUID,
        storage_key: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> PipelineJob:
        """Create a pending pipeline job for an uploaded piece of content.

        Idempotent on ``idempotency_key`` (auto-generated from upload_session_id
        if not provided). Also idempotent on ``upload_session_id`` for backward
        compatibility.
        """
        # Generate a default idempotency key from upload_session_id for backward
        # compatibility with existing callers/tests.
        if idempotency_key is None:
            idempotency_key = f"upload-{upload_session_id}"

        # Check idempotency key first (unique index at DB level too).
        existing_by_key = await self.job_repo.get_by_idempotency_key(idempotency_key)
        if existing_by_key is not None:
            return existing_by_key

        # Back-compat: also check upload_session_id.
        existing_by_upload = await self.job_repo.get_by_upload_session(upload_session_id)
        if existing_by_upload is not None:
            return existing_by_upload

        # Concurrency limits.
        await self._check_concurrency_limits(content_id)

        job = PipelineJob(
            content_id=content_id,
            upload_session_id=upload_session_id,
            idempotency_key=idempotency_key,
            status=PipelineJobStatus.PENDING,
            stage_versions={},
            retries=0,
        )
        await self.job_repo.create(job)

        # Seed the job context with everything every stage needs. Ports are
        # injected here so the stages stay pure functions of ``ctx`` and the
        # orchestrator controls which adapters are used (selected by settings).
        ctx = self._build_context(job, storage_key=storage_key)
        if context:
            ctx.update(context)
        # Persist ctx without the injected ports (they are re-injected on
        # advance()); port objects are not JSON-serializable.
        job.context = {k: v for k, v in ctx.items() if not _is_port(v)}  # type: ignore[assignment]
        await self.job_repo.save(job)

        logger.info(
            "started pipeline job %s for content %s (upload %s, idempotency %s)",
            job.id,
            content_id,
            upload_session_id,
            idempotency_key,
        )
        # The job row commits here; outbox events enqueued later in the
        # lifecycle commit with the stage that produced them.
        await self.job_repo.session.commit()
        return job

    def _build_context(self, job: PipelineJob, *, storage_key: str) -> dict[str, Any]:
        """Assemble the per-job context dict with injected ports."""
        return {
            "job_id": str(job.id),
            "content_id": str(job.content_id),
            "upload_session_id": str(job.upload_session_id),
            "storage_key": storage_key,
            "quarantine_root": settings.PIPELINE_QUARANTINE_ROOT,
            "work_root": settings.PIPELINE_WORK_ROOT,
            "stage_timeout_seconds": settings.PIPELINE_STAGE_TIMEOUT_SECONDS,
            "max_cpu_threads": settings.PIPELINE_MAX_CPU_THREADS,
            "max_output_bytes": settings.PIPELINE_MAX_OUTPUT_BYTES,
            # Ports — selected by MEDIA_PIPELINE_ADAPTERS in _build_ports().
            **self._ports,
            "bitrates": [400, 800, 1200, 2400, 4800],
        }

    # ------------------------------------------------------------------
    # advance — run the next stage (with retry + DLQ + hardening).
    # ------------------------------------------------------------------
    async def advance(self, job_id: UUID) -> PipelineJob:
        """Advance the job through as many stages as currently succeed.

        Runs stages in order starting after the last completed one. Stops when
        a stage exhausts retries (job -> failed + DLQ), when the circuit
        breaker is open, when total retry time cap is hit, or when all stages
        complete (job -> completed). Safe to call repeatedly.
        """
        job = await self.job_repo.get(job_id)
        if job is None:
            raise PipelineError(f"pipeline job {job_id} not found")
        if job.status == PipelineJobStatus.COMPLETED:
            return job
        if job.status == PipelineJobStatus.FAILED:
            raise PipelineError(f"pipeline job {job_id} already failed")

        # Acquire lease before doing any work.
        acquired = await self._acquire_lease(job)
        if not acquired:
            raise LeaseAcquisitionFailed(
                f"could not acquire lease on job {job_id} (held by {job.leased_by})"
            )

        # Refresh context from persisted JSONB + re-inject current ports.
        ctx = getattr(job, "context", None)
        if not ctx:
            await self._release_lease(job)
            raise PipelineError(f"pipeline job {job_id} has no context")
        ctx = _rehydrate_context(job, dict(ctx), self._ports)

        # Track per-job total retry time across all stages.
        # We store the cumulative retry time in ctx so it persists across advances.
        total_retry_time = ctx.get("_total_retry_time_seconds", 0.0)
        max_total_retry = settings.PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS
        if total_retry_time >= max_total_retry:
            await self._release_lease(job)
            await self._fail_job(
                job,
                job.current_stage or "unknown",  # type: ignore[arg-type]
                f"total retry time {total_retry_time:.1f}s exceeds cap {max_total_retry}s",
            )
            self._cleanup_job_dirs(job)
            return job

        # Determine which stages are already done.
        done = set(job.stage_versions.keys())

        job.status = PipelineJobStatus.RUNNING  # type: ignore[assignment]
        if job.started_at is None:
            job.started_at = datetime.now(UTC)  # type: ignore[unreachable]
        await self.job_repo.save(job)

        # Increment concurrency counters now that we're actively running.
        self._increment_concurrency(job.content_id)
        try:
            for stage_name in self.registry.order:
                if stage_name in done:
                    continue

                # Circuit breaker check.
                self._check_circuit_breaker(stage_name)

                stage = self.registry.get(stage_name)
                job.current_stage = stage_name  # type: ignore[assignment]
                # Persist the (port-stripped) ctx so resume works mid-stage.
                job.context = {k: v for k, v in ctx.items() if not _is_port(v)}  # type: ignore[assignment]
                await self.job_repo.save(job)

                # Heartbeat the lease before a potentially long stage run.
                await self._heartbeat_lease(job)

                try:
                    ctx, stage_retry_time = await self._run_stage_with_retries(
                        job, stage, ctx, total_retry_time
                    )
                    total_retry_time += stage_retry_time
                    ctx["_total_retry_time_seconds"] = total_retry_time
                except PipelineNonRetryable as exc:
                    # Immediate, non-retryable failure (e.g. virus). Fail the job.
                    await self._record_stage(
                        job, stage.name, PipelineStageStatus.FAILED, 0, str(exc)
                    )
                    await self._fail_job(job, stage.name, str(exc))
                    self._cleanup_job_dirs(job)
                    return job
                except CircuitBreakerOpen as exc:
                    # Circuit breaker open — fail the job immediately.
                    await self._record_stage(
                        job, stage.name, PipelineStageStatus.FAILED, 0, str(exc)
                    )
                    await self._fail_job(job, stage.name, str(exc))
                    self._cleanup_job_dirs(job)
                    return job
                except TotalRetryTimeExceeded as exc:
                    await self._record_stage(
                        job, stage.name, PipelineStageStatus.FAILED, 0, str(exc)
                    )
                    await self._fail_job(job, stage.name, str(exc))
                    self._cleanup_job_dirs(job)
                    return job
                except PipelineError:
                    # Critical stage exhausted retries; _run_stage_with_retries
                    # already failed the job and emitted DLQ event.
                    self._cleanup_job_dirs(job)
                    return job
                except asyncio.CancelledError:
                    # Orchestrator shutdown / worker cancellation mid-stage:
                    # the subprocess was killed by run_process; remove the
                    # job's temp files so no half-baked media lingers (#218).
                    self._cleanup_job_dirs(job)
                    raise

                # _run_stage_with_retries returns an updated ctx on success or after
                # skipping a non-critical stage. If a critical stage exhausted
                # retries it fails the job (DLQ) and sets status=FAILED inside.
                if job.status == PipelineJobStatus.FAILED:
                    self._cleanup_job_dirs(job)
                    return job

                # Stage succeeded: snapshot its output into stage_versions.
                job.stage_versions = {  # type: ignore[assignment]
                    **job.stage_versions,
                    stage_name: {"completed_at": datetime.now(UTC).isoformat()},
                }
                job.retries = 0  # type: ignore[assignment]
                # Persist ctx (without ports) so a later advance() can resume.
                job.context = {k: v for k, v in ctx.items() if not _is_port(v)}  # type: ignore[assignment]
                await self.job_repo.save(job)

                # Reset circuit breaker on success.
                self._record_stage_success(stage_name)

                # Disk quota check after each stage.
                self._check_disk_quota(job)

                # Emit the stage's success event via the transactional outbox.
                if stage.success_event:
                    await self.job_repo.enqueue_event(
                        topic=stage.success_event,
                        event_key=str(job.id),
                        payload={
                            "job_id": str(job.id),
                            "content_id": str(job.content_id),
                            "stage": stage_name,
                        },
                    )

            # HLS + DASH packaging are reported as one packaged event.
            if "hls_package" in job.stage_versions and "dash_package" in job.stage_versions:
                await self.job_repo.enqueue_event(
                    topic="content.packaged",
                    event_key=str(job.id),
                    payload={
                        "job_id": str(job.id),
                        "content_id": str(job.content_id),
                        "hls_url": ctx.get("hls_url"),
                        "dash_url": ctx.get("dash_url"),
                    },
                )

            job.status = PipelineJobStatus.COMPLETED  # type: ignore[assignment]
            job.current_stage = None  # type: ignore[assignment]
            job.error = None  # type: ignore[assignment]
            await self.job_repo.save(job)
            await self.job_repo.enqueue_event(
                topic="content.published",
                event_key=str(job.id),
                payload={
                    "job_id": str(job.id),
                    "content_id": str(job.content_id),
                },
            )
            logger.info("pipeline job %s completed", job.id)
            # Job state + all outbox events enqueued above commit atomically.
            await self.job_repo.session.commit()
            # Cleanup on success.
            self._cleanup_job_dirs(job)
            return job
        finally:
            # Always decrement concurrency and release lease.
            self._decrement_concurrency(job.content_id)
            await self._release_lease(job)
            # Persist the lease release so the job is immediately resumable.
            await self.job_repo.session.commit()

    async def _run_stage_with_retries(
        self,
        job: PipelineJob,
        stage: Stage,
        ctx: dict[str, Any],
        total_retry_time_so_far: float,
    ) -> tuple[dict[str, Any], float]:
        """Run one stage with exponential-backoff retries.

        Returns (updated_ctx, stage_retry_time) on success. On retry exhaustion
        of a critical stage, fails the job (DLQ) and raises. On retry exhaustion
        of a non-critical stage, logs a skip and returns (ctx, retry_time).
        Raises CircuitBreakerOpen, TotalRetryTimeExceeded as appropriate.
        """
        attempt = 0
        last_error: Exception | None = None
        stage_retry_time = 0.0
        max_total_retry = settings.PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS

        while attempt < self.max_attempts:
            attempt += 1
            job.retries = attempt  # type: ignore[assignment]
            await self.job_repo.save(job)

            # Heartbeat lease before each attempt.
            await self._heartbeat_lease(job)

            start = time.monotonic()
            try:
                ctx = await stage.run(ctx)
                duration_ms = int((time.monotonic() - start) * 1000)
                await self._record_stage(
                    job,
                    stage.name,
                    PipelineStageStatus.SUCCESS,
                    duration_ms,
                    f"attempt {attempt} ok",
                )
                return ctx, stage_retry_time
            except PipelineNonRetryable:
                raise
            except Exception as exc:  # noqa: BLE001 — surface via stage log
                last_error = exc
                duration_ms = int((time.monotonic() - start) * 1000)
                await self._record_stage(
                    job,
                    stage.name,
                    PipelineStageStatus.FAILED,
                    duration_ms,
                    f"attempt {attempt} failed: {exc}",
                )
                logger.warning(
                    "stage %s attempt %d/%d failed for job %s: %s",
                    stage.name,
                    attempt,
                    self.max_attempts,
                    job.id,
                    exc,
                )
                stage_retry_time += time.monotonic() - start

                # Check total retry time cap.
                if total_retry_time_so_far + stage_retry_time >= max_total_retry:
                    raise TotalRetryTimeExceeded(
                        f"job {job.id} total retry time would exceed {max_total_retry}s"
                    )

                if attempt < self.max_attempts:
                    delay = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_cap)
                    stage_retry_time += delay
                    # Check total retry time cap including the backoff delay.
                    if total_retry_time_so_far + stage_retry_time >= max_total_retry:
                        raise TotalRetryTimeExceeded(
                            f"job {job.id} total retry time would exceed {max_total_retry}s"
                        )
                    await asyncio.sleep(delay)

        # Retries exhausted.
        # Record circuit breaker failure.
        self._record_stage_failure(stage.name)

        if stage.critical:
            # Fail the job and emit the DLQ event. ``advance()`` checks
            # ``job.status == FAILED`` right after us and returns.
            await self._fail_job(
                job,
                stage.name,
                f"critical stage {stage.name} exhausted {self.max_attempts} "
                f"attempts: {last_error}",
            )
            raise PipelineError(f"job {job.id} failed at critical stage {stage.name}")
        # Non-critical: skip and continue.
        await self._record_stage(
            job,
            stage.name,
            PipelineStageStatus.SKIPPED,
            0,
            f"skipped after {self.max_attempts} failed attempts: {last_error}",
        )
        return ctx, stage_retry_time

    async def _fail_job(self, job: PipelineJob, stage_name: str, message: str) -> PipelineJob:
        """Mark a job failed and emit the ``content.pipeline.failed`` DLQ event.

        Includes a ``dlq_key`` for downstream deduplication.
        """
        job.status = PipelineJobStatus.FAILED  # type: ignore[assignment]
        job.current_stage = stage_name  # type: ignore[assignment]
        job.error = message  # type: ignore[assignment]
        await self.job_repo.save(job)
        dlq_key = f"{job.id}:{stage_name}"
        await self.job_repo.enqueue_event(
            topic="content.pipeline.failed",
            event_key=str(job.id),
            payload={
                "job_id": str(job.id),
                "content_id": str(job.content_id),
                "stage": stage_name,
                "error": message,
                "dlq_key": dlq_key,
            },
        )
        logger.error("pipeline job %s FAILED at stage %s: %s", job.id, stage_name, message)
        # FAILED status + DLQ outbox event commit atomically.
        await self.job_repo.session.commit()
        return job

    async def _record_stage(
        self,
        job: PipelineJob,
        stage_name: str,
        status: PipelineStageStatus,
        duration_ms: int,
        message: str,
    ) -> PipelineStageLog:
        log = PipelineStageLog(
            job_id=job.id,
            stage=stage_name,
            status=status,
            duration_ms=duration_ms,
            message=message,
        )
        await self.log_repo.record(log)
        return log

    # ------------------------------------------------------------------
    # Stale lease recovery (called by a background worker / cron).
    # ------------------------------------------------------------------
    async def recover_stale_jobs(self) -> int:
        """Find jobs with stale leases and reset them to RUNNING for retry.

        Returns the number of jobs recovered.
        """
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(seconds=settings.PIPELINE_JOB_LEASE_SECONDS)
        stale_jobs = await self.job_repo.list_stale(cutoff, status=PipelineJobStatus.RUNNING)
        recovered = 0
        for job in stale_jobs:
            # Only recover if the job is still in a retryable state.
            if job.status == PipelineJobStatus.RUNNING and job.current_stage:
                job.leased_by = None
                job.leased_at = None  # type: ignore[assignment]
                job.retries = 0  # type: ignore[assignment]  # reset retries for the current stage
                await self.job_repo.save(job)
                logger.info("recovered stale job %s (was leased by %s)", job.id, job.leased_by)
                recovered += 1
        if recovered:
            await self.job_repo.session.commit()
        return recovered

    # ------------------------------------------------------------------
    # Outbox drain (background worker).
    # ------------------------------------------------------------------

    async def drain_outbox(self) -> int:
        """Publish PENDING outbox rows to the bus; mark them dispatched.

        A row whose publish fails stays PENDING and is retried on the next
        drain (at-least-once; consumers dedupe on the event key). Returns the
        number of rows processed.
        """
        rows = await self.job_repo.pending_events(limit=settings.OUTBOX_BATCH_SIZE)
        for row in rows:
            try:
                await self.publisher.publish(
                    Event(
                        topic=row.topic,
                        key=row.event_key or "",
                        payload=row.payload,
                    )
                )
            except Exception:  # noqa: BLE001 - keep row pending for retry
                logger.exception(
                    "outbox publish failed for event %s (topic=%s); will retry",
                    row.id,
                    row.topic,
                )
                continue
            await self.job_repo.mark_dispatched(row.id)
        # Persist dispatch state before the next drain cycle.
        await self.job_repo.session.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Legacy compatibility (kept for the old /media/transcode route/tests).
    # ------------------------------------------------------------------

    async def start_transcoding(self, content_id: UUID, source_url: str):
        """Legacy entry point kept for import/test compatibility."""
        from app.repositories import TranscodingJobRepository

        return await TranscodingJobRepository(self.job_repo.session).create(content_id, source_url)

    async def get_job_status(self, content_id: UUID):
        """Legacy entry point kept for import/test compatibility."""
        from app.repositories import TranscodingJobRepository

        return await TranscodingJobRepository(self.job_repo.session).get_by_content_id(content_id)
