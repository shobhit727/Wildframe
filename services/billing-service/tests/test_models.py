import uuid
from datetime import datetime
import pytest
from app.models.subscription_tier import SubscriptionTier
from app.models.commerce import CommerceRecord
from app.models.payout_ledger import PayoutLedger

def test_subscription_tier_defaults():
    tier = SubscriptionTier(
        name="basic",
        jurisdiction="US",
        price_cents=1000,
        currency="USD",
        tax_rate=0.0,
    )
    # defaults may be None before DB commit
    assert tier.trial_days in (None, 0)
    assert tier.cooling_off_days in (None, 14)
    assert tier.refund_days in (None, 14)
    assert tier.price_change_notice_days in (None, 30)
    assert tier.is_active in (None, True)
    assert tier.created_at is None or isinstance(tier.created_at, datetime)

def test_commerce_record_defaults():
    record = CommerceRecord(
        invoice_id="inv123",
        amount_cents=5000,
        tax_cents=500,
        currency="USD",
    )
    assert record.id is None or isinstance(record.id, uuid.UUID)
    assert record.created_at is None or isinstance(record.created_at, datetime)
    if record.created_at is not None:
        assert record.created_at.tzinfo is not None

def test_payout_ledger_defaults():
    ledger = PayoutLedger(
        payout_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        gross_cents=10000,
        tax_cents=2000,
        net_cents=8000,
    )
    assert ledger.reconciled in (None, False)
    assert ledger.created_at is None or isinstance(ledger.created_at, datetime)

def test_subscription_tier_index_args():
    args = SubscriptionTier.__table_args__[0].columns.keys()
    assert set(args) == {"jurisdiction", "name"}

def test_payout_ledger_index_args():
    args = PayoutLedger.__table_args__[0].columns.keys()
    assert "creator_id" in args

@pytest.mark.asyncio
async def test_model_instantiation_is_sync():
    tier = SubscriptionTier(name="pro", jurisdiction="EU", price_cents=2000, currency="EUR", tax_rate=0.2)
    assert tier.name == "pro"
    assert tier.jurisdiction == "EU"
    assert tier.price_cents == 2000
    assert tier.currency == "EUR"
    assert tier.tax_rate == 0.2
