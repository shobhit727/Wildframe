"""Billing tiers routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.subscription_tier import SubscriptionTier
from app.schemas.billing import SubscriptionTierCreate, SubscriptionTierResponse

router = APIRouter(prefix="/tiers", tags=["billing-tiers"])


@router.post("", response_model=SubscriptionTierResponse, status_code=201)
async def create_tier(request: SubscriptionTierCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> SubscriptionTierResponse:
    tier = SubscriptionTier(**request.model_dump())
    db.add(tier)
    await db.flush()
    await db.commit()
    await db.refresh(tier)
    return SubscriptionTierResponse.model_validate(tier)
