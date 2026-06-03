"""Media pipeline service tests."""
import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import MediaPipelineService

@pytest.mark.asyncio
async def test_start_transcoding(db: AsyncSession):
    """Test starting transcoding job."""
    content_id = uuid4()
    service = MediaPipelineService(None)
    
    job = await service.start_transcoding(content_id, "https://example.com/video.mp4")
    assert job is not None
    assert job.status in ["pending", "processing"]

@pytest.mark.asyncio
async def test_get_job_status(db: AsyncSession):
    """Test getting job status."""
    content_id = uuid4()
    service = MediaPipelineService(None)
    
    job = await service.get_job_status(content_id)
    assert job is None or hasattr(job, 'status')
