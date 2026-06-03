"""Recommendation service models."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Index, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class UserPreferences(Base):
    """User content preferences for recommendations."""
    __tablename__ = "user_preferences"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    liked_genres = Column(JSON, default=[])
    disliked_genres = Column(JSON, default=[])
    preferred_languages = Column(JSON, default=["en"])
    watch_frequency = Column(String(50), default="medium")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Recommendation(Base):
    """Generated recommendation for user."""
    __tablename__ = "recommendations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id = Column(UUID(as_uuid=True), nullable=False)
    score = Column(Float, nullable=False)
    reason = Column(String(255))
    algorithm = Column(String(50))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("idx_recommendations_user", "user_id", "score"),)
