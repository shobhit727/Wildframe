"""Streaming service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, Body, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services import StreamingService
from app.schemas import (
    PlaybackSessionCreateRequest,
    PlaybackSessionUpdateRequest,
    ManifestGenerationRequest,
    PlaybackSessionResponse,
    VideoManifestResponse,
)

router = APIRouter(prefix="/streaming", tags=["streaming"])


async def get_streaming_service(db: AsyncSession = Depends(get_db)) -> StreamingService:
    return StreamingService(db)


@router.post("/session/start", response_model=PlaybackSessionResponse, status_code=status.HTTP_201_CREATED)
async def start_streaming_session(
    request: PlaybackSessionCreateRequest = Body(...),
    service: StreamingService = Depends(get_streaming_service),
):
    """Start a new streaming session."""
    session = await service.start_playback_session(request)
    return session


@router.get("/manifest/{content_id}", response_model=VideoManifestResponse)
async def get_video_manifest(
    content_id: UUID,
    quality: str = "auto",
    service: StreamingService = Depends(get_streaming_service),
):
    """Get video manifest for HLS/DASH streaming.

    `content_id` is used as the manifest id. ``quality`` selects the
    bitrate tier but the canonical lookup is by manifest id.
    """
    manifest = await service.get_manifest(content_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Manifest not found")
    return manifest


@router.put("/session/{session_id}/position", response_model=PlaybackSessionResponse)
async def update_watch_position(
    session_id: UUID,
    position_seconds: int = Body(..., embed=True, ge=0),
    service: StreamingService = Depends(get_streaming_service),
):
    """Update watch position."""
    request = PlaybackSessionUpdateRequest(current_position_seconds=position_seconds)
    session = await service.update_playback_session(session_id, request)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/session/{session_id}/end", response_model=PlaybackSessionResponse)
async def end_streaming_session(
    session_id: UUID,
    service: StreamingService = Depends(get_streaming_service),
):
    """End streaming session."""
    session = await service.end_playback_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/watch-history/{user_id}")
async def get_watch_history(
    user_id: UUID,
    limit: int = 50,
    service: StreamingService = Depends(get_streaming_service),
):
    """Get user download/watch history."""
    history = await service.download_repo.get_user_downloads(user_id)
    return {"items": history[:limit], "total": len(history)}


@router.get("/metrics/{session_id}")
async def get_streaming_metrics(
    session_id: UUID,
    service: StreamingService = Depends(get_streaming_service),
):
    """Get streaming session metrics.

    Session-level metrics aggregation not implemented yet; surface a 501
    rather than fabricate a payload.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Session metrics not implemented",
    )
