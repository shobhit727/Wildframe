"""Analytics service repositories."""
from uuid import UUID
from datetime import datetime, timezone
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models import Event

class EventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def create(self, user_id: UUID, event_type: str, event_data: dict = None, content_id: UUID = None):
        event = Event(user_id=user_id, event_type=event_type, event_data=event_data, content_id=content_id)
        self.session.add(event)
        await self.session.flush()
        return event
    async def get_by_user(self, user_id: UUID, limit: int = 100) -> List[Event]:
        stmt = select(Event).where(Event.user_id == user_id).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()
