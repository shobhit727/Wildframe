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
    Column,
    DateTime,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


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


class ContentFlag(Base):
    """A flag raised against a piece of content for moderator review."""

    __tablename__ = "content_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    flag_reason = Column(SQLEnum(FlagReason), nullable=False)
    reported_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    status = Column(
        SQLEnum(FlagStatus),
        default=FlagStatus.PENDING,
        nullable=False,
        index=True,
    )
    reviewed_by = Column(UUID(as_uuid=True), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("idx_content_flag_status_created", "status", "created_at"),)


class ModerationDecision(Base):
    """An audit-trail row for every moderator action on a flag."""

    __tablename__ = "moderation_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    flag_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_flags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    moderator_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    decision = Column(SQLEnum(DecisionType), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("idx_decision_flag_created", "flag_id", "created_at"),)


class CreatorStrike(Base):
    """A strike against a creator. 3 active strikes = auto-suspension."""

    __tablename__ = "creator_strikes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    strike_reason = Column(SQLEnum(StrikeReason), nullable=False)
    related_flag_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_flags.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("idx_strike_creator_active", "creator_id", "is_active"),)
