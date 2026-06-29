"""Media pipeline service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.repositories import TranscodingJobRepository
from app.services import MediaPipelineService

router = APIRouter(prefix="/media", tags=["media"])

async def get_media_service(db: AsyncSession = Depends(get_db)) -> MediaPipelineService:
    return MediaPipelineService(TranscodingJobRepository(db))

@router.post("/transcode")
async def start_transcoding(content_id: UUID = Body(...), source_url: str = Body(...),
                           service: MediaPipelineService = Depends(get_media_service)):
    """Start transcoding job."""
    job = await service.start_transcoding(content_id, source_url)
    return {"job_id": str(job.id), "status": "pending"}

@router.get("/job-status/{content_id}")
async def get_transcoding_status(content_id: UUID,
                                service: MediaPipelineService = Depends(get_media_service)):
    """Get transcoding job status."""
    job = await service.get_job_status(content_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": job.status, "progress": job.progress_percentage}
