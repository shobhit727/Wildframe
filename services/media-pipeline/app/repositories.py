"""Media pipeline service repositories."""
from uuid import UUID
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import (
    PipelineJob,
    PipelineJobStatus,
    PipelineStageLog,
    PipelineStageStatus,
)


class PipelineJobRepository:
    """Persistence for pipeline jobs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, job: PipelineJob) -> PipelineJob:
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: UUID) -> Optional[PipelineJob]:
        result = await self.session.execute(
            select(PipelineJob).where(PipelineJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_by_upload_session(
        self, upload_session_id: UUID
    ) -> Optional[PipelineJob]:
        result = await self.session.execute(
            select(PipelineJob).where(
                PipelineJob.upload_session_id == upload_session_id
            )
        )
        return result.scalar_one_or_none()

    async def save(self, job: PipelineJob) -> PipelineJob:
        job.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def list_by_status(
        self, status: PipelineJobStatus, limit: int = 50
    ) -> List[PipelineJob]:
        result = await self.session.execute(
            select(PipelineJob)
            .where(PipelineJob.status == status)
            .order_by(PipelineJob.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class PipelineStageLogRepository:
    """Persistence for the append-only stage audit log."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, log: PipelineStageLog) -> PipelineStageLog:
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_for_job(self, job_id: UUID) -> List[PipelineStageLog]:
        result = await self.session.execute(
            select(PipelineStageLog)
            .where(PipelineStageLog.job_id == job_id)
            .order_by(PipelineStageLog.created_at)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Legacy compatibility repository.
#
# Kept so existing imports of ``TranscodingJobRepository`` still resolve. It is
# unused by the new pipeline.
# ---------------------------------------------------------------------------

from app.models import TranscodingJob


class TranscodingJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, content_id: UUID, source_url: str) -> TranscodingJob:
        job = TranscodingJob(content_id=content_id, source_url=source_url)
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_by_content_id(self, content_id: UUID) -> Optional[TranscodingJob]:
        result = await self.session.execute(
            select(TranscodingJob).where(TranscodingJob.content_id == content_id)
        )
        return result.scalar_one_or_none()

    async def update_progress(self, job_id: UUID, progress: int) -> TranscodingJob:
        job = await self.session.get(TranscodingJob, job_id)
        if job:
            job.progress_percentage = progress
            await self.session.flush()
        return job
