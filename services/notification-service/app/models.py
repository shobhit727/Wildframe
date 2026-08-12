"""Notification service models."""

import uuid
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow_naive() -> datetime:
    """UTC timestamp stored tz-naive (repo convention: all columns are naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class Notification(Base):
    """User notification."""

    __tablename__ = "notifications"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(String(1000), nullable=False)
    channel = Column(String(50))
    # Deduplication: one domain event id maps to at most one notification row.
    event_id = Column(UUID(as_uuid=True), nullable=True)
    delivery_status = Column(String(20), default="pending", nullable=False)
    # Per-channel outcome map (JSON) so retries only re-dispatch failed channels.
    delivery_errors = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow_naive)
    read_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    # Soft delete: deleted rows are excluded from every read path.
    deleted_at = Column(DateTime, nullable=True)
    __table_args__ = (
        Index("idx_notifications_user_read", "user_id", "is_read"),
        Index("uq_notifications_event_id", "event_id", unique=True),
    )


class NotificationPreference(Base):
    """Per-user channel delivery preferences, enforced server-side at send time."""

    __tablename__ = "notification_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    in_app_enabled = Column(Boolean, default=True, nullable=False)
    email_enabled = Column(Boolean, default=True, nullable=False)
    push_enabled = Column(Boolean, default=True, nullable=False)
    sms_enabled = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
