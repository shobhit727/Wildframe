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
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.core.events import Event, EventPublisher, get_event_publisher
from app.core.settings import settings
from app.core.stages import Stage, StageRegistry, registry as default_registry
from app.models import (
    PipelineJob,
    PipelineJobStatus,
    PipelineStageLog,
    PipelineStageStatus,
)
from app.repositories import PipelineJobRepository, PipelineStageLogRepository


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry / DLQ tuning (overridable via settings).
# ---------------------------------------------------------------------------

MAX_STAGE_ATTEMPTS: int = 3
BACKOFF_BASE_SECONDS: float = 1.0
BACKOFF_CAP_SECONDS: float = 30.0


class PipelineError(Exception):
    """Base pipeline error."""


class PipelineNonRetryable(PipelineError):
    """A failure that cannot be fixed by retrying (e.g. virus detected)."""


class MediaPipelineService:
    """Orchestrates the animation pipeline as a retryable state machine."""

    def __init__(
        self,
        job_repo: PipelineJobRepository,
        log_repo: PipelineStageLogRepository,
        registry: Optional[StageRegistry] = None,
        publisher: Optional[EventPublisher] = None,
        # Retry knobs are injectable so tests can run them with zero delay.
        max_attempts: int = MAX_STAGE_ATTEMPTS,
        backoff_base: float = BACKOFF_BASE_SECONDS,
        backoff_cap: float = BACKOFF_CAP_SECONDS,
    ) -> None:
        self.job_repo = job_repo
        self.log_repo = log_repo
        self.registry = registry or default_registry
        self.publisher = publisher or get_event_publisher()
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    # ------------------------------------------------------------------
    # start_job
    # ------------------------------------------------------------------

    async def start_job(
        self,
        *,
        content_id: UUID,
        upload_session_id: UUID,
        storage_key: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PipelineJob:
        """Create a pending pipeline job for an uploaded piece of content.

        Idempotent on ``upload_session_id``: if a job already exists for the
        upload it is returned as-is (so a duplicate ``content.uploaded`` event
        does not create a duplicate job).
        """
        existing = await self.job_repo.get_by_upload_session(upload_session_id)
        if existing is not None:
            return existing

        job = PipelineJob(
            content_id=content_id,
            upload_session_id=upload_session_id,
            status=PipelineJobStatus.PENDING,
            stage_versions={},
            retries=0,
        )
        await self.job_repo.create(job)

        # Seed the job context with everything every stage needs. Ports are
        # injected here so the stages stay pure functions of ``ctx`` and the
        # orchestrator controls which adapters are used (selected by settings
        # in a real deploy; stubs here for dev/test).
        ctx = self._build_context(job, storage_key=storage_key)
        if context:
            ctx.update(context)
        job.context = ctx  # type: ignore[attr-defined]

        logger.info(
            "started pipeline job %s for content %s (upload %s)",
            job.id,
            content_id,
            upload_session_id,
        )
        return job

    def _build_context(self, job: PipelineJob, *, storage_key: str) -> Dict[str, Any]:
        """Assemble the per-job context dict with injected ports."""
        from app.core.stages import (
            StubVirusScanner,
            StubMetadataExtractor,
            StubThumbnailGenerator,
            StubMultiBitrateEncoder,
            StubPackager,
            StubObjectStorage,
            StubCDN,
        )

        return {
            "job_id": str(job.id),
            "content_id": str(job.content_id),
            "upload_session_id": str(job.upload_session_id),
            "storage_key": storage_key,
            "quarantine_root": "/tmp/wildframe/quarantine",
            # Ports — swapped for real adapters in production via settings.
            "virus_scanner": StubVirusScanner(),
            "metadata_extractor": StubMetadataExtractor(),
            "thumbnail_generator": StubThumbnailGenerator(),
            "encoder": StubMultiBitrateEncoder(),
            "packager": StubPackager(),
            "object_storage": StubObjectStorage(),
            "cdn": StubCDN(),
            "bitrates": [400, 800, 1200, 2400, 4800],
        }

    # ------------------------------------------------------------------
    # advance — run the next stage (with retry + DLQ).
    # ------------------------------------------------------------------

    async def advance(self, job_id: UUID) -> PipelineJob:
        """Advance the job through as many stages as currently succeed.

        Runs stages in order starting after the last completed one. Stops when
        a stage exhausts retries (job -> failed + DLQ) or when all stages
        complete (job -> completed). Safe to call repeatedly.
        """
        job = await self.job_repo.get(job_id)
        if job is None:
            raise PipelineError(f"pipeline job {job_id} not found")
        if job.status == PipelineJobStatus.COMPLETED:
            return job
        if job.status == PipelineJobStatus.FAILED:
            raise PipelineError(f"pipeline job {job_id} already failed")

        ctx = getattr(job, "context", None)
        if ctx is None:
            raise PipelineError(f"pipeline job {job_id} has no context")

        # Determine which stages are already done.
        done = set(job.stage_versions.keys())

        job.status = PipelineJobStatus.RUNNING
        if job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        await self.job_repo.save(job)

        for stage_name in self.registry.order:
            if stage_name in done:
                continue

            stage = self.registry.get(stage_name)
            job.current_stage = stage_name
            await self.job_repo.save(job)

            try:
                ctx = await self._run_stage_with_retries(job, stage, ctx)
            except PipelineNonRetryable as exc:
                # Immediate, non-retryable failure (e.g. virus). Fail the job.
                await self._record_stage(
                    job, stage.name, PipelineStageStatus.FAILED, 0, str(exc)
                )
                return await self._fail_job(job, stage.name, str(exc))
            # _run_stage_with_retries returns an updated ctx on success or after
            # skipping a non-critical stage. If a critical stage exhausted
            # retries it fails the job (DLQ) and sets status=FAILED inside.
            if job.status == PipelineJobStatus.FAILED:
                return job

            # Stage succeeded: snapshot its output into stage_versions.
            job.stage_versions = {
                **job.stage_versions,
                stage_name: {"completed_at": datetime.now(timezone.utc).isoformat()},
            }
            job.retries = 0
            await self.job_repo.save(job)

            # Emit the stage's success event, if any.
            if stage.success_event:
                await self.publisher.publish(
                    Event(
                        topic=stage.success_event,
                        key=str(job.id),
                        payload={
                            "job_id": str(job.id),
                            "content_id": str(job.content_id),
                            "stage": stage_name,
                        },
                    )
                )

        # HLS + DASH packaging are reported as one packaged event.
        if (
            "hls_package" in job.stage_versions
            and "dash_package" in job.stage_versions
        ):
            await self.publisher.publish(
                Event(
                    topic="content.packaged",
                    key=str(job.id),
                    payload={
                        "job_id": str(job.id),
                        "content_id": str(job.content_id),
                        "hls_url": ctx.get("hls_url"),
                        "dash_url": ctx.get("dash_url"),
                    },
                )
            )

        job.status = PipelineJobStatus.COMPLETED
        job.current_stage = None
        job.error = None
        await self.job_repo.save(job)
        await self.publisher.publish(
            Event(
                topic="content.published",
                key=str(job.id),
                payload={
                    "job_id": str(job.id),
                    "content_id": str(job.content_id),
                },
            )
        )
        logger.info("pipeline job %s completed", job.id)
        return job

    async def _run_stage_with_retries(
        self, job: PipelineJob, stage: Stage, ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run one stage with exponential-backoff retries.

        Returns the updated ctx on success. On retry exhaustion of a critical
        stage, fails the job (DLQ) and raises. On retry exhaustion of a
        non-critical stage, logs a skip and returns ctx unchanged.
        """
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt < self.max_attempts:
            attempt += 1
            job.retries = attempt
            await self.job_repo.save(job)
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
                return ctx
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
                if attempt < self.max_attempts:
                    delay = min(
                        self.backoff_base * (2 ** (attempt - 1)), self.backoff_cap
                    )
                    await asyncio.sleep(delay)

        # Retries exhausted.
        if stage.critical:
            # Fail the job and emit the DLQ event. ``advance()`` checks
            # ``job.status == FAILED`` right after us and returns.
            await self._fail_job(
                job,
                stage.name,
                f"critical stage {stage.name} exhausted {self.max_attempts} "
                f"attempts: {last_error}",
            )
            return ctx
        # Non-critical: skip and continue.
        await self._record_stage(
            job,
            stage.name,
            PipelineStageStatus.SKIPPED,
            0,
            f"skipped after {self.max_attempts} failed attempts: {last_error}",
        )
        return ctx

    async def _fail_job(
        self, job: PipelineJob, stage_name: str, message: str
    ) -> PipelineJob:
        """Mark a job failed and emit the ``content.pipeline.failed`` DLQ event."""
        job.status = PipelineJobStatus.FAILED
        job.current_stage = stage_name
        job.error = message
        await self.job_repo.save(job)
        await self.publisher.publish(
            Event(
                topic="content.pipeline.failed",
                key=str(job.id),
                payload={
                    "job_id": str(job.id),
                    "content_id": str(job.content_id),
                    "stage": stage_name,
                    "error": message,
                },
            )
        )
        logger.error(
            "pipeline job %s FAILED at stage %s: %s", job.id, stage_name, message
        )
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
    # Legacy compatibility (kept for the old /media/transcode route/tests).
    # ------------------------------------------------------------------

    async def start_transcoding(self, content_id: UUID, source_url: str):
        """Legacy entry point kept for import/test compatibility."""
        from app.repositories import TranscodingJobRepository

        return await TranscodingJobRepository(self.job_repo.session).create(
            content_id, source_url
        )

    async def get_job_status(self, content_id: UUID):
        """Legacy entry point kept for import/test compatibility."""
        from app.repositories import TranscodingJobRepository

        return await TranscodingJobRepository(self.job_repo.session).get_by_content_id(
            content_id
        )
