"""Final billing tests."""

from app.models.subscription_tier import SubscriptionTier
from app.models.commerce import CommerceRecord


def test_billing_final1():
    assert (
        SubscriptionTier(
            name="basic", jurisdiction="EU", price_cents=100, currency="EUR", tax_rate=0.2
        )
        is not None
    )


def test_billing_final2():
    assert (
        CommerceRecord(invoice_id="INV-2", amount_cents=100, tax_cents=20, currency="USD")
        is not None
    )
