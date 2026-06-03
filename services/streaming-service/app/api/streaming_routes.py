"""Streaming service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.repositories import StreamingSessionRepository, VideoManifestRepository, WatchHistoryRepository, StreamingMetricsRepository
from app.services import StreamingService

router = APIRouter(prefix="/streaming", tags=["streaming"])

async def get_streaming_service(db: AsyncSession = Depends(get_db_session)) -> StreamingService:
    return StreamingService(
        StreamingSessionRepository(db),
        VideoManifestRepository(db),
        WatchHistoryRepository(db),
        StreamingMetricsRepository(db)
    )

@router.post("/session/start")
async def start_streaming_session(user_id: UUID = Body(...), content_id: UUID = Body(...),
                                 device_id: str = Body(...),
                                 service: StreamingService = Depends(get_streaming_service)):
    """Start a new streaming session."""
    session = await service.start_session(user_id, content_id, device_id)
    return {"session_id": str(session.id), "content_id": str(content_id), "status": "active"}

@router.get("/manifest/{content_id}")
async def get_video_manifest(content_id: UUID, quality: str = "auto",
                            service: StreamingService = Depends(get_streaming_service)):
    """Get video manifest for HLS/DASH streaming."""
    manifest = await service.get_manifest(content_id, quality)
    if not manifest:
        raise HTTPException(status_code=404, detail="Manifest not found")
    return {"url": manifest.url, "type": manifest.type, "quality": quality}

@router.put("/session/{session_id}/position")
async def update_watch_position(session_id: UUID, position_seconds: int = Body(...),
                               service: StreamingService = Depends(get_streaming_service)):
    """Update watch position."""
    await service.update_position(session_id, position_seconds)
    return {"status": "updated", "position": position_seconds}

@router.post("/session/{session_id}/end")
async def end_streaming_session(session_id: UUID,
                               service: StreamingService = Depends(get_streaming_service)):
    """End streaming session."""
    await service.end_session(session_id)
    return {"status": "ended"}

@router.get("/watch-history/{user_id}")
async def get_watch_history(user_id: UUID, limit: int = 50,
                           service: StreamingService = Depends(get_streaming_service)):
    """Get user watch history."""
    history = await service.get_watch_history(user_id, limit)
    return {"items": history, "total": len(history)}

@router.get("/metrics/{session_id}")
async def get_streaming_metrics(session_id: UUID,
                               service: StreamingService = Depends(get_streaming_service)):
    """Get streaming session metrics."""
    metrics = await service.get_metrics(session_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Metrics not found")
    return {"bitrate": metrics.bitrate, "dropped_frames": metrics.dropped_frames, "buffering_events": metrics.buffering_events}
