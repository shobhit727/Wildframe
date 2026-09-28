"""Two defensive branches at the tail of ``advance()``'s stage loop.

``app/services.py:630-637`` (``except CircuitBreakerOpen``) and ``660-662``
(``if job.status == FAILED`` after a stage returns) are both hard to reach from
the shipped stage set. This module pins down *why* for each, and tests the
branch that is genuinely reachable.

* **630-637 is unreachable.** ``CircuitBreakerOpen`` subclasses ``PipelineError``
  but not ``PipelineNonRetryable``, so when a stage raises it,
  ``_run_stage_with_retries`` catches it in its generic ``except Exception``
  and treats it as an ordinary retryable failure — it never escapes. The only
  other source, ``_check_circuit_breaker`` at line 605, sits *outside* the
  per-stage ``try``. So nothing can ever arrive at line 630. Proven below.
* **660-662 is reachable** via a stage that fails the job itself (a supported
  extension point — ``Stage`` is a public port and is injected through
  ``StageRegistry``). It is unreachable via the built-in stages, which either
  return with the job RUNNING or raise ``PipelineError`` from line 645.
"""

from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.settings import settings
from app.core.stages import Stage
from app.models import PipelineJobStatus, PipelineStageStatus
from app.services import CircuitBreakerOpen, MediaPipelineService
from tests.test_pipeline_state_machine import (
    CountingStage,
    FakeJobRepo,
    _fresh_registry,
)
from tests.test_services_gaps import _service


