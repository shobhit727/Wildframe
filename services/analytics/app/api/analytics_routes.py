"""Analytics service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.repositories import EventRepository
from app.services import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])

async def get_analytics_service(db: AsyncSession = Depends(get_db_session)) -> AnalyticsService:
    return AnalyticsService(EventRepository(db))

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
