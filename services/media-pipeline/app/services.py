"""Media pipeline service business logic."""
from uuid import UUID
from app.repositories import TranscodingJobRepository

class MediaPipelineService:
    def __init__(self, job_repo: TranscodingJobRepository):
        self.job_repo = job_repo
    
    async def start_transcoding(self, content_id: UUID, source_url: str):
        """Start transcoding job."""
        return await self.job_repo.create(content_id, source_url)
    
    async def get_job_status(self, content_id: UUID):
        """Get transcoding job status."""
        return await self.job_repo.get_by_content_id(content_id)
