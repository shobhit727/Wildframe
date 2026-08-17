"""Notification service API routes."""

from typing import Annotated
from uuid import UUID

from jose import JWTError, jwt  # type: ignore[import-untyped]
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
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
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
        )
    except JWTError:
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


class PreferenceUpdate(BaseModel):
    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
    push_enabled: bool | None = None
    sms_enabled: bool | None = None


@router.post("/send", response_model=dict)
async def send_notification(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    user_id: UUID = Body(...),
    title: str = Body(...),
    message: str = Body(...),
    channel: str = Body(default="in-app"),
    event_id: UUID | None = Body(default=None),
    channels: list[str] | None = Body(default=None),
    email_address: str | None = Body(default=None),
    template: str = Body(default="generic"),
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Send notification."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only act on your own account"
        )
    kwargs: dict = {}
    if event_id is not None:
        kwargs["event_id"] = event_id
    if channels is not None:
        kwargs["channels"] = channels
    if email_address is not None:
        kwargs["email_address"] = email_address
    if template != "generic":
        kwargs["template"] = template
    result = await service.send_notification(user_id, title, message, channel, **kwargs)
    if isinstance(result, dict):
        return result
    return {"status": "sent"}  # type: ignore[unreachable]


@router.get("/unread/{user_id}")
async def get_unread_notifications(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    user_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Get unread notifications belonging to the authenticated user."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only access your own data"
        )

    notifications = await service.get_unread(user_id, limit=limit, offset=offset)
    return {
        "notifications": [
            {
                "id": str(notification.id),
                "user_id": str(notification.user_id),
                "title": notification.title,
                "message": notification.message,
                "channel": notification.channel,
                "is_read": notification.is_read,
                "created_at": (
                    notification.created_at.isoformat() if notification.created_at else None
                ),
            }
            for notification in notifications
        ],
        "total": len(notifications),
    }


@router.get("/unread-count/{user_id}")
async def get_unread_count(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    user_id: UUID,
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Get the unread notification count for the authenticated user."""
    if user_id != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only access your own data"
        )
    return {"count": await service.get_unread_count(user_id)}


@router.get("/preferences")
async def get_preferences(
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Get the authenticated user's channel delivery preferences."""
    return await service.get_preferences(current_user)


@router.put("/preferences")
async def update_preferences(
    update: PreferenceUpdate,
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Update the authenticated user's channel delivery preferences."""
    flags = update.model_dump(exclude_none=True)
    if not flags:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No preference fields provided",
        )
    return await service.update_preferences(current_user, flags)


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


@router.post("/{notification_id}/retry")
async def retry_notification_delivery(
    notification_id: UUID,
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Re-dispatch previously failed channels without duplicating the notification."""
    result = await service.retry_delivery(notification_id, current_user)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return result


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: UUID,
    current_user: Annotated[UUID, Depends(get_current_user_id)],
    service: NotificationService = Depends(get_notif_service),  # noqa: B008
):
    """Delete a notification only when it belongs to the authenticated user."""
    deleted = await service.delete_notification(notification_id, current_user)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "deleted"}
