"""Integration tests for billing."""
from app.models.subscription_tier import SubscriptionTier

def test_tier_create():
    tier = SubscriptionTier(name="basic", jurisdiction="EU", price_cents=999, currency="EUR", tax_rate=0.2, cooling_off_days=14)
    assert tier.price_cents == 999
    assert tier.cooling_off_days == 14

def test_tier_tax_inclusive():
    tier = SubscriptionTier(name="premium", jurisdiction="IN", price_cents=1999, currency="INR", tax_rate=0.18, cooling_off_days=14)
    assert tier.tax_rate == 0.18
