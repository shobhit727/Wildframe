"""A job may only reach ``COMPLETED`` if its media was durably stored (#1000).

``advance()`` used to mark the job ``COMPLETED``, enqueue ``content.published``,
commit, and only then call ``_cleanup_job_dirs`` -- which ``rmtree``s
``PIPELINE_WORK_ROOT/<job_id>``, exactly where the ffmpeg adapters wrote the
encoded renditions, the HLS playlists and the DASH manifest. The storage port in
the ffmpeg adapter set is ``StubObjectStorage``, whose ``upload()`` fabricates
an ``s3://`` URI without opening the file, so nothing was ever stored and the
pipeline destroyed the only copy of the media it had just reported as published.

These tests pin the containment:

* a run whose adapter set is real (not ``stub``) and whose storage port is not
  durable must not reach ``COMPLETED``, must not announce ``content.published``,
  and must not delete the job sandbox;
* the all-``stub`` adapter set is unaffected -- there the stubs never wrote a
  file, so there is nothing to lose and dev/test flows keep working.

The fakes are reused from ``test_pipeline_state_machine`` and ``_service`` from
``test_services_gaps``, the same helpers the orchestrator tests already share.
"""

import os
from unittest.mock import patch
from uuid import uuid4

from app.core.settings import settings
from app.core.stages import StubObjectStorage
from app.models import PipelineJobStatus
from tests.test_pipeline_state_machine import CountingStage, FakeJobRepo, _fresh_registry
from tests.test_services_gaps import _service

PUBLISHED = "content.published"
DLQ = "content.pipeline.failed"


def _registry() -> object:
    """A registry shaped like the shipped tail of the pipeline."""
    registry = _fresh_registry()
    registry.register(CountingStage("ffmpeg_multi_bitrate_encode", success_event="content.encoded"))
    registry.register(CountingStage("s3_upload", success_event="content.uploaded_to_storage"))
    return registry


def _write_manifest(work_root: str, job_id) -> str:
    """Write a packaged artifact into the job sandbox, as the ffmpeg packager does."""
    hls_dir = os.path.join(work_root, str(job_id), "hls")
    os.makedirs(hls_dir, exist_ok=True)
    master = os.path.join(hls_dir, "master.m3u8")
    with open(master, "w", encoding="utf-8") as handle:
        handle.write("#EXTM3U\n#EXT-X-VERSION:3\n")
    return master


def test_stub_object_storage_is_not_a_durable_port():
    """The premise of the defect: ``StubObjectStorage.upload`` stores nothing.

    If this ever flips, the completion gate below stops applying and the
    regression tests here start passing for the wrong reason.
    """
    assert StubObjectStorage().durable is False


async def test_real_adapters_cannot_complete_a_job_whose_media_was_never_stored(tmp_path):
    """The #1000 symptom: COMPLETED + published + the media deleted."""
    repo = FakeJobRepo()
    work_root = tmp_path / "work"
    quarantine_root = tmp_path / "quarantine"

    with (
        patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "ffmpeg"),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(work_root)),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(quarantine_root)),
    ):
        service = _service(repo, _registry())
        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="media/a/clip.mp4"
        )
        master = _write_manifest(str(work_root), job.id)
        result = await service.advance(job.id)
        await service.drain_outbox()

    topics = [event.topic for event in service.publisher.sent]

    assert (
        result.status == PipelineJobStatus.FAILED
    ), "a job whose media was never stored must not report COMPLETED"
    assert "durable" in (result.error or ""), result.error
    assert PUBLISHED not in topics, "content.published must not announce unstored media"
    assert DLQ in topics, "the job must reach an operator through the DLQ event"
    assert os.path.isfile(master), (
        "the encoded/packaged media is the only copy when nothing was stored; "
        "it must survive so an operator can recover or re-run it"
    )


async def test_the_manifest_survives_a_replayed_advance(tmp_path):
    """A DLQ'd job stays DLQ'd, and its artifacts stay put.

    ``advance()`` refuses to re-run a failed job, so this also pins that the
    gate is terminal rather than a warning the next tick walks past.
    """
    from app.services import PipelineError

    repo = FakeJobRepo()
    work_root = tmp_path / "work"

    with (
        patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "ffmpeg"),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(work_root)),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
    ):
        service = _service(repo, _registry())
        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="media/a/clip.mp4"
        )
        master = _write_manifest(str(work_root), job.id)
        await service.advance(job.id)

        try:
            await service.advance(job.id)
        except PipelineError as exc:
            assert "already failed" in str(exc)
        else:
            raise AssertionError("a FAILED job must not be advanced again")

    assert os.path.isfile(master), "the artifacts must outlive the failed job"


async def test_the_stub_adapter_set_still_completes(tmp_path):
    """Control: the gate is scoped to real adapters, not a blanket failure.

    Under ``stub`` nothing is ever written to disk, so there is nothing to lose;
    if this regressed, the dev/test pipeline would be dead in the water and the
    tests above would pass merely because every job failed.
    """
    repo = FakeJobRepo()
    work_root = tmp_path / "work"

    with (
        patch.object(settings, "MEDIA_PIPELINE_ADAPTERS", "stub"),
        patch.object(settings, "PIPELINE_WORK_ROOT", str(work_root)),
        patch.object(settings, "PIPELINE_QUARANTINE_ROOT", str(tmp_path / "quarantine")),
    ):
        service = _service(repo, _registry())
        job = await service.start_job(
            content_id=uuid4(), upload_session_id=uuid4(), storage_key="media/a/clip.mp4"
        )
        _write_manifest(str(work_root), job.id)
        result = await service.advance(job.id)
        await service.drain_outbox()

    topics = [event.topic for event in service.publisher.sent]
    assert result.status == PipelineJobStatus.COMPLETED
    assert PUBLISHED in topics
    assert not os.path.exists(work_root / str(job.id)), "stub runs must still clean up"
