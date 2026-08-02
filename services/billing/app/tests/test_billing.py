"""Billing service tests."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import BillingService


@pytest.mark.asyncio
async def test_get_subscription(db: AsyncSession):
    """Test getting subscription."""
    user_id = uuid4()
    service = BillingService(None, None)

    sub = await service.get_subscription(user_id)
    assert sub is None or hasattr(sub, "tier")


@pytest.mark.asyncio
async def test_upgrade_subscription(db: AsyncSession):
    """Test upgrading subscription."""
    user_id = uuid4()
    service = BillingService(None, None)

    await service.upgrade_subscription(user_id, "premium", 9.99)
