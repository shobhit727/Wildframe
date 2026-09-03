"""Tests for subscription."""

from app.models.subscription_tier import SubscriptionTier
from app.schemas.billing import SubscriptionTierCreate


def test_tier_create():
    data = SubscriptionTierCreate(name="basic", jurisdiction="EU", price_cents=999)
    assert data.name == "basic"


def test_tier_model():
    rec = SubscriptionTier(
        name="premium", jurisdiction="US", price_cents=1999, currency="USD", tax_rate=0.2
    )
    assert rec.name == "premium"
