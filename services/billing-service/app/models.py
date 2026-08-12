"""Billing service domain models.

Implements the Sustenance Engine architecture from PRODUCT_VISION.md:
  - Revenue tiers: AVOD (free, ad-supported), SVOD ($7.99/mo), TVOD (pay-per-view)
  - Living-wage floor per region (guaranteed minimum per finished minute)
  - Creator Pool (15% of net revenue, redistributed to creators below their floor)
  - Milestone-Tranched funding (10/20/30/40 with kill clauses)
  - Payout ledger with idempotency keys to prevent double-pay

Key invariant: >= 55% of net SVOD revenue goes to creators. This is
calculated BEFORE platform costs are deducted (contractual floor, not target).

Monetary invariants (see #189/#191/#477/#478):
  - Amounts are Numeric columns (exact decimal), never binary floats.
  - Currency codes are ISO-4217 and immutable after a record exists: no
    repository or service method ever rewrites currency on an existing row.
  - Invoices keep their original ``amount`` forever; refunds only move
    ``refunded_amount`` (bounded by a check constraint).
  - Provider event/payment identifiers carry unique constraints so replays
    cannot duplicate financial or entitlement rows.
"""

from datetime import datetime
from typing import Any
from decimal import Decimal
from enum import Enum
import uuid
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base (mypy-friendly vs declarative_base())."""


# ---------------------------------------------------------------------------
# Webhook inbox + Refund (new tables for #47/#191/#478)
# ---------------------------------------------------------------------------


class WebhookEventStatus(str, Enum):
    """Processing state of a received Stripe webhook event."""

    PROCESSING = "processing"  # Claimed; handler executing.
    PROCESSED = "processed"  # Handler completed successfully.
    FAILED = "failed"  # Handler failed; eligible for bounded retry.


class StripeWebhookEvent(Base):
    """Durable inbox row for Stripe webhook events (#47).

    The unique constraint on ``event_id`` makes event-level idempotency
    database-backed instead of process-local: concurrent deliveries and
    post-restart replays race on the same unique key, and only one claim
    wins. FAILED rows are reclaimed up to ``max_attempts`` times so a
    crashed handler can be retried by Stripe's next delivery.
    """

    __tablename__ = "stripe_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[WebhookEventStatus] = mapped_column(
        SQLEnum(WebhookEventStatus), default=WebhookEventStatus.PROCESSING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )


class RefundStatus(str, Enum):
    PROCESSED = "processed"
    REJECTED = "rejected"  # Refund could not be applied (bounds violation).


class Refund(Base):
    """A Stripe refund, recorded idempotently (#191/#478).

    ``refund_id`` (the Stripe refund id) is unique, so replayed
    ``charge.refunded`` / ``refund.created`` events can never create a
    second record or apply a refund twice.
    """

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    refund_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[RefundStatus] = mapped_column(
        SQLEnum(RefundStatus), default=RefundStatus.PROCESSED, nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


# ---------------------------------------------------------------------------
# SubscriptionStatus enum (moved from before Subscription class)
# ---------------------------------------------------------------------------


class SubscriptionStatus(str, Enum):
    """Server-enforced subscription lifecycle state (#190/#482).

    Legal transitions (see SUBSCRIPTION_TRANSITIONS in services.py):
    ACTIVE -> CANCELLED (cancel), CANCELLED -> ACTIVE (re-subscribe /
    Stripe reactivation). ``is_active`` mirrors this state and is only
    ever written through the same transition.
    """

    ACTIVE = "active"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Revenue Tiers (§3 of PRODUCT_VISION.md)
# ---------------------------------------------------------------------------


class RevenueTier(str, Enum):
    """User-facing subscription / purchase tiers.

    AVOD  — ad-supported, free. Creator earns from pool + ad-rev share.
    SVOD  — subscription at $7.99/mo. >=55% of net to creators.
    TVOD  — pay-per-view, per-title purchase.
    """

    AVOD = "avod"
    SVOD = "svod"
    TVOD = "tvod"


class Subscription(Base):
    """A user's subscription to a revenue tier.

    Every user starts at AVOD (free). Upgrading to SVOD activates
    recurring billing; TVOD purchases are one-off and tracked in
    Purchase records, not here.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    tier: Mapped[RevenueTier] = mapped_column(
        SQLEnum(RevenueTier), default=RevenueTier.AVOD, nullable=False
    )
    monthly_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", nullable=False, comment="ISO 4217; immutable once set"
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        SQLEnum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE, nullable=False, index=True
    )
    last_stripe_event_ts: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Epoch seconds of the newest Stripe event applied; monotonic guard (#482).",
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    renewal_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Purchases (TVOD — per-title)
# ---------------------------------------------------------------------------


class Purchase(Base):
    """A one-off TVOD (pay-per-view) purchase of a title."""

    __tablename__ = "purchases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", nullable=False, comment="ISO 4217; immutable once set"
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        comment="Prevents duplicate charges from retried requests.",
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        comment="Prevents duplicate entitlements from replayed payment events (#481).",
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="Set exactly once when a TVOD refund is applied."
    )
    purchased_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    __table_args__ = (Index("idx_purchase_user_content", "user_id", "content_id", unique=True),)


# ---------------------------------------------------------------------------
# Invoicing
# ---------------------------------------------------------------------------


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class Invoice(Base):
    """Billing invoice — one per billing cycle or TVOD purchase."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True, index=True
    )
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, comment="Immutable after creation (#191)."
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", nullable=False, comment="ISO 4217; immutable once set"
    )
    creator_share_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        default=Decimal("0.00"),
        comment="Portion of this invoice allocated to creators (>=55% of net for SVOD).",
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        SQLEnum(InvoiceStatus), default=InvoiceStatus.PENDING
    )
    stripe_invoice_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        comment="Stripe invoice id; unique so invoice.paid replays cannot duplicate rows (#191).",
    )
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        default=Decimal("0.00"),
        nullable=False,
        comment="Cumulative refunds applied; bounded by amount via check constraint.",
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    __table_args__ = (
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name="ck_invoice_refund_bounds",
        ),
    )


# ---------------------------------------------------------------------------
# Living-Wage Floor (§2.1)
# ---------------------------------------------------------------------------


class RegionFloor(Base):
    """Per-region living-wage floor rate.

    The effective floor for a creator = tier_weight * regional_index *
    minutes_consumed (quality-adjusted). This is a minimum guarantee,
    not a cap — outsized performers earn far more.

    Admin-editable; reviewed quarterly.
    """

    __tablename__ = "region_floors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    region_code: Mapped[str] = mapped_column(
        String(10), nullable=False, unique=True, comment="ISO 3166-1 alpha-2 or custom code"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, comment="ISO 4217")
    floor_low: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, comment="Minimum per finished minute"
    )
    floor_high: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, comment="Maximum per finished minute"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Creator Pool (§2.2)
# ---------------------------------------------------------------------------


class CreatorPoolEntry(Base):
    """Tracks the Creator Pool balance and its redistribution.

    Each payout cycle, 15% of net revenue flows into the pool. The pool
    is redistributed pro-rata toward creators below their floor, weighted
    toward emerging studios. Top earners contribute more and draw nothing
    until the floor is broadly met.
    """

    __tablename__ = "creator_pool_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    cycle_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cycle_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    net_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    pool_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), default=Decimal("0.1500"), comment="Default 15%"
    )
    pool_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, comment="= net_revenue * pool_percentage"
    )
    redistributed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


class CreatorPoolDistribution(Base):
    """A single creator's share of the Creator Pool for one cycle."""

    __tablename__ = "creator_pool_distributions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pool_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("creator_pool_entries.id"), nullable=False, index=True
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    floor_deficit: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        comment="How far below floor before this distribution",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


# ---------------------------------------------------------------------------
# Milestone-Tranched Funding (§2.3)
# ---------------------------------------------------------------------------


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    MISSED = "missed"
    KILLED = "killed"


class Milestone(Base):
    """A creator commitment milestone.

    Large creator commitments are NOT paid upfront. Funds release in
    tranches 10/20/30/40 tied to verified milestones (script, animatic,
    first cut, final). Each tranche has a kill clause: miss a milestone,
    remaining tranches pause and funds revert to the pool.
    """

    __tablename__ = "milestones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_title: Mapped[str] = mapped_column(String(255), nullable=False)
    total_commitment: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[MilestoneStatus] = mapped_column(
        SQLEnum(MilestoneStatus), default=MilestoneStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    tranches = relationship(
        "MilestoneTranche", back_populates="milestone", order_by="MilestoneTranche.tranche_number"
    )


class TrancheStatus(str, Enum):
    LOCKED = "locked"  # Not yet releasable
    RELEASED = "released"  # Funds disbursed
    REVERTED = "reverted"  # Killed — funds returned to Creator Pool


class MilestoneTranche(Base):
    """A single tranche of milestone-tranched funding.

    Tranches are 10/20/30/40 of total_commitment. Each tranche releases
    only when its milestone is verified. If a milestone is killed, all
    unreleased tranches revert to the Creator Pool.
    """

    __tablename__ = "milestone_tranches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("milestones.id"), nullable=False, index=True
    )
    tranche_number: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1-4, representing 10/20/30/40%"
    )
    percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, comment="10.00, 20.00, 30.00, or 40.00"
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, comment="= milestone.total_commitment * percentage"
    )
    status: Mapped[TrancheStatus] = mapped_column(
        SQLEnum(TrancheStatus), default=TrancheStatus.LOCKED
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    milestone = relationship("Milestone", back_populates="tranches")

    __table_args__ = (
        CheckConstraint(
            "tranche_number >= 1 AND tranche_number <= 4",
            name="ck_tranche_number_range",
        ),
        Index("idx_tranche_milestone_number", "milestone_id", "tranche_number", unique=True),
    )


# ---------------------------------------------------------------------------
# Payout Ledger (§4)
# ---------------------------------------------------------------------------


class PayoutStatus(str, Enum):
    ACCRUED = "accrued"  # Earned but not yet transferred
    TRANSFERRING = "transferring"  # Stripe Connect transfer in flight
    COMPLETED = "completed"  # Successfully transferred
    FAILED = "failed"  # Transfer failed; will retry


class PayoutLedger(Base):
    """The canonical ledger for all creator payouts.

    Each entry is keyed by an idempotency_key so that retried webhooks
    or retried payout runs cannot double-pay a creator. Payouts go
    through Stripe Connect so creators receive funds in their own accounts.
    """

    __tablename__ = "payout_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[PayoutStatus] = mapped_column(
        SQLEnum(PayoutStatus), default=PayoutStatus.ACCRUED
    )
    breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        comment="JSON breakdown: {floor_payment, svod_share, avod_share, pool_top_up, tvod_share}",
    )
    stripe_transfer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_account_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Creator's Stripe Connect account"
    )
    cycle_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cycle_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accrued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())

    __table_args__ = (Index("idx_payout_creator_cycle", "creator_id", "cycle_start", "cycle_end"),)
