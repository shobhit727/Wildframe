"""Analytics service API routes."""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.repositories import (
    EventRepository,
    ContentViewEventRepository,
    CreatorAnalyticsSnapshotRepository,
    ContentPerformanceMetricsRepository,
)
from app.services import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])

async def get_analytics_service(db: AsyncSession = Depends(get_db)) -> AnalyticsService:
    return AnalyticsService(
        EventRepository(db),
        ContentViewEventRepository(db),
        CreatorAnalyticsSnapshotRepository(db),
        ContentPerformanceMetricsRepository(db),
    )

@router.post("/events")
async def log_event(user_id: UUID = Body(...), event_type: str = Body(...),
                   event_data: dict = Body(None), content_id: UUID = Body(None),
                   service: AnalyticsService = Depends(get_analytics_service)):
    """Log analytics event."""
    await service.log_event(user_id, event_type, event_data, content_id)
    return {"status": "logged"}

@router.get("/user-events/{user_id}")
async def get_user_events(user_id: UUID, limit: int = 100,
                         service: AnalyticsService = Depends(get_analytics_service)):
    """Get user events."""
    events = await service.get_user_events(user_id, limit)
    return {"events": events, "total": len(events)}

@router.post("/view-events")
async def record_view_event(
    content_id: UUID = Body(...),
    viewer_id: UUID = Body(...),
    watch_duration_seconds: int = Body(0),
    content_duration_seconds: int = Body(0),
    completion_pct: float = Body(0.0),
    playback_quality: str = Body(None),
    started_at: datetime = Body(None),
    completed_at: datetime = Body(None),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Record a content view/playback event."""
    await service.record_view_event(
        content_id=content_id,
        viewer_id=viewer_id,
        watch_duration_seconds=watch_duration_seconds,
        content_duration_seconds=content_duration_seconds,
        completion_pct=completion_pct,
        playback_quality=playback_quality,
        started_at=started_at,
        completed_at=completed_at,
    )
    return {"status": "recorded"}

@router.get("/creators/{creator_id}")
async def get_creator_analytics(creator_id: UUID, service: AnalyticsService = Depends(get_analytics_service)):
    """Get analytics for a creator."""
    analytics = await service.get_creator_analytics(creator_id)
    if not analytics:
        return {"creator_id": str(creator_id), "analytics": None}
    return analytics

@router.get("/content/{content_id}")
async def get_content_performance(content_id: UUID, service: AnalyticsService = Depends(get_analytics_service)):
    """Get performance metrics for content."""
    performance = await service.get_content_performance(content_id)
    if not performance:
        return {"content_id": str(content_id), "metrics": None}
    return performance
