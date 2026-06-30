"""Billing service API routes.

Exposes the Sustenance Engine endpoints:
  - Subscription management (AVOD/SVOD/TVOD)
  - TVOD per-title purchases
  - Living-wage floor lookup
  - Creator Pool status
  - Milestone-tranched funding management
  - Payout ledger history
"""
from decimal import Decimal
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, Body, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import (
    SubscriptionRepository,
    PurchaseRepository,
    InvoiceRepository,
    RegionFloorRepository,
    CreatorPoolRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
)
from app.services import BillingService, BillingError, TierInvalidError


router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------

async def get_billing_service(db: AsyncSession = Depends(get_db)) -> BillingService:
    """Wire up BillingService with all its repositories."""
    return BillingService(
        sub_repo=SubscriptionRepository(db),
        purchase_repo=PurchaseRepository(db),
        inv_repo=InvoiceRepository(db),
        floor_repo=RegionFloorRepository(db),
        pool_repo=CreatorPoolRepository(db),
        milestone_repo=MilestoneRepository(db),
        payout_repo=PayoutLedgerRepository(db),
    )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    tier: str = Field(..., description="Revenue tier: avod, svod, or tvod")


class PurchaseRequest(BaseModel):
    user_id: UUID
    content_id: UUID
    price: Decimal = Field(..., gt=0, description="Price in USD")


class CreateMilestoneRequest(BaseModel):
    creator_id: UUID
    project_title: str
    total_commitment: Decimal = Field(..., gt=0)


class ReleaseTrancheRequest(BaseModel):
    tranche_number: int = Field(..., ge=1, le=4)


class SubscriptionResponse(BaseModel):
    user_id: UUID
    tier: str
    monthly_price: str
    is_active: bool


# ---------------------------------------------------------------------------
# Subscription routes
# ---------------------------------------------------------------------------

@router.get("/subscription/{user_id}", response_model=SubscriptionResponse)
async def get_subscription(
    user_id: UUID,
    service: BillingService = Depends(get_billing_service),
):
    """Get a user's current subscription details."""
    sub = await service.get_subscription(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return SubscriptionResponse(
        user_id=sub.user_id,
        tier=sub.tier.value,
        monthly_price=str(sub.monthly_price),
        is_active=sub.is_active,
    )


@router.post("/subscribe/{user_id}")
async def subscribe(
    user_id: UUID,
    request: SubscribeRequest,
    service: BillingService = Depends(get_billing_service),
):
    """Subscribe or upgrade to a revenue tier (AVOD/SVOD/TVOD)."""
    try:
        sub = await service.subscribe(user_id, request.tier)
    except TierInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "subscribed", "tier": sub.tier.value, "monthly_price": str(sub.monthly_price)}


@router.post("/cancel/{user_id}")
async def cancel_subscription(
    user_id: UUID,
    service: BillingService = Depends(get_billing_service),
):
    """Cancel a subscription (reverts to AVOD free tier)."""
    sub = await service.cancel_subscription(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "cancelled", "tier": sub.tier.value}


# ---------------------------------------------------------------------------
# TVOD purchases
# ---------------------------------------------------------------------------

@router.post("/purchase")
async def purchase_title(
    request: PurchaseRequest,
    service: BillingService = Depends(get_billing_service),
):
    """Record a pay-per-view (TVOD) purchase."""
    try:
        purchase = await service.purchase_title(request.user_id, request.content_id, request.price)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"purchase_id": str(purchase.id), "status": "completed"}


# ---------------------------------------------------------------------------
# Sustenance Engine — Floor
# ---------------------------------------------------------------------------

@router.get("/floor/{region_code}")
async def get_floor(
    region_code: str,
    service: BillingService = Depends(get_billing_service),
):
    """Get the living-wage floor for a region."""
    floor = await service.get_floor(region_code)
    if not floor:
        raise HTTPException(status_code=404, detail=f"No floor configured for region '{region_code}'")
    return {
        "region_code": floor.region_code,
        "currency": floor.currency,
        "floor_low": str(floor.floor_low),
        "floor_high": str(floor.floor_high),
    }


