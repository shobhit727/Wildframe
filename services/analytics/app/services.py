"""Analytics service business logic."""
from uuid import UUID
from app.repositories import EventRepository

class AnalyticsService:
    def __init__(self, event_repo: EventRepository):
        self.event_repo = event_repo
    
    async def log_event(self, user_id: UUID, event_type: str, event_data: dict = None, content_id: UUID = None):
        """Log analytics event."""
        return await self.event_repo.create(user_id, event_type, event_data, content_id)
    
    async def get_user_events(self, user_id: UUID, limit: int = 100):
        """Get user events."""
        events = await self.event_repo.get_by_user(user_id, limit)
        return [{"event_type": e.event_type, "data": e.event_data, "timestamp": e.timestamp.isoformat()} for e in events]
