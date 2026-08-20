import uuid

"""Creators service models."""

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


class CreatorSuspendedError(Exception):
    """Raised when an operation is attempted on a suspended creator."""


class KYCStatus(str, Enum):
    """KYC verification status for a creator account."""

    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class MilestoneStatus(str, Enum):
    """Lifecycle status of a milestone."""

    DRAFT = "draft"
    FUNDING = "funding"
    COMPLETED = "completed"
    KILLED = "killed"


class TrancheStatus(str, Enum):
    """Lifecycle status of a milestone tranche."""

    LOCKED = "locked"
    RELEASED = "released"
    ROLLED_BACK = "rolled_back"


class PayoutStatus(str, Enum):
    """Lifecycle status of a payout ledger entry."""

    ACCRUED = "accrued"
    TRANSFERRED = "transferred"
    FAILED = "failed"


class CreatorAccount(Base):
    """A creator's onboarding + KYC + Stripe Connect link.

    One row per onboarded creator. user_id is the API-gateway identity id.
    """

    __tablename__ = "creator_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    bio: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    region_code: Mapped[str] = mapped_column(String(8), nullable=False, default="US")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    stripe_connect_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kyc_status: Mapped[KYCStatus] = mapped_column(
        SQLEnum(KYCStatus), default=KYCStatus.PENDING, nullable=False
    )
    kyc_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class EffectiveFloor(Base):
    """Per-creator living-wage floor (per finished minute, in payout currency).

    One active row per creator (enforced via unique creator_id). History is
    preserved because each adjustment inserts a new row with a later
    effective_from; get_floor_for_creator returns the latest by effective_from.
    """

    __tablename__ = "effective_floors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    per_minute_amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    effective_from: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    last_adjusted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (CheckConstraint("per_minute_amount >= 0", name="ck_floor_non_negative"),)


class CreatorPoolBalance(Base):
    """Running balance of a creator's accrued + contributed pool cents.

    accrued_cents: pool top-ups the creator has received (below-floor support).
    contributed_cents: creator's contributions into the pool (when above floor).
    """

    __tablename__ = "creator_pool_balances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    accrued_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contributed_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_payout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("accrued_cents >= 0", name="ck_pool_accrued_non_negative"),
        CheckConstraint("contributed_cents >= 0", name="ck_pool_contrib_non_negative"),
    )


class Milestone(Base):
    """A funded creator commitment with a kill clause.

    Tranches release 10/20/30/40 (see MilestoneTranche). Killing a milestone
    rolls back every non-released tranche in one transaction; released tranches
    stay released — that is the capital protection guarantee.
    """

    __tablename__ = "milestones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[MilestoneStatus] = mapped_column(
        SQLEnum(MilestoneStatus),
        default=MilestoneStatus.DRAFT,
        nullable=False,
    )
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    goal: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    kill_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class MilestoneTranche(Base):
    """A single funding tranche of a milestone.

    threshold is the percentage gate (10/20/30/40). Unique per milestone so a
    milestone cannot have two tranches at the same threshold.
    """

    __tablename__ = "milestone_tranches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    milestone_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[TrancheStatus] = mapped_column(
        SQLEnum(TrancheStatus), default=TrancheStatus.LOCKED, nullable=False
    )
    release_condition: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("milestone_id", "threshold", name="uq_tranche_milestone_threshold"),
    )


class PayoutLedger(Base):
    """Idempotent payout ledger.

    One row per (creator, period) keyed by a unique idempotency_key. A retried
    payout / retried webhook must never double-pay; the unique constraint is the
    last line of defense.
    """

    __tablename__ = "payout_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    view_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    floor_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pool_topup_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    share_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stripe_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stripe_transfer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[PayoutStatus] = mapped_column(
        SQLEnum(PayoutStatus), default=PayoutStatus.ACCRUED, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )

    __table_args__ = (
        CheckConstraint("floor_cents >= 0", name="ck_ledger_floor_non_negative"),
        CheckConstraint("pool_topup_cents >= 0", name="ck_ledger_pool_non_negative"),
        CheckConstraint("share_cents >= 0", name="ck_ledger_share_non_negative"),
        Index("ix_ledger_creator_period", "creator_id", "period_start", "period_end"),
    )


class InboundEventStatus(str, Enum):
    """Processing status of an inbound event from another service."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class InboundEvent(Base):
    """Inbound event from another service (e.g., moderation.creator.suspended).

    Events are written to this table by a consumer worker (polling or Kafka).
    The event_key enables idempotent processing - each event is processed exactly once.
    """

    __tablename__ = "inbound_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic: Mapped[str] = mapped_column(String(127), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[InboundEventStatus] = mapped_column(
        SQLEnum(InboundEventStatus), default=InboundEventStatus.PENDING, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_inbound_status_created", "status", "created_at"),)
