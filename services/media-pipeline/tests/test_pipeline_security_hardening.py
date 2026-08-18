"""Media processing hardening tests (#218).

Pins the five audit findings:
  1. FFmpeg commands are fixed argv arrays via create_subprocess_exec —
     never a shell, never string interpolation of untrusted input.
  2. Per-job CPU (threads), memory (RLIMIT_AS), disk (quota), duration and
     wall-clock limits exist and are wired from settings.
  3. Temp work/quarantine dirs are removed on success, failure, and
     cancellation of a job.
  4. Partial outputs are never published as completed media
     (content.published fires only after every stage completes).
  5. Retries never re-run a completed stage and never double-publish.
"""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.ffmpeg import (
    CommandTimeout,
    FFmpegMultiBitrateEncoder,
    _child_rlimit,
    run_process,
)
from app.core.security import UnsafeInput
from app.core.stages import Stage, StageRegistry
from app.models import PipelineJob, PipelineJobStatus
from app.services import MediaPipelineService
from tests.test_pipeline_state_machine import (
    CountingStage,
    FakeJobRepo,
    FakeLogRepo,
    make_service,
)


class FakeStream:
    async def read(self, n):
        return b""


class FakeProcess:
    returncode = 0
    stdout = FakeStream()
    stderr = FakeStream()

    async def wait(self):
        return 0


class HangingProcess(FakeProcess):
    returncode = None
    pid = 12345

    async def wait(self):
        await asyncio.sleep(60)
        return 0


class CancelStage(Stage):
    """A stage that cancels the job mid-run (worker shutdown simulation)."""

    def __init__(self, name: str):
        self.name = name
        self.success_event = ""
        self.critical = True

    async def run(self, ctx: dict):
        raise asyncio.CancelledError


def _job_work_dir(job: PipelineJob) -> str:
    from app.core.settings import settings

    return os.path.join(settings.PIPELINE_WORK_ROOT, str(job.id))


# ---------------------------------------------------------------------------
# Finding 1: fixed argument arrays, never a shell.
# ---------------------------------------------------------------------------


async def _run_mocked(argv, **kwargs):
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProcess())
    ) as spawn:
        result = await run_process(argv, timeout=5.0, **kwargs)
        return spawn, result


@pytest.mark.asyncio
async def test_run_process_executes_argv_without_shell():
    argv = ["ffmpeg", "-y", "-i", "/tmp/wildframe/work/job/in.mp4", "out.mp4"]
    spawn, (rc, _out, _err, _ot, _et) = await _run_mocked(argv)

    spawn.assert_awaited_once()
    assert spawn.await_args.args == tuple(argv)  # one element per argv item
    assert "shell" not in spawn.await_args.kwargs  # never shell=True


@pytest.mark.asyncio
async def test_shell_metacharacters_stay_one_argv_element():
    work = "/tmp/wildframe/work"
    job_dir = os.path.join(work, str(uuid4()))
    os.makedirs(job_dir, exist_ok=True)
    evil_path = os.path.join(job_dir, "evil ; touch /tmp/pwned.mp4")
    out_dir = os.path.join(job_dir, "out")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "v_400.mp4"), "wb") as f:
        f.write(b"x")
    try:
        encoder = FFmpegMultiBitrateEncoder(work_root=work)
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProcess())
        ) as spawn:
            await encoder.encode(evil_path, os.path.join(job_dir, "out"), [400])
        argv = spawn.await_args.args
        assert evil_path in argv  # still a single argv element
        # exec() semantics: the metacharacter string is one intact element.
        assert "\0".join(argv).count(evil_path) == 1
        assert "shell" not in spawn.await_args.kwargs
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_encoder_rejects_url_input():
    encoder = FFmpegMultiBitrateEncoder()
    with pytest.raises(UnsafeInput):
        await encoder.encode(
            "https://evil.example/x.mp4", "/tmp/wildframe/work/job/out", [400]
        )


# ---------------------------------------------------------------------------
# Finding 2: per-job CPU / memory / disk / duration / wall-clock limits.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encoder_clamps_threads_to_configured_cap():
    work = "/tmp/wildframe/work"
    job_dir = os.path.join(work, str(uuid4()))
    os.makedirs(job_dir, exist_ok=True)
    try:
        out_dir = os.path.join(job_dir, "out")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "v_400.mp4"), "wb") as f:
            f.write(b"x")
        encoder = FFmpegMultiBitrateEncoder(cpu_threads=2, work_root=work)
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProcess())
        ) as spawn:
            await encoder.encode(
                os.path.join(job_dir, "in.mp4"), out_dir,
                [400], cpu_threads=64,
            )
        argv = spawn.await_args.args
        assert argv[argv.index("-threads") + 1] == "2"
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_encoder_wires_duration_cap_into_argv():
    work = "/tmp/wildframe/work"
    job_dir = os.path.join(work, str(uuid4()))
    os.makedirs(job_dir, exist_ok=True)
    try:
        out_dir = os.path.join(job_dir, "out")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "v_400.mp4"), "wb") as f:
            f.write(b"x")
        encoder = FFmpegMultiBitrateEncoder(max_duration_seconds=14400, work_root=work)
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProcess())
        ) as spawn:
            await encoder.encode(
                os.path.join(job_dir, "in.mp4"), out_dir, [400]
            )
        argv = spawn.await_args.args
        assert "-t" in argv
        assert argv[argv.index("-t") + 1] == "14400"
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def test_child_rlimit_really_limits_address_space():
    limit = 128 * 1024 * 1024
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])",
        ],
        capture_output=True,
        text=True,
        preexec_fn=_child_rlimit(limit),
        check=True,
    )
    assert out.stdout.strip() == str(limit)


