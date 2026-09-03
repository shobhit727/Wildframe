"""Creators-service onboarding - KYC/KYB, Stripe Connect, tax forms, living wage."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CreatorOnboarding(Base):
    __tablename__ = "creator_onboarding"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True, unique=True)
    kyc_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending, verified, rejected
    kyc_type: Mapped[str] = mapped_column(String(20), nullable=False)  # individual, entity
    stripe_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tax_form_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # W-8BEN, W-9, GST
    tax_form_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bank_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contract_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    living_wage_cents: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (Index("idx_onboarding_user", "user_id"),)
