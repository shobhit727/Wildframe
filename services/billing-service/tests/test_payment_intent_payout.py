from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.webhook_routes import _handle_payment_intent_succeeded
from app.models import InvoiceStatus
from app.services import BillingError, BillingService, DuplicatePayoutError


def _pi_event(
    pi_id="pi_test_123", amount=999, currency="usd", metadata=None, event_id="evt_test_123"
):
    return {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "created": 1700000000,
        "data": {
            "object": {
                "id": pi_id,
                "amount": amount,
                "currency": currency,
                "metadata": metadata or {},
            }
        },
    }


def _mock_service():
    svc = MagicMock(spec=BillingService)
    svc.purchase_repo = MagicMock()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    svc.inv_repo = MagicMock()
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=None)
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), uuid4()))
    svc.accrue_payout = AsyncMock(return_value=MagicMock())
    return svc


@pytest.mark.asyncio
async def test_first_delivery_creates_payout_and_reconciles_invoice():
    pi_id = "pi_first_123"
    content_id = uuid4()
    creator_id = uuid4()
    purchase_id = uuid4()
    purchase = MagicMock()
    purchase.content_id = content_id
    purchase.price = Decimal("9.99")
    purchase.currency = "USD"
    purchase.id = purchase_id
    purchase.purchased_at = None
    invoice = MagicMock()
    invoice.amount = Decimal("9.99")
    invoice.currency = "USD"
    invoice.status = InvoiceStatus.PENDING
    invoice.issued_at = None
    invoice.paid_at = None
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    payout = MagicMock()
    svc.accrue_payout = AsyncMock(return_value=payout)
    evt = _pi_event(pi_id=pi_id, amount=999, currency="usd", metadata={}, event_id="evt_first")
    await _handle_payment_intent_succeeded(evt, svc)
    svc._fetch_content_details.assert_awaited_once_with(content_id)
    svc.accrue_payout.assert_awaited_once()
    kwargs = svc.accrue_payout.await_args.kwargs
    assert kwargs["creator_id"] == creator_id
    assert kwargs["amount"] == BillingService.calculate_creator_share(Decimal("9.99"))
    assert kwargs["currency"] == "USD"
    assert kwargs["idempotency_key"] == f"pi:{pi_id}"
    assert kwargs["breakdown"]["content_id"] == str(content_id)
    assert kwargs["breakdown"]["payment_intent"] == pi_id
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.paid_at is not None


@pytest.mark.asyncio
async def test_duplicate_webhook_is_idempotent():
    pi_id = "pi_dup_123"
    content_id = uuid4()
    creator_id = uuid4()
    svc = _mock_service()
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    purchase = MagicMock()
    purchase.content_id = content_id
    purchase.price = Decimal("9.99")
    purchase.currency = "USD"
    purchase.id = uuid4()
    purchase.purchased_at = None
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=None)
    existing = MagicMock()
    svc.accrue_payout = AsyncMock(return_value=existing)
    evt = _pi_event(pi_id=pi_id, amount=999, currency="usd", metadata={})
    await _handle_payment_intent_succeeded(evt, svc)
    await _handle_payment_intent_succeeded(evt, svc)
    assert svc.accrue_payout.await_count == 2
    for call in svc.accrue_payout.await_args_list:
        assert call.kwargs["idempotency_key"] == f"pi:{pi_id}"
        assert call.kwargs["amount"] == BillingService.calculate_creator_share(Decimal("9.99"))


@pytest.mark.asyncio
async def test_conflicting_duplicate_raises():
    pi_id = "pi_conflict_123"
    content_id = uuid4()
    creator_id = uuid4()
    svc = _mock_service()
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    purchase = MagicMock()
    purchase.content_id = content_id
    purchase.price = Decimal("9.99")
    purchase.currency = "USD"
    purchase.id = uuid4()
    purchase.purchased_at = None
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.accrue_payout = AsyncMock(
        side_effect=DuplicatePayoutError(
            "Payout pi:pi_conflict_123 already exists with amount 1.00, cannot re-accrue as 5.00"
        )
    )
    evt = _pi_event(pi_id=pi_id, amount=999, currency="usd", metadata={})
    with pytest.raises(DuplicatePayoutError):
        await _handle_payment_intent_succeeded(evt, svc)


