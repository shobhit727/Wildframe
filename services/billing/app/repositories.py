"""Billing service repositories."""
from uuid import UUID
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Subscription, Invoice

class SubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def get_by_user(self, user_id: UUID) -> Optional[Subscription]:
        stmt = select(Subscription).where(Subscription.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    async def create(self, user_id: UUID, tier: str, monthly_price: float):
        sub = Subscription(user_id=user_id, tier=tier, monthly_price=monthly_price)
        self.session.add(sub)
        await self.session.flush()
        return sub

class InvoiceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    async def create(self, subscription_id: UUID, user_id: UUID, amount: float):
        inv = Invoice(subscription_id=subscription_id, user_id=user_id, amount=amount)
        self.session.add(inv)
        await self.session.flush()
        return inv