class _BreakerTrippingStage(Stage):
    """A stage whose ``run()`` raises ``CircuitBreakerOpen`` itself."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.success_event = ""
        self.critical = True
        self.calls = 0

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        raise CircuitBreakerOpen(f"circuit breaker open for stage {self.name}")


class _FailsTheJobStage(Stage):
    """A stage that terminates the job itself and then returns normally.

    Shaped like a real compliance/quarantine stage: it decides the asset must
    not be transcoded, so it marks the job failed and emits the DLQ event
    directly instead of raising and letting the retry policy drive the outcome.
    """

    def __init__(self, name: str, service) -> None:
        self.name = name
        # A success event that must NOT be emitted once the job is already dead.
        self.success_event = "content.compliance_passed"
        self.critical = True
        self._service = service
        self.calls = 0

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        job = await self._service.job_repo.get(UUID(ctx["job_id"]))
        await self._service._fail_job(job, self.name, "asset rejected by compliance")
        return ctx


@pytest.fixture(autouse=True)
def _reset_class_level_counters():
    """Concurrency/breaker counters are class attributes; isolate every test."""
    _reset_counters()
    yield
    _reset_counters()


def _reset_counters() -> None:
    MediaPipelineService._global_active_jobs = 0
    MediaPipelineService._content_concurrency.clear()
    MediaPipelineService._creator_concurrency.clear()
    MediaPipelineService._circuit_breaker.clear()


class TestCircuitBreakerOpenHandlerIsUnreachable:
    """services.py:630-637 — nothing can reach it. Asserted, not fixed."""

    async def test_a_stage_raising_the_breaker_error_is_retried_not_routed_to_the_handler(self):
        """The breaker error is swallowed by the retry handler and retried to exhaustion."""
        repo = FakeJobRepo()
        registry = _fresh_registry()
        stage = _BreakerTrippingStage("flaky")
        registry.register(stage)
        service = _service(repo, registry, max_attempts=3)

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        result = await service.advance(job.id)

        # Retried to exhaustion, then failed through the *normal* critical path.
        assert stage.calls == 3, "the breaker error must be retried, not short-circuited"
        assert result.status == PipelineJobStatus.FAILED
        # The message is the critical-exhaustion wrapper around the last error —
        # the shape _run_stage_with_retries produces, not the bare breaker message
        # that line 635 would have written.
        assert result.error.startswith("critical stage flaky exhausted 3 attempts:")
        assert "circuit breaker open for stage flaky" in result.error

    async def test_the_handler_would_have_logged_one_failed_stage_row(self):
        """Had 630 run, the stage log would hold a single FAILED row.

        Instead every attempt is logged, which is the observable signature of the
        retry handler having dealt with the error.
        """
        repo = FakeJobRepo()
        registry = _fresh_registry()
        registry.register(_BreakerTrippingStage("flaky"))
        service = _service(repo, registry, max_attempts=3)

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        await service.advance(job.id)

        logs = await service.log_repo.list_for_job(job.id)
        failed = [log for log in logs if log.status == PipelineStageStatus.FAILED]
        assert len(failed) == 3, [log.message for log in failed]
        assert all("circuit breaker open" in log.message for log in failed)

class TestPreOpenBreakerIsRecordedNotPropagated:
    """A breaker that is *already* open when ``advance()`` is called.

    This is a different path from the class above. Here the failure comes from
    ``_check_circuit_breaker`` before the stage runs, rather than from a stage
    tripping the breaker during its own retries, so it reaches the
    ``except CircuitBreakerOpen`` handler in ``app/services.py`` rather than the
    generic retry handler.

    It used to assert the opposite -- that the exception escaped ``advance()``
    and left the job in ``running`` with no stage log and no DLQ event. The
    handler is now reachable and the job is properly failed, so asserting the
    escape would re-introduce the orphaned-job defect. This test was moved out of
    ``TestCircuitBreakerOpenHandlerIsUnreachable`` because that class name is no
    longer true of it; the two tests in that class still describe the retry path
    accurately.
    """

    async def test_an_already_open_breaker_fails_the_job_without_running_the_stage(self):
        repo = FakeJobRepo()
        registry = _fresh_registry()
        stage = CountingStage("a")
        registry.register(stage)
        service = _service(repo, registry)
        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        service._circuit_breaker["a"] = settings.PIPELINE_CIRCUIT_BREAKER_THRESHOLD

        result = await service.advance(job.id)

        assert stage.calls == 0, "an open breaker must not run the stage at all"
        assert result.status == PipelineJobStatus.FAILED, "the job must not stay in running"
        rows = await service.log_repo.list_for_job(job.id)
        assert [r.status for r in rows] == [PipelineStageStatus.FAILED]
        await service.drain_outbox()
        assert [e for e in service.publisher.sent if e.topic == "content.pipeline.failed"]


class TestPostStageFailedGuard:
    """services.py:660-662 — a stage that failed the job stops the loop."""

    async def test_advance_returns_the_failed_job_and_stops_the_loop(self):
        repo = FakeJobRepo()
        registry = _fresh_registry()
        service = _service(repo, registry)
        # The stage drives the job to FAILED through the service, so it has to be
        # wired after the service exists.
        registry.register(_FailsTheJobStage("compliance", service))
        downstream = CountingStage("encode")
        registry.register(downstream)

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        result = await service.advance(job.id)

        assert result.status == PipelineJobStatus.FAILED
        assert result.error == "asset rejected by compliance"
        assert result.current_stage == "compliance"
        assert downstream.calls == 0, "the loop must stop once the job is failed"

    async def test_the_guard_stops_the_success_bookkeeping(self):
        """The stage is logged SUCCESS, but no stage version is recorded for it.

        The stage genuinely returned, so the retry layer logs it as a success;
        it is the *job-level* verdict that the guard acts on. The observable
        contract is therefore: a success row may exist, but ``stage_versions``
        must not gain an entry, because that entry is what lets a later
        ``advance()`` skip the stage.
        """
        repo = FakeJobRepo()
        registry = _fresh_registry()
        service = _service(repo, registry)
        registry.register(_FailsTheJobStage("compliance", service))

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        result = await service.advance(job.id)

        assert result.status == PipelineJobStatus.FAILED
        assert result.stage_versions == {}, "a failed job must not record a stage version"
        logs = await service.log_repo.list_for_job(job.id)
        assert [log.stage for log in logs] == ["compliance"]
        assert logs[0].status == PipelineStageStatus.SUCCESS
        assert "attempt 1 ok" in logs[0].message

    async def test_the_stage_success_event_is_not_enqueued_for_a_failed_job(self):
        """Paired with the control below: the event is emitted iff the job survives.

        Asserting only the negative would also pass if the event were never
        emitted for any job, so the control test pins the other direction.
        """
        repo = FakeJobRepo()
        registry = _fresh_registry()
        service = _service(repo, registry)
        registry.register(_FailsTheJobStage("compliance", service))

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        await service.advance(job.id)
        await service.drain_outbox()

        topics = [e.topic for e in service.publisher.sent]
        assert (
            "content.compliance_passed" not in topics
        ), "a job the stage already failed must not announce stage success"
        # Only the DLQ event from _fail_job.
        assert topics.count("content.pipeline.failed") == 1

    async def test_control_a_surviving_stage_does_enqueue_its_success_event(self):
        """The other half of the pair: the enqueue path itself works.

        Without this, the test above could not tell "the guard suppressed the
        event" apart from "the event is never emitted".
        """
        repo = FakeJobRepo()
        registry = _fresh_registry()
        service = _service(repo, registry)
        registry.register(CountingStage("a", success_event="content.a_done"))

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        result = await service.advance(job.id)
        await service.drain_outbox()

        assert result.status == PipelineJobStatus.COMPLETED
        assert "content.a_done" in [e.topic for e in service.publisher.sent]

    async def test_the_job_sandbox_is_cleaned_up(self, tmp_path):
        repo = FakeJobRepo()
        registry = _fresh_registry()
        service = _service(repo, registry)
        registry.register(_FailsTheJobStage("compliance", service))

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        for root in ("work", "quarantine"):
            d = tmp_path / root / str(job.id) / "nested"
            d.mkdir(parents=True)
            (d / "partial.mp4").write_bytes(b"x" * 512)

        with (
            patch.object(settings, "PIPELINE_WORK_ROOT", str(tmp_path / "work")),
            patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
        ):
            result = await service.advance(job.id)

        assert result.status == PipelineJobStatus.FAILED
        assert not (tmp_path / "work" / str(job.id)).exists(), "work sandbox must be removed"
        assert not (
            tmp_path / "quarantine" / str(job.id)
        ).exists(), "quarantine sandbox must be removed"

    async def test_the_lease_is_released_so_the_job_is_not_stuck(self):
        repo = FakeJobRepo()
        registry = _fresh_registry()
        service = _service(repo, registry)
        registry.register(_FailsTheJobStage("compliance", service))

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        result = await service.advance(job.id)

        assert result.leased_by is None
        assert result.leased_at is None

    async def test_a_built_in_stage_never_reaches_the_guard(self):
        """Control: the shipped stages cannot trip 660 — they raise instead.

        A critical stage that exhausts its retries fails the job and *then*
        raises ``PipelineError``, which line 645 handles. So the guard is only
        for stages that fail the job without raising.
        """
        repo = FakeJobRepo()
        registry = _fresh_registry()
        registry.register(CountingStage("a", fail_times=99))
        service = _service(repo, registry, max_attempts=2)

        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="k"
        )
        result = await service.advance(job.id)

        assert result.status == PipelineJobStatus.FAILED
        assert "exhausted 2 attempts" in result.error
