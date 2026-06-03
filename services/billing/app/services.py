"""Billing service business logic."""
from uuid import UUID
from app.repositories import SubscriptionRepository, InvoiceRepository

class BillingService:
    def __init__(self, sub_repo: SubscriptionRepository, inv_repo: InvoiceRepository):
        self.sub_repo = sub_repo
        self.inv_repo = inv_repo
    
    async def get_subscription(self, user_id: UUID):
        """Get user subscription."""
        return await self.sub_repo.get_by_user(user_id)
    
    async def upgrade_subscription(self, user_id: UUID, tier: str, monthly_price: float):
        """Upgrade user subscription tier."""
        sub = await self.sub_repo.get_by_user(user_id)
        if sub:
            sub.tier = tier
            sub.monthly_price = monthly_price
        return sub
