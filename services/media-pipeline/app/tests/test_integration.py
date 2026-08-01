"""Integration tests for Media Pipeline Service."""
from uuid import uuid4

import pytest_asyncio

from app.models import PipelineJobStatus, PipelineStageStatus
from app.repositories import PipelineJobRepository, PipelineStageLogRepository
from app.services import MediaPipelineService


@pytest_asyncio.fixture
async def pipeline_service(db_session):
    """MediaPipelineService instance with test DB."""
    return MediaPipelineService(
        job_repo=PipelineJobRepository(db_session),
        log_repo=PipelineStageLogRepository(db_session),
    )


class TestPipelineJobIntegration:
    """Integration tests for pipeline jobs."""

    async def test_start_job_idempotent(self, pipeline_service, db_session):
        """Test starting a pipeline job is idempotent on upload_session_id."""
        content_id = uuid4()
        upload_session_id = uuid4()
        storage_key = "uploads/test-video.mp4"
        
        # First call
        job1 = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key=storage_key,
        )
        
        # Second call with same upload_session_id
        job2 = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key=storage_key,
        )
        
        assert job1.id == job2.id
        assert job1.upload_session_id == upload_session_id
        assert job1.status == PipelineJobStatus.PENDING

    async def test_get_job(self, pipeline_service, db_session):
        """Test getting a pipeline job by ID."""
        content_id = uuid4()
        upload_session_id = uuid4()
        
        job = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key="test.mp4",
        )
        
        retrieved = await pipeline_service.job_repo.get(job.id)
        
        assert retrieved is not None
        assert retrieved.id == job.id

    async def test_list_jobs_by_status(self, pipeline_service, db_session):
        """Test listing jobs by status."""
        for i in range(3):
            await pipeline_service.start_job(
                content_id=uuid4(),
                upload_session_id=uuid4(),
                storage_key=f"test{i}.mp4",
            )
        
        pending_jobs = await pipeline_service.job_repo.list_by_status(
            PipelineJobStatus.PENDING, limit=10
        )
        
        assert len(pending_jobs) >= 3


class TestPipelineAdvanceIntegration:
    """Integration tests for pipeline advancement."""

    async def test_advance_job_completes(self, pipeline_service, db_session):
        """Test advancing a job through all stages."""
        content_id = uuid4()
        upload_session_id = uuid4()
        
        job = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key="test.mp4",
        )
        
        # Advance through all stages
        completed_job = await pipeline_service.advance(job.id)
        
        # Job should be completed (all stub stages succeed)
        assert completed_job.status == PipelineJobStatus.COMPLETED
        assert completed_job.current_stage is None
        assert len(completed_job.stage_versions) > 0
        
        # Verify stage logs created
        logs = await pipeline_service.log_repo.list_for_job(job.id)
        assert len(logs) > 0
        assert all(log.status == PipelineStageStatus.SUCCESS for log in logs)

    async def test_advance_job_idempotent(self, pipeline_service, db_session):
        """Test advancing a job multiple times is idempotent."""
        content_id = uuid4()
        upload_session_id = uuid4()
        
        job = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key="test.mp4",
        )
        
        # First advance
        await pipeline_service.advance(job.id)
        
        # Second advance - should be no-op since already completed
        completed = await pipeline_service.advance(job.id)
        
        assert completed.status == PipelineJobStatus.COMPLETED


class TestPipelineStageLogIntegration:
    """Integration tests for stage logs."""

    async def test_stage_log_created_per_attempt(self, pipeline_service, db_session):
        """Test that stage logs are created for each attempt."""
        content_id = uuid4()
        upload_session_id = uuid4()
        
        job = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key="test.mp4",
        )
        
        await pipeline_service.advance(job.id)
        
        logs = await pipeline_service.log_repo.list_for_job(job.id)
        
        # Should have one log per stage (10 stages in default pipeline)
        assert len(logs) >= 10
        
        # All should be success
        for log in logs:
            assert log.status == PipelineStageStatus.SUCCESS
            assert log.duration_ms >= 0

    async def test_stage_log_contains_metadata(self, pipeline_service, db_session):
        """Test that stage logs contain metadata."""
        content_id = uuid4()
        upload_session_id = uuid4()
        
        job = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key="test.mp4",
        )
        
        await pipeline_service.advance(job.id)
        
        logs = await pipeline_service.log_repo.list_for_job(job.id)
        
        for log in logs:
            assert log.stage in [
                "quarantine_store", "virus_scan", "metadata_extract",
                "thumbnail_generate", "audio_extract", "subtitle_extract",
                "ffmpeg_multi_bitrate_encode", "hls_package", "dash_package",
                "s3_upload", "cdn_invalidate"
            ]
            assert log.message is not None
            assert log.created_at is not None


class TestPipelineContextPersistence:
    """Integration tests for job context persistence."""

    async def test_context_persisted_across_advances(self, pipeline_service, db_session):
        """Test that job context is persisted and rehydrated."""
        content_id = uuid4()
        upload_session_id = uuid4()
        
        job = await pipeline_service.start_job(
            content_id=content_id,
            upload_session_id=upload_session_id,
            storage_key="uploads/test.mp4",
        )
        
        # Check initial context
        assert job.context is not None
        assert job.context["storage_key"] == "uploads/test.mp4"
        assert "job_id" in job.context
        assert "content_id" in job.context
        
        # Advance through a few stages
        await pipeline_service.advance(job.id)
        
        # Refresh job
        refreshed = await pipeline_service.job_repo.get(job.id)
        
        assert refreshed.context is not None
        assert "storage_key" in refreshed.context