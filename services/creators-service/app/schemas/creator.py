"""Pydantic v2 schemas for the Creators service."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.models import KYCStatus, MilestoneStatus, TrancheStatus, PayoutStatus


# ---------------------------------------------------------------- CreatorAccount
class CreatorAccountCreate(BaseModel):
    display_name: str = Field("", max_length=255)
    bio: str = Field("", max_length=2000)
    region_code: str = Field("US", max_length=8)
    currency: str = Field("USD", max_length=8)


class CreatorAccountUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=255)
    bio: Optional[str] = Field(None, max_length=2000)
    region_code: Optional[str] = Field(None, max_length=8)
    currency: Optional[str] = Field(None, max_length=8)
    stripe_connect_account_id: Optional[str] = Field(None, max_length=255)


class CreatorAccountResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str
    bio: str
    region_code: str
    currency: str
    stripe_connect_account_id: Optional[str]
    kyc_status: KYCStatus
    kyc_verified_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------- EffectiveFloor
class EffectiveFloorCreate(BaseModel):
    per_minute_amount: float = Field(..., ge=0)
    currency: str = Field("USD", max_length=8)
    reason: Optional[str] = Field(None, max_length=500)


class EffectiveFloorResponse(BaseModel):
    id: UUID
    creator_id: UUID
    per_minute_amount: float
    currency: str
    effective_from: datetime
    last_adjusted_at: Optional[datetime]
    reason: Optional[str]


# ----------------------------------------------------------- CreatorPoolBalance
class PoolContributionRequest(BaseModel):
    cents: int = Field(..., ge=0)


class CreatorPoolBalanceResponse(BaseModel):
    id: UUID
    creator_id: UUID
    accrued_cents: int
    contributed_cents: int
    last_payout_at: Optional[datetime]


# -------------------------------------------------------------------- Milestone
class MilestoneCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    total_cents: int = Field(0, ge=0)
    currency: str = Field("USD", max_length=8)
    goal: Optional[str] = Field(None, max_length=1000)


class MilestoneResponse(BaseModel):
    id: UUID
    title: str
    creator_id: UUID
    status: MilestoneStatus
    total_cents: int
    currency: str
    goal: Optional[str]
    kill_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------- MilestoneTranche
class TrancheCreate(BaseModel):
    threshold: int = Field(..., ge=0, le=100)
    amount_cents: int = Field(0, ge=0)
    release_condition: Optional[str] = Field(None, max_length=1000)


class MilestoneTrancheResponse(BaseModel):
    id: UUID
    milestone_id: UUID
    threshold: int
    amount_cents: int
    status: TrancheStatus
    release_condition: Optional[str]
    released_at: Optional[datetime]


# ----------------------------------------------------------------- PayoutLedger
class PayoutAccrualRequest(BaseModel):
    period_start: datetime
    period_end: datetime
    view_minutes: int = Field(0, ge=0)
    earned_cents: int = Field(0, ge=0)
    stripe_fee_cents: int = Field(0, ge=0)


class PayoutLedgerResponse(BaseModel):
    id: UUID
    creator_id: UUID
    idempotency_key: str
    period_start: datetime
    period_end: datetime
    view_minutes: int
    floor_cents: int
    pool_topup_cents: int
    share_cents: int
    stripe_fee_cents: int
    net_cents: int
    stripe_transfer_id: Optional[str]
    status: PayoutStatus
    created_at: datetime
