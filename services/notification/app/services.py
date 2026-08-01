"""Notification service business logic."""
from uuid import UUID

from app.repositories import NotificationRepository


class NotificationService:
    def __init__(self, notif_repo: NotificationRepository):
        self.notif_repo = notif_repo
    
    async def send_notification(self, user_id: UUID, title: str, message: str, channel: str = "in-app"):
        """Send notification to user."""
        return await self.notif_repo.create(user_id, title, message, channel)
    
    async def mark_as_read(self, notification_id: UUID):
        """Mark notification as read."""
        # Implementation