@pytest.mark.asyncio
async def test_missing_metadata_raises():
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    evt = _pi_event(pi_id="pi_missing_123", amount=999, currency="usd", metadata={})
    with pytest.raises(BillingError, match="Missing content_id"):
        await _handle_payment_intent_succeeded(evt, svc)
    svc.accrue_payout.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_metadata_with_purchase_fallback_succeeds():
    pi_id = "pi_meta_fallback"
    content_id = uuid4()
    creator_id = uuid4()
    purchase = MagicMock()
    purchase.content_id = content_id
    purchase.price = Decimal("5.00")
    purchase.currency = "USD"
    purchase.id = uuid4()
    purchase.purchased_at = None
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("5.00"), creator_id))
    evt = _pi_event(pi_id=pi_id, amount=500, currency="usd", metadata={})
    await _handle_payment_intent_succeeded(evt, svc)
    svc.accrue_payout.assert_awaited_once()


@pytest.mark.asyncio
async def test_amount_mismatch_raises():
    pi_id = "pi_amt_mismatch"
    content_id = uuid4()
    creator_id = uuid4()
    svc = _mock_service()
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    evt = _pi_event(
        pi_id=pi_id, amount=100, currency="usd", metadata={"content_id": str(content_id)}
    )
    with pytest.raises(BillingError, match="Amount mismatch"):
        await _handle_payment_intent_succeeded(evt, svc)
    svc.accrue_payout.assert_not_awaited()


@pytest.mark.asyncio
async def test_currency_mismatch_raises():
    content_id = uuid4()
    creator_id = uuid4()
    svc = _mock_service()
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    evt = _pi_event(
        pi_id="pi_cur_mismatch",
        amount=999,
        currency="eur",
        metadata={"content_id": str(content_id)},
    )
    with pytest.raises(BillingError, match="Currency mismatch"):
        await _handle_payment_intent_succeeded(evt, svc)
    svc.accrue_payout.assert_not_awaited()


@pytest.mark.asyncio
async def test_ledger_creation_uses_creator_share():
    pi_id = "pi_ledger_123"
    content_id = uuid4()
    creator_id = uuid4()
    svc = _mock_service()
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("20.00"), creator_id))
    evt = _pi_event(
        pi_id=pi_id, amount=2000, currency="usd", metadata={"content_id": str(content_id)}
    )
    await _handle_payment_intent_succeeded(evt, svc)
    svc.accrue_payout.assert_awaited_once()
    kwargs = svc.accrue_payout.await_args.kwargs
    assert kwargs["amount"] == Decimal("11.00")
    assert kwargs["creator_id"] == creator_id
    assert kwargs["currency"] == "USD"


@pytest.mark.asyncio
async def test_invoice_amount_mismatch_raises():
    pi_id = "pi_inv_mismatch"
    content_id = uuid4()
    creator_id = uuid4()
    purchase = MagicMock()
    purchase.content_id = content_id
    purchase.price = Decimal("9.99")
    purchase.currency = "USD"
    purchase.id = uuid4()
    purchase.purchased_at = None
    invoice = MagicMock()
    invoice.amount = Decimal("5.00")
    invoice.currency = "USD"
    invoice.status = InvoiceStatus.PENDING
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    evt = _pi_event(pi_id=pi_id, amount=999, currency="usd", metadata={})
    with pytest.raises(BillingError, match="Invoice amount mismatch"):
        await _handle_payment_intent_succeeded(evt, svc)
    svc.accrue_payout.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_content_id_path_without_purchase():
    pi_id = "pi_meta_path"
    content_id = uuid4()
    creator_id = uuid4()
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator_id))
    evt = _pi_event(
        pi_id=pi_id, amount=999, currency="usd", metadata={"content_id": str(content_id)}
    )
    await _handle_payment_intent_succeeded(evt, svc)
    svc._fetch_content_details.assert_awaited_once_with(content_id)
    svc.accrue_payout.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_content_id_in_metadata_raises():
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    evt = _pi_event(
        pi_id="pi_invalid", amount=999, currency="usd", metadata={"content_id": "not-a-uuid"}
    )
    with pytest.raises(BillingError, match="Invalid content_id"):
        await _handle_payment_intent_succeeded(evt, svc)


@pytest.mark.asyncio
async def test_content_not_found_raises():
    content_id = uuid4()
    svc = _mock_service()
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    svc._fetch_content_details = AsyncMock(
        side_effect=ValueError(f"Content {content_id} not found")
    )
    evt = _pi_event(
        pi_id="pi_notfound", amount=999, currency="usd", metadata={"content_id": str(content_id)}
    )
    with pytest.raises(BillingError, match="not found"):
        await _handle_payment_intent_succeeded(evt, svc)
