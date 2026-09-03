"""Billing-service schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubscriptionTierCreate(BaseModel):
    name: str = Field(..., pattern="^(basic|premium|family)$")
    jurisdiction: str
    price_cents: int = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    tax_rate: float = Field(default=0.2, ge=0, le=1)
    trial_days: int = Field(default=0, ge=0)
    cooling_off_days: int = Field(default=14, ge=0)
    refund_days: int = Field(default=14, ge=0)


class SubscriptionTierResponse(BaseModel):
    id: UUID
    name: str
    jurisdiction: str
    price_cents: int
    currency: str
    tax_rate: float
    trial_days: int
    cooling_off_days: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
