"""Notification service business logic."""

from uuid import UUID

from app.repositories import NotificationRepository


class NotificationService:
    def __init__(self, notif_repo: NotificationRepository):
        self.notif_repo = notif_repo

    async def send_notification(
        self, user_id: UUID, title: str, message: str, channel: str = "in-app"
    ):
        """Send notification to user."""
        return await self.notif_repo.create(user_id, title, message, channel)

    async def get_unread(self, user_id: UUID):
        """Return unread notifications belonging to the user."""
        return await self.notif_repo.get_unread(user_id)

    async def mark_as_read(self, notification_id: UUID, user_id: UUID) -> bool:
        """Mark one of the user's notifications as read."""
        return await self.notif_repo.mark_as_read(notification_id, user_id)
