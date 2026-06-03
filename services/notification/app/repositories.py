"""Notification service repositories."""
from uuid import UUID
from datetime import datetime, timezone
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Notification

class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def create(self, user_id: UUID, title: str, message: str, channel: str = "in-app"):
        notif = Notification(user_id=user_id, title=title, message=message, channel=channel)
        self.session.add(notif)
        await self.session.flush()
        return notif
    async def get_unread(self, user_id: UUID) -> List[Notification]:
        stmt = select(Notification).where((Notification.user_id == user_id) & (Notification.is_read == False))
        result = await self.session.execute(stmt)
        return result.scalars().all()
