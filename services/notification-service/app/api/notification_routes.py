"""Notification service API routes."""

from typing import Annotated
from uuid import UUID

from jose import jwt
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import NotificationRepository
from app.services import NotificationService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


async def get_current_user_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Resolve the authenticated user id from the JWT sub claim."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        )


async def get_notif_service(
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> NotificationService:
    return NotificationService(NotificationRepository(db))


@router.post("/send", response_model=dict)
async def send_notification(
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
    current_user: Annotated[UUID, Depends(get_current_user_id)] = ...,
    user_id: UUID = Body(...),
    title: str = Body(...),
    message: str = Body(...),
    channel: str = Body(default="in-app"),
):
    """Send notification."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only act on your own account"
        )
    await service.send_notification(user_id, title, message, channel)
    return {"status": "sent"}


@router.get("/unread/{user_id}")
async def get_unread_notifications(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    user_id: UUID,
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Get unread notifications belonging to the authenticated user."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only access your own data"
        )

    notifications = await service.get_unread(user_id)
    return {
        "notifications": [
            {
                "id": str(notification.id),
                "user_id": str(notification.user_id),
                "title": notification.title,
                "message": notification.message,
                "channel": notification.channel,
                "is_read": notification.is_read,
                "created_at": notification.created_at.isoformat()
                if notification.created_at
                else None,
            }
            for notification in notifications
        ],
        "total": len(notifications),
    }


@router.post("/{notification_id}/read")
async def mark_notification_as_read(
    notification_id: UUID,
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Mark a notification read only when it belongs to the authenticated user."""
    updated = await service.mark_as_read(notification_id, current_user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "read"}