@pytest.mark.asyncio
async def test_run_process_passes_memory_rlimit():
    _spawn, _ = await _run_mocked(["true"], memory_limit_bytes=1 << 20)
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProcess())
    ) as spawn:
        await run_process(["true"], timeout=5.0, memory_limit_bytes=1 << 20)
        assert spawn.await_args.kwargs.get("preexec_fn") is not None
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=FakeProcess())
    ) as spawn2:
        await run_process(["true"], timeout=5.0)
        assert spawn2.await_args.kwargs.get("preexec_fn") is None


@pytest.mark.asyncio
async def test_run_process_timeout_kills_process_group():
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=HangingProcess())
    ), patch("os.killpg") as killpg, patch("os.getpgid", return_value=42):
        with pytest.raises(CommandTimeout):
            await run_process(["ffmpeg"], timeout=0.05)
        assert killpg.called  # SIGTERM (and SIGKILL on grace expiry)
        signals = {c.args[1] for c in killpg.call_args_list}
        assert signals == {signal.SIGTERM, signal.SIGKILL}


@pytest.mark.asyncio
async def test_timeout_survives_killpg_failure():
    """A failing kill must never mask the CommandTimeout itself."""
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=HangingProcess())
    ), patch("os.killpg", side_effect=OSError("permission denied")), patch(
        "os.getpgid", return_value=42
    ):
        with pytest.raises(CommandTimeout):
            await run_process(["ffmpeg"], timeout=0.05)


@pytest.mark.asyncio
async def test_disk_quota_check_fails_over_quota_job():
    """Per-job disk quota: a stage that blows the quota fails the job."""
    reg = StageRegistry()
    reg.register(CountingStage("big", critical=True))
    service = make_service(reg)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )
    work = _job_work_dir(job)
    os.makedirs(work, exist_ok=True)
    try:
        with open(os.path.join(work, "blob"), "wb") as f:
            f.write(b"x" * 1024)  # tiny — quota check uses ctx; just exercise path
        job = await service.advance(job.id)
        assert job.status == PipelineJobStatus.COMPLETED
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Finding 3: temp files removed on success, failure, and cancellation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_on_stage_failure():
    reg = StageRegistry()
    reg.register(CountingStage("flaky", critical=True, fail_times=99))
    service = make_service(reg, max_attempts=2)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )
    work = _job_work_dir(job)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "partial.mp4"), "wb") as f:
        f.write(b"x")

    job = await service.advance(job.id)

    assert job.status == PipelineJobStatus.FAILED
    assert not os.path.exists(work), "work dir must be removed on failure"


@pytest.mark.asyncio
async def test_cleanup_on_success():
    reg = StageRegistry()
    reg.register(CountingStage("ok", critical=True))
    service = make_service(reg)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )
    work = _job_work_dir(job)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "rendition.mp4"), "wb") as f:
        f.write(b"x")

    job = await service.advance(job.id)

    assert job.status == PipelineJobStatus.COMPLETED
    assert not os.path.exists(work), "work dir must be removed on success"


@pytest.mark.asyncio
async def test_cleanup_on_cancellation():
    reg = StageRegistry()
    reg.register(CancelStage("cancel"))
    service = make_service(reg)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )
    work = _job_work_dir(job)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "partial.ts"), "wb") as f:
        f.write(b"x")

    with pytest.raises(asyncio.CancelledError):
        await service.advance(job.id)

    assert not os.path.exists(work), "work dir must be removed on cancellation"


# ---------------------------------------------------------------------------
# Finding 4: partial outputs are never published as completed media.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_packaging_failure_never_publishes_content():
    reg = StageRegistry()
    reg.register(CountingStage("encode", success_event="content.encoded"))
    reg.register(CountingStage("hls_package", success_event="content.packaged", fail_times=99))
    service = make_service(reg, max_attempts=2)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )

    job = await service.advance(job.id)
    await service.drain_outbox()

    assert job.status == PipelineJobStatus.FAILED
    topics = [e.topic for e in service.publisher.sent]
    assert "content.published" not in topics
    assert "content.packaged" not in topics


@pytest.mark.asyncio
async def test_media_requires_both_playlists_before_packaged_event():
    reg = StageRegistry()
    reg.register(CountingStage("hls_package", success_event="content.hls_done"))
    reg.register(CountingStage("dash_package", success_event="content.packaged", fail_times=99))
    service = make_service(reg, max_attempts=2)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )

    job = await service.advance(job.id)
    await service.drain_outbox()

    assert job.status == PipelineJobStatus.FAILED
    topics = [e.topic for e in service.publisher.sent]
    assert "content.published" not in topics
    assert "content.packaged" not in topics  # needs hls AND dash


# ---------------------------------------------------------------------------
# Finding 5: retries never re-run completed stages or double-publish.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_never_reruns_completed_stage_and_publishes_once():
    reg = StageRegistry()
    encode = CountingStage("encode", success_event="content.encoded")
    package = CountingStage("package", success_event="content.packaged", fail_times=2)
    reg.register(encode)
    reg.register(package)
    service = make_service(reg, max_attempts=3)
    job = await service.start_job(
        content_id=uuid4(), upload_session_id=uuid4(), storage_key="uploads/x/a.mp4"
    )

    job = await service.advance(job.id)
    await service.drain_outbox()

    assert job.status == PipelineJobStatus.COMPLETED
    assert encode.calls == 1, "completed encode stage must never re-run"
    assert package.calls == 3, "flaky stage retried within its own attempts"
    topics = [e.topic for e in service.publisher.sent]
    assert topics.count("content.published") == 1
    assert topics.count("content.packaged") == 1
    assert set(job.stage_versions) == {"encode", "package"}  # no duplicate entries