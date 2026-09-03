"""Billing-service subscription tier - jurisdiction-aware, tax-inclusive pricing, trial rules."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SubscriptionTier(Base):
    """Subscription tier per jurisdiction - tax-inclusive, trial, cancellation, refund, price change."""

    __tablename__ = "subscription_tiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), nullable=False)  # basic, premium, family
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # EU, US, IN, GLOBAL
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)  # tax-inclusive
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # USD, EUR, INR
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False)  # VAT 20%, GST 18%, etc.
    trial_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cooling_off_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)  # EU 14
    cancellation_policy: Mapped[str] = mapped_column(String(20), default="easy_cancel", nullable=False)  # easy_cancel, state_specific
    refund_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)  # EU 14
    price_change_notice_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)  # EU 30
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (Index("idx_tier_jurisdiction", "jurisdiction", "name", unique=True),)
