"""Payout schemas."""

from uuid import UUID

from pydantic import BaseModel, Field


class PayoutCreate(BaseModel):
    creator_id: UUID
    amount_cents: int = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    schedule: str = Field(default="net-30", pattern="^(net-30|net-45|net-60)$")
