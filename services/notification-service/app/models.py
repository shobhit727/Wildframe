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


#: Maximum stored length of ``Notification.title`` / ``Notification.message``.
#:
#: These are the single source of truth for both halves of the length
#: guarantee. ``NotificationService.send_notification`` sanitises and truncates
#: user-controlled text to exactly these lengths before the insert, and the
#: ``VARCHAR`` columns below are declared *from these constants* -- so the two
#: cannot be edited apart.
#:
#: They used to be a literal ``String(255)`` / ``String(1000)`` here and a
#: separate ``TITLE_MAX_LENGTH`` / ``MESSAGE_MAX_LENGTH`` in ``app.services.py``.
#: Nothing tied the two together: raising the service limits tenfold kept all
#: 302 tests green, and since PostgreSQL rejects an over-long value for a
#: ``VARCHAR(n)`` with ``StringDataRightTruncation``, the result would have been
#: a 500 on every send with a long title -- the exact failure the sanitiser's
#: truncation exists to prevent.
#:
#: Widening either one is a *schema* change, not a tuning knob: it needs a
#: migration and a review, because it changes what every existing reader of the
#: table can see. That is asserted in the test suite so it cannot be an
#: accident.
TITLE_MAX_LENGTH = 255
MESSAGE_MAX_LENGTH = 1000


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class Notification(Base):
    """User notification."""

    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Declared from the limits above rather than repeating the numbers.
    title = Column(String(TITLE_MAX_LENGTH), nullable=False)
    message = Column(String(MESSAGE_MAX_LENGTH), nullable=False)
    channel = Column(String(50))
    # Deduplication: one domain event id maps to at most one notification row.
    event_id = Column(UUID(as_uuid=True), nullable=True)
    delivery_status = Column(String(20), default="pending", nullable=False)
    # Per-channel outcome map (JSON) so retries only re-dispatch failed channels.
    delivery_errors = Column(Text, nullable=True)
    is_read: Mapped[bool | None] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow_naive)
    read_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    # Soft delete: deleted rows are excluded from every read path.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
