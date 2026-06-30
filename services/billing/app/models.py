"""Billing service domain models.

Implements the Sustenance Engine architecture from PRODUCT_VISION.md:
  - Revenue tiers: AVOD (free, ad-supported), SVOD ($7.99/mo), TVOD (pay-per-view)
  - Living-wage floor per region (guaranteed minimum per finished minute)
  - Creator Pool (15% of net revenue, redistributed to creators below their floor)
  - Milestone-Tranched funding (10/20/30/40 with kill clauses)
  - Payout ledger with idempotency keys to prevent double-pay

Key invariant: >= 55% of net SVOD revenue goes to creators. This is
calculated BEFORE platform costs are deducted (contractual floor, not target).
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from enum import Enum

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    Enum as SQLEnum, ForeignKey, Index, Numeric, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    tier = Column(SQLEnum(RevenueTier), default=RevenueTier.AVOD, nullable=False)
    monthly_price = Column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    renewal_date = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Purchases (TVOD — per-title)
# ---------------------------------------------------------------------------

class Purchase(Base):
    """A one-off TVOD (pay-per-view) purchase of a title."""
    __tablename__ = "purchases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    price = Column(Numeric(10, 2), nullable=False)
    idempotency_key = Column(
        String(128), unique=True, nullable=False,
        comment="Prevents duplicate charges from retried requests.",
    )
    purchased_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_purchase_user_content", "user_id", "content_id", unique=True),
    )


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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True, index=True)
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    creator_share_amount = Column(
        Numeric(10, 2), default=Decimal("0.00"),
        comment="Portion of this invoice allocated to creators (>=55% of net for SVOD).",
    )
    status = Column(SQLEnum(InvoiceStatus), default=InvoiceStatus.PENDING)
    issued_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    due_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    region_code = Column(String(10), nullable=False, unique=True, comment="ISO 3166-1 alpha-2 or custom code")
    currency = Column(String(3), nullable=False, comment="ISO 4217")
    floor_low = Column(Numeric(10, 4), nullable=False, comment="Minimum per finished minute")
    floor_high = Column(Numeric(10, 4), nullable=False, comment="Maximum per finished minute")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    cycle_start = Column(DateTime, nullable=False)
    cycle_end = Column(DateTime, nullable=False)
    net_revenue = Column(Numeric(12, 2), nullable=False)
    pool_percentage = Column(Numeric(5, 4), default=Decimal("0.1500"), comment="Default 15%")
    pool_amount = Column(Numeric(12, 2), nullable=False, comment="= net_revenue * pool_percentage")
    redistributed_amount = Column(Numeric(12, 2), default=Decimal("0.00"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CreatorPoolDistribution(Base):
    """A single creator's share of the Creator Pool for one cycle."""
    __tablename__ = "creator_pool_distributions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pool_entry_id = Column(UUID(as_uuid=True), ForeignKey("creator_pool_entries.id"), nullable=False, index=True)
    creator_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    floor_deficit = Column(Numeric(10, 2), comment="How far below floor before this distribution")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    project_title = Column(String(255), nullable=False)
    total_commitment = Column(Numeric(12, 2), nullable=False)
    status = Column(SQLEnum(MilestoneStatus), default=MilestoneStatus.PENDING)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tranches = relationship("MilestoneTranche", back_populates="milestone", order_by="MilestoneTranche.tranche_number")


class TrancheStatus(str, Enum):
    LOCKED = "locked"          # Not yet releasable
    RELEASED = "released"      # Funds disbursed
    REVERTED = "reverted"      # Killed — funds returned to Creator Pool


class MilestoneTranche(Base):
    """A single tranche of milestone-tranched funding.

    Tranches are 10/20/30/40 of total_commitment. Each tranche releases
    only when its milestone is verified. If a milestone is killed, all
    unreleased tranches revert to the Creator Pool.
    """
    __tablename__ = "milestone_tranches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    milestone_id = Column(UUID(as_uuid=True), ForeignKey("milestones.id"), nullable=False, index=True)
    tranche_number = Column(Integer, nullable=False, comment="1-4, representing 10/20/30/40%")
    percentage = Column(Numeric(5, 2), nullable=False, comment="10.00, 20.00, 30.00, or 40.00")
    amount = Column(Numeric(12, 2), nullable=False, comment="= milestone.total_commitment * percentage")
    status = Column(SQLEnum(TrancheStatus), default=TrancheStatus.LOCKED)
    released_at = Column(DateTime, nullable=True)
    reverted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

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
    ACCRUED = "accrued"            # Earned but not yet transferred
    TRANSFERRING = "transferring"  # Stripe Connect transfer in flight
    COMPLETED = "completed"        # Successfully transferred
    FAILED = "failed"              # Transfer failed; will retry


class PayoutLedger(Base):
    """The canonical ledger for all creator payouts.

    Each entry is keyed by an idempotency_key so that retried webhooks
    or retried payout runs cannot double-pay a creator. Payouts go
    through Stripe Connect so creators receive funds in their own accounts.
    """
    __tablename__ = "payout_ledger"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creator_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    idempotency_key = Column(String(128), unique=True, nullable=False)
    status = Column(SQLEnum(PayoutStatus), default=PayoutStatus.ACCRUED)
    breakdown = Column(
        JSONB,
        comment="JSON breakdown: {floor_payment, svod_share, avod_share, pool_top_up, tvod_share}",
    )
    stripe_transfer_id = Column(String(255), nullable=True)
    stripe_account_id = Column(String(255), nullable=True, comment="Creator's Stripe Connect account")
    cycle_start = Column(DateTime, nullable=False)
    cycle_end = Column(DateTime, nullable=False)
    accrued_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    transferred_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_payout_creator_cycle", "creator_id", "cycle_start", "cycle_end"),
    )
