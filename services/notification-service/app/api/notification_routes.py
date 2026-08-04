"""Notification service API routes."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import NotificationRepository
from app.services import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def get_notif_service(db: AsyncSession = Depends(get_db)) -> NotificationService:
    return NotificationService(NotificationRepository(db))


@router.post("/send", response_model=dict)
async def send_notification(
    service: NotificationService = Depends(get_notif_service),
    user_id: UUID = Body(...),
    title: str = Body(...),
    message: str = Body(...),
    channel: str = Body(default="in-app"),
):
    """Send notification."""
    await service.send_notification(user_id, title, message, channel)
    return {"status": "sent"}


@router.get("/unread/{user_id}")
async def get_unread_notifications(
    user_id: UUID, service: NotificationService = Depends(get_notif_service)
):
    """Get unread notifications."""
    return {"notifications": [], "total": 0}
