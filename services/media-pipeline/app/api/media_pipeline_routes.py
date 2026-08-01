"""Media pipeline service API routes.

New pipeline routes are prefixed with ``/pipeline`` (the orchestrator). The
legacy ``/media/transcode`` route is kept for backward compatibility but the
canonical surface is ``/pipeline``.

Endpoints:
    POST /pipeline/jobs/{upload_id}/start  — start (or fetch) a pipeline job
    GET  /pipeline/jobs/{id}               — get job status + stage log
"""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import (
    PipelineJobRepository,
    PipelineStageLogRepository,
)
from app.services import MediaPipelineService, PipelineError

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


async def get_pipeline_service(
    db: AsyncSession = Depends(get_db),
) -> MediaPipelineService:
    return MediaPipelineService(PipelineJobRepository(db), PipelineStageLogRepository(db))


# ---------------------------------------------------------------------------
# Request / response schemas.
# ---------------------------------------------------------------------------


class StartJobRequest(BaseModel):
    content_id: UUID
    upload_session_id: UUID
    # storage_key is what the uploads-service stored the object at; it is the
    # pipeline's entry point to the bytes.
    storage_key: str


class JobResponse(BaseModel):
    job_id: UUID
    content_id: UUID
    upload_session_id: UUID
    status: str
    current_stage: str | None = None
    retries: int
    error: str | None = None
    stage_versions: dict


class StageLogResponse(BaseModel):
    stage: str
    status: str
    duration_ms: int
    message: str | None = None
    created_at: str


class JobDetailResponse(BaseModel):
    job: JobResponse
    stage_log: list[StageLogResponse]


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@router.post("/jobs/{upload_session_id}/start", response_model=JobResponse)
async def start_job(
    upload_session_id: UUID,
    request: StartJobRequest,
    service: MediaPipelineService = Depends(get_pipeline_service),
):
    """Start (or idempotently fetch) the pipeline for an uploaded file.

    Kicks off the state machine from ``pending`` and runs it as far as it can
    with the current data. Returns the resulting job.
    """
    try:
        job = await service.start_job(
            content_id=request.content_id,
            upload_session_id=upload_session_id,
            storage_key=request.storage_key,
        )
        job = await service.advance(job.id)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_to_response(job)


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: UUID,
    service: MediaPipelineService = Depends(get_pipeline_service),
):
    """Get a pipeline job's status plus its full stage-log audit trail."""
    job = await service.job_repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pipeline job not found")
    logs = await service.log_repo.list_for_job(job_id)
    return JobDetailResponse(
        job=_job_to_response(job),
        stage_log=[
            StageLogResponse(
                stage=log.stage,
                status=log.status.value,
                duration_ms=log.duration_ms,
                message=log.message,
                created_at=log.created_at.isoformat(),
            )
            for log in logs
        ],
    )


def _job_to_response(job) -> JobResponse:
    return JobResponse(
        job_id=job.id,
        content_id=job.content_id,
        upload_session_id=job.upload_session_id,
        status=job.status.value,
        current_stage=job.current_stage,
        retries=job.retries,
        error=job.error,
        stage_versions=job.stage_versions or {},
    )


# ---------------------------------------------------------------------------
# Legacy compatibility route (prefix /media). Kept so old callers/tests keep
# working; the canonical surface is the /pipeline router above.
# ---------------------------------------------------------------------------

legacy_router = APIRouter(prefix="/media", tags=["media-legacy"])


@legacy_router.post("/transcode")
async def start_transcoding(
    content_id: UUID = Body(...),
    source_url: str = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Legacy entry point: create a TranscodingJob (compatibility)."""
    service = MediaPipelineService(
        PipelineJobRepository(db), PipelineStageLogRepository(db)
    )
    job = await service.start_transcoding(content_id, source_url)
    return {"job_id": str(job.id), "status": "pending"}


@legacy_router.get("/job-status/{content_id}")
async def get_transcoding_status(
    content_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Legacy entry point: fetch a TranscodingJob by content id."""
    service = MediaPipelineService(
        PipelineJobRepository(db), PipelineStageLogRepository(db)
    )
    job = await service.get_job_status(content_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": job.status, "progress": job.progress_percentage}
