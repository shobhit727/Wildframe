"""Notification service API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import NotificationRepository
from app.services import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def get_notif_service(db: Annotated[AsyncSession, Depends(get_db)]) -> NotificationService:
    return NotificationService(NotificationRepository(db))


@router.post("/send")
async def send_notification(
    user_id: Annotated[UUID, Body(...)],
    title: Annotated[str, Body(...)],
    message: Annotated[str, Body(...)],
    channel: Annotated[str, Body(default="in-app")],
    service: Annotated[NotificationService, Depends(get_notif_service)],
):
    """Send notification."""
    await service.send_notification(user_id, title, message, channel)
    return {"status": "sent"}


@router.get("/unread/{user_id}")
async def get_unread_notifications(
    user_id: UUID, service: Annotated[NotificationService, Depends(get_notif_service)]
):
    """Get unread notifications."""
    return {"notifications": [], "total": 0}
