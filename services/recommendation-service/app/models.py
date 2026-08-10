import uuid

"""Recommendation service models."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, Mapped, declarative_base

Base = declarative_base()


class UserPreferences(Base):
    """User content preferences for recommendations."""

    __tablename__ = "user_preferences"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    liked_genres = Column(JSON, default=[])
    disliked_genres = Column(JSON, default=[])
    preferred_languages = Column(JSON, default=["en"])
    watch_frequency = Column(String(50), default="medium")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Recommendation(Base):
    """Generated recommendation for user."""

    __tablename__ = "recommendations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    score = Column(Float, nullable=False)
    reason = Column(String(255))
    algorithm = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    __table_args__ = (Index("idx_recommendations_user", "user_id", "score"),)
