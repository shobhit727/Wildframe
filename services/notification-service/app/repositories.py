"""Notification service repositories."""

from datetime import UTC, datetime
from uuid import UUID
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: UUID, title: str, message: str, channel: str = "in-app"):
        notif = Notification(user_id=user_id, title=title, message=message, channel=channel)
        self.session.add(notif)
        await self.session.flush()
        return notif

    async def get_unread(self, user_id: UUID) -> list[Notification]:
        stmt = select(Notification).where(
            (Notification.user_id == user_id) & (Notification.is_read == False)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_as_read(self, notification_id: UUID, user_id: UUID) -> bool:
        """Mark a notification read only when it belongs to the caller."""
        stmt = (
            update(Notification)
            .where(
                (Notification.id == notification_id)
                & (Notification.user_id == user_id)
                & (Notification.is_read == False)
            )
            .values(is_read=True, read_at=datetime.now(UTC).replace(tzinfo=None))
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast(CursorResult, result).rowcount == 1