@router.get("/floors")
async def list_floors(
    service: BillingService = Depends(get_billing_service),
):
    """List all regional floor configurations."""
    floors = await service.list_floors()
    return [
        {
            "region_code": f.region_code,
            "currency": f.currency,
            "floor_low": str(f.floor_low),
            "floor_high": str(f.floor_high),
        }
        for f in floors
    ]


# ---------------------------------------------------------------------------
# Sustenance Engine — Creator Pool
# ---------------------------------------------------------------------------

@router.get("/pool")
async def get_pool_status(
    service: BillingService = Depends(get_billing_service),
):
    """Get the current Creator Pool balance and latest cycle info."""
    pool = await service.get_pool_status()
    if not pool:
        return {"status": "no_cycles_yet"}
    return {
        "cycle_start": pool.cycle_start.isoformat(),
        "cycle_end": pool.cycle_end.isoformat(),
        "net_revenue": str(pool.net_revenue),
        "pool_percentage": str(pool.pool_percentage),
        "pool_amount": str(pool.pool_amount),
        "redistributed_amount": str(pool.redistributed_amount),
    }


# ---------------------------------------------------------------------------
# Sustenance Engine — Milestones & Tranches
# ---------------------------------------------------------------------------

@router.post("/milestones")
async def create_milestone(
    request: CreateMilestoneRequest,
    service: BillingService = Depends(get_billing_service),
):
    """Create a milestone commitment with 10/20/30/40 tranched funding."""
    try:
        ms = await service.create_milestone(
            request.creator_id, request.project_title, request.total_commitment,
        )
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "milestone_id": str(ms.id),
        "status": ms.status.value,
        "total_commitment": str(ms.total_commitment),
        "tranches": "10/20/30/40 (all locked)",
    }


@router.post("/milestones/{milestone_id}/release")
async def release_tranche(
    milestone_id: UUID,
    request: ReleaseTrancheRequest,
    service: BillingService = Depends(get_billing_service),
):
    """Release a tranche after milestone verification."""
    try:
        tranche = await service.release_tranche(milestone_id, request.tranche_number)
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "tranche_number": tranche.tranche_number,
        "percentage": str(tranche.percentage),
        "amount": str(tranche.amount),
        "status": tranche.status.value,
    }


@router.post("/milestones/{milestone_id}/kill")
async def kill_milestone(
    milestone_id: UUID,
    service: BillingService = Depends(get_billing_service),
):
    """Kill a milestone — revert all unreleased tranches to the Creator Pool."""
    try:
        ms = await service.kill_milestone(milestone_id)
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "milestone_id": str(ms.id),
        "status": ms.status.value,
        "message": "All unreleased tranches reverted to Creator Pool.",
    }


# ---------------------------------------------------------------------------
# Payout history
# ---------------------------------------------------------------------------

@router.get("/payouts/{creator_id}")
async def get_payout_history(
    creator_id: UUID,
    service: BillingService = Depends(get_billing_service),
):
    """Get a creator's full payout ledger history."""
    payouts = await service.get_payout_history(creator_id)
    return [
        {
            "id": str(p.id),
            "amount": str(p.amount),
            "currency": p.currency,
            "status": p.status.value,
            "cycle_start": p.cycle_start.isoformat() if p.cycle_start else None,
            "cycle_end": p.cycle_end.isoformat() if p.cycle_end else None,
        }
        for p in payouts
    ]


# ---------------------------------------------------------------------------
# Creator share (utility endpoint)
# ---------------------------------------------------------------------------

@router.get("/creator-share")
async def calculate_creator_share(
    svod_revenue: Decimal = Query(..., description="Total SVOD revenue for the period"),
):
    """Calculate the minimum creator share from SVOD revenue (>=55% floor)."""
    share = BillingService.calculate_creator_share(svod_revenue)
    return {
        "svod_revenue": str(svod_revenue),
        "creator_share_floor": str(share),
        "percentage": "55%",
        "note": "Actual payouts may be higher with Creator Pool top-ups.",
    }
