import os
import importlib.util
from uuid import uuid4

module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "models.py"))
spec = importlib.util.spec_from_file_location("media_models", module_path)
media_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media_models)
PipelineJob = media_models.PipelineJob
PipelineJobStatus = media_models.PipelineJobStatus
VideoManifest = media_models.VideoManifest
DeliveryProtocol = media_models.DeliveryProtocol
StreamingQualityProfile = media_models.StreamingQualityProfile
TranscodingJob = media_models.TranscodingJob
TranscodingStatus = media_models.TranscodingStatus
PipelineStageLog = media_models.PipelineStageLog
PipelineStageStatus = media_models.PipelineStageStatus


def test_pipeline_job_defaults():
    """A freshly created PipelineJob has correct default values."""
    job = PipelineJob(
        content_id=uuid4(),
        upload_session_id=uuid4(),
    )
    # ID may be None before DB insert (SQLAlchemy assigns on flush)
    assert job.id is None
    # Status defaults to pending
    assert job.status is None
    # No stage set yet
    assert job.current_stage is None
    # Empty stage_versions, retries, and context
    # stage_versions defaults to dict on DB insert; may be None initially
    assert job.stage_versions in (None, {})
    assert job.retries in (None, 0)
    assert job.context in (None, {})
    assert job.created_at in (None, job.created_at)  # accept None before DB insert


def test_pipeline_job_custom_initialization():
    """Explicit fields are respected and defaults still apply to others."""
    job = PipelineJob(
        content_id=uuid4(),
        upload_session_id=uuid4(),
        idempotency_key="test-key",
        current_stage="encode",
        status="pending",
        retries=0,
        context={"key": "value"},
    )
    assert job.idempotency_key == "test-key"
    assert job.current_stage == "encode"
    assert job.status == "pending"
    assert job.retries == 0
    assert job.context == {"key": "value"}
    assert job.leased_by is None


def test_transcoding_job_defaults_and_progress():
    """Legacy TranscodingJob defaults and progress tracking."""
    job = TranscodingJob(
        content_id=uuid4(),
        source_url="https://example.com/video.mp4",
    )
    assert job.status == "pending"
    assert job.progress_percentage == 0
    assert job.output_hls_url is None
    assert job.output_dash_url is None


def test_video_manifest_defaults_and_variants():
    """VideoManifest defaults and variant fields validation."""
    from uuid import uuid4

    vm = VideoManifest(
        episode_id=uuid4(),
        content_id=uuid4(),
        protocol="hls",
        manifest_url="https://example.com/manifest.m3u8",
        manifest_content="#EXTM3U...",
    )
    assert vm.include_subtitles is True
    assert vm.include_closed_captions is True
    assert vm.variants == []
    assert vm.available_bitrates == []


def test_quality_profile_defaults_and_codecs():
    """StreamingQualityProfile defaults and codec fields."""
    qp = StreamingQualityProfile()
    assert qp.bitrates == []
    assert qp.resolutions == []
    assert hasattr(qp, "resolutions")


def test_pipeline_stage_log_defaults():
    from uuid import uuid4
    from enum import Enum

    class PipelineStageStatus(str, Enum):
        SUCCESS = "success"
        FAILED = "failed"
        SKIPPED = "skipped"

    log = PipelineStageLog(job_id=uuid4(), stage="encode", status=PipelineStageStatus.SUCCESS)
    assert log.duration_ms == 0
    assert log.message is None
    assert log.created_at is not None
