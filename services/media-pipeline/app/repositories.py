"""Media pipeline service repositories."""
from uuid import UUID
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import TranscodingJob

class TranscodingJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def create(self, content_id: UUID, source_url: str):
        job = TranscodingJob(content_id=content_id, source_url=source_url)
        self.session.add(job)
        await self.session.flush()
        return job
    async def get_by_content_id(self, content_id: UUID) -> Optional[TranscodingJob]:
        stmt = select(TranscodingJob).where(TranscodingJob.content_id == content_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    async def update_progress(self, job_id: UUID, progress: int):
        job = await self.session.get(TranscodingJob, job_id)
        if job:
            job.progress_percentage = progress
            await self.session.flush()
        return job
