import uuid

"""Moderation service models.

Three tables drive the content review workflow:

* ``content_flags``       — one row per flag raised against a piece of content;
  tracks who raised it, why, its review status, and the resolution.
* ``moderation_decisions`` — one row per moderator action on a flag; an audit
  trail of who decided what and why.
* ``creator_strikes``      — one row per strike against a creator; 3 active
  strikes trigger automatic suspension (enforced in the service layer).

Status machine (``ContentFlag.status``):

    pending → reviewing → resolved
                  ↘ escalated

A flag starts ``pending``. A moderator picks it up (``reviewing``) before
rendering a decision (``resolved``) or escalating to a senior moderator
(``escalated``).
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class FlagReason(str, Enum):
    """Why a piece of content was flagged."""

    SPAM = "spam"
    INAPPROPRIATE = "inappropriate"
    COPYRIGHT = "copyright"
    OTHER = "other"


class FlagStatus(str, Enum):
    """Review status of a flag."""

    PENDING = "pending"
    REVIEWING = "reviewing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class DecisionType(str, Enum):
    """Moderator decision on a flag."""

    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class StrikeReason(str, Enum):
    """Why a creator received a strike."""

    CONTENT_VIOLATION = "content_violation"
    COPYRIGHT = "copyright"
    REPEATED_FLAGS = "repeated_flags"


class OutboxEventStatus(str, Enum):
    """Delivery status of a transactional-outbox row."""

    PENDING = "pending"
    DISPATCHED = "dispatched"


class OutboxEvent(Base):
    """A domain event persisted in the same transaction as its business state.

    The transactional outbox guarantees that the event becomes durable
    exactly when the state it describes becomes durable: both rows share one
    database transaction. A background worker publishes PENDING rows
    (at-least-once; consumers dedupe on ``event_key``) and marks them
    DISPATCHED, so a broker outage never loses an event and a successful
    commit always implies a recoverable event trail.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[OutboxEventStatus] = mapped_column(
        SQLEnum(OutboxEventStatus),
        default=OutboxEventStatus.PENDING,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (Index("idx_outbox_status_created", "status", "created_at"),)


class ContentFlag(Base):
    """A flag raised against a piece of content for moderator review."""

    __tablename__ = "content_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    flag_reason: Mapped[FlagReason] = mapped_column(SQLEnum(FlagReason), nullable=False)
    reported_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    content_creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="Authoritative creator UUID owning content_id (resolved upstream by content-service).",
    )
    status: Mapped[FlagStatus] = mapped_column(
        SQLEnum(FlagStatus),
        default=FlagStatus.PENDING,
        nullable=False,
        index=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("idx_content_flag_status_created", "status", "created_at"),)


class ModerationDecision(Base):
    """An audit-trail row for every moderator action on a flag."""

    __tablename__ = "moderation_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    flag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_flags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    moderator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    decision: Mapped[DecisionType] = mapped_column(SQLEnum(DecisionType), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("idx_decision_flag_created", "flag_id", "created_at"),)


class CreatorStrike(Base):
    """A strike against a creator. 3 active strikes = auto-suspension."""

    __tablename__ = "creator_strikes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    strike_reason: Mapped[StrikeReason] = mapped_column(SQLEnum(StrikeReason), nullable=False)
    related_flag_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_flags.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("idx_strike_creator_active", "creator_id", "is_active"),)
