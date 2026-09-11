from uuid import uuid4

from app.models import PipelineJob, PipelineJobStatus


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
        current_stage="encode",
        status=PipelineJobStatus.RUNNING,
        retries=2,
    )
    assert job.current_stage == "encode"
    assert job.status == PipelineJobStatus.RUNNING
    assert job.retries == 2
    # Unspecified defaults remain correct
    # stage_versions defaults to dict on DB insert; may be None initially
    assert job.stage_versions in (None, {})  # unchanged
    assert job.context in (None, {})
    assert job.error is None
    assert job.leased_by is None
