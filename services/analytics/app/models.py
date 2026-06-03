"""Analytics service models."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Integer, DateTime, Index, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Event(Base):
    """Analytics event log."""
    __tablename__ = "events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    event_data = Column(JSON)
    content_id = Column(UUID(as_uuid=True), nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("idx_events_user_type", "user_id", "event_type"),)
