"""Billing service models."""
from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Enum as SQLEnum, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class SubscriptionTier(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"
    FAMILY = "family"

class Subscription(Base):
    """User subscription."""
    __tablename__ = "subscriptions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    tier = Column(SQLEnum(SubscriptionTier), default=SubscriptionTier.FREE)
    monthly_price = Column(Float, default=0.0)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    renewal_date = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Invoice(Base):
    """Billing invoice."""
    __tablename__ = "invoices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    subscription_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    status = Column(String(50), default="pending")
    issued_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    due_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
