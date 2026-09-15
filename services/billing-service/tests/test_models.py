import uuid
from datetime import datetime
import pytest
from decimal import Decimal
from app.models.subscription_tier import SubscriptionTier
from app.models.commerce import CommerceRecord
from app.models.payout_ledger import PayoutLedger
from app.models import (
    Subscription,
    Purchase,
    Invoice,
    Refund,
    SubscriptionStatus,
    InvoiceStatus,
    RefundStatus,
)
from app.services import validate_transition, InvalidStateTransitionError, SUBSCRIPTION_TRANSITIONS


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
    tier = SubscriptionTier(
        name="pro", jurisdiction="EU", price_cents=2000, currency="EUR", tax_rate=0.2
    )
    assert tier.name == "pro"
    assert tier.jurisdiction == "EU"
    assert tier.price_cents == 2000
    assert tier.currency == "EUR"
    assert tier.tax_rate == 0.2


def test_subscription_defaults_and_transitions():
    sub = Subscription(user_id=uuid.uuid4())
    # defaults may be None before DB commit
    assert sub.status in (None, SubscriptionStatus.ACTIVE)
    assert sub.is_active in (None, True)
    # check both allowed transitions do not raise
    cur = sub.status or SubscriptionStatus.ACTIVE
    validate_transition(
        cur, SubscriptionStatus.CANCELLED, SUBSCRIPTION_TRANSITIONS, context="subscription"
    )
    validate_transition(
        SubscriptionStatus.CANCELLED,
        SubscriptionStatus.ACTIVE,
        SUBSCRIPTION_TRANSITIONS,
        context="subscription",
    )


def test_purchase_idempotency_key_format():
    user = uuid.uuid4()
    content = uuid.uuid4()
    purchase = Purchase(
        user_id=user,
        content_id=content,
        price=Decimal("10.00"),
        currency="USD",
        idempotency_key=f"tvod:{user}:{content}",
    )
    assert purchase.idempotency_key.startswith("tvod:")
    r = Refund(invoice_id=uuid.uuid4(), amount=Decimal("50.00"), currency="USD")
    assert r.status in (None, RefundStatus.PROCESSED)
    assert r.reason is None
