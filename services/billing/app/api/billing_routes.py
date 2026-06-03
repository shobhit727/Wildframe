"""Billing service API routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.repositories import SubscriptionRepository, InvoiceRepository
from app.services import BillingService

router = APIRouter(prefix="/billing", tags=["billing"])

async def get_billing_service(db: AsyncSession = Depends(get_db_session)) -> BillingService:
    return BillingService(SubscriptionRepository(db), InvoiceRepository(db))

@router.get("/subscription/{user_id}")
async def get_subscription(user_id: UUID, service: BillingService = Depends(get_billing_service)):
    """Get user subscription."""
    sub = await service.get_subscription(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"user_id": str(user_id), "tier": sub.tier, "price": sub.monthly_price}

@router.post("/upgrade/{user_id}")
async def upgrade_subscription(user_id: UUID, tier: str = Body(...), 
                              service: BillingService = Depends(get_billing_service)):
    """Upgrade subscription tier."""
    prices = {"free": 0, "basic": 4.99, "premium": 9.99, "family": 14.99}
    await service.upgrade_subscription(user_id, tier, prices.get(tier, 0))
    return {"status": "upgraded", "tier": tier}
