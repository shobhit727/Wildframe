from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.api.webhook_routes import _handle_refund, _process_single_refund_obj
from app.models import RefundStatus
from app.services import BillingService


def _refund_obj(
    refund_id="re_test_123",
    charge="ch_test_123",
    pi="pi_test_123",
    amount=500,
    currency="usd",
    metadata=None,
    reason=None,
):
    return {
        "id": refund_id,
        "charge": charge,
        "payment_intent": pi,
        "amount": amount,
        "currency": currency,
        "metadata": metadata or {},
        "reason": reason,
    }


def _charge_obj(
    charge_id="ch_test_123",
    pi="pi_test_123",
    invoice=None,
    currency="usd",
    metadata=None,
    refunds=None,
):
    base = {
        "id": charge_id,
        "payment_intent": pi,
        "currency": currency,
        "metadata": metadata or {},
        "invoice": invoice,
    }
    if refunds is not None:
        base["refunds"] = refunds
    return base


def _make_service():
    svc = BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )
    svc.refund_repo.get_by_refund_id = AsyncMock(return_value=None)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=True)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    svc.inv_repo.get = AsyncMock(return_value=None)
    svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=None)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=None)
    svc.inv_repo.get_latest_for_user = AsyncMock(return_value=None)
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    return svc


def _link_invoice_to_payment_intent(svc, invoice, payment_intent_id="pi_test_123"):
    purchase = MagicMock(id=uuid4())
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
    return payment_intent_id


@pytest.mark.asyncio
async def test_idempotent_duplicate_refund():
    svc = _make_service()
    existing = MagicMock(refund_id="re_dup_1")
    svc.refund_repo.get_by_refund_id = AsyncMock(return_value=existing)
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("no call")):
        result = await svc.process_refund("re_dup_1", "ch_1", Decimal("5.00"), "USD")
    assert result is existing
    svc.refund_repo.create.assert_not_awaited()
    svc.refund_repo.apply_to_invoice.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_refund_applies_to_invoice():
    svc = _make_service()
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("10.00"), currency="USD")
    svc.inv_repo.get = AsyncMock(return_value=invoice)
    payment_intent_id = _link_invoice_to_payment_intent(svc, invoice)
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("not found")):
        with patch("app.services.StripeClient.retrieve_charge", side_effect=Exception("not found")):
            result = await svc.process_refund(
                "re_partial_1", "ch_1", Decimal("3.00"), "USD",
                invoice_id=inv_id, payment_intent_id=payment_intent_id,
            )
    svc.refund_repo.apply_to_invoice.assert_awaited_once_with(inv_id, Decimal("3.00"))
    assert result.status == RefundStatus.PROCESSED if hasattr(result, "status") else True


@pytest.mark.asyncio
async def test_multiple_refunds_cumulative():
    svc = _make_service()
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("10.00"), currency="USD")
    svc.inv_repo.get = AsyncMock(return_value=invoice)
    payment_intent_id = _link_invoice_to_payment_intent(svc, invoice)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=True)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("nf")):
        with patch("app.services.StripeClient.retrieve_charge", side_effect=Exception("nf")):
            r1 = await svc.process_refund(
                "re_1", "ch_1", Decimal("4.00"), "USD",
                invoice_id=inv_id, payment_intent_id=payment_intent_id,
            )
            svc.refund_repo.get_by_refund_id = AsyncMock(return_value=None)
            r2 = await svc.process_refund(
                "re_2", "ch_1", Decimal("6.00"), "USD",
                invoice_id=inv_id, payment_intent_id=payment_intent_id,
            )
    assert svc.refund_repo.apply_to_invoice.await_count == 2
    assert r1.refund_id == "re_1"
    assert r2.refund_id == "re_2"


@pytest.mark.asyncio
async def test_amount_exceeds_invoice_rejected():
    svc = _make_service()
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("5.00"), currency="USD")
    svc.inv_repo.get = AsyncMock(return_value=invoice)
    payment_intent_id = _link_invoice_to_payment_intent(svc, invoice)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=False)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("nf")):
        with patch("app.services.StripeClient.retrieve_charge", side_effect=Exception("nf")):
            result = await svc.process_refund(
                "re_big", "ch_1", Decimal("10.00"), "USD",
                invoice_id=inv_id, payment_intent_id=payment_intent_id,
            )
    assert result.status == RefundStatus.REJECTED


@pytest.mark.asyncio
async def test_stripe_lookup_invoice_via_payment_intent():
    svc = _make_service()
    pi_id = "pi_link_1"
    purchase_id = uuid4()
    inv_id = uuid4()
    purchase = MagicMock(id=purchase_id, content_id=uuid4())
    invoice = MagicMock(id=inv_id, amount=Decimal("9.99"), currency="USD")
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=True)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    stripe_refund = {
        "id": "re_pi_1",
        "amount": 999,
        "currency": "usd",
        "charge": "ch_1",
        "payment_intent": pi_id,
    }
    with patch("app.services.StripeClient.retrieve_refund", return_value=stripe_refund):
        with patch(
            "app.services.StripeClient.retrieve_charge",
            return_value={"id": "ch_1", "payment_intent": pi_id, "invoice": None},
        ):
            result = await svc.process_refund(
                "re_pi_1", "ch_1", Decimal("9.99"), "USD", payment_intent_id=pi_id
            )
    svc.inv_repo.get_by_purchase_id.assert_awaited()
    assert result.invoice_id == inv_id
    svc.refund_repo.apply_to_invoice.assert_awaited_once_with(inv_id, Decimal("9.99"))


@pytest.mark.asyncio
async def test_mismatched_metadata_invoice_is_pending_without_mutation():
    svc = _make_service()
    authoritative_invoice = MagicMock(
        id=uuid4(), amount=Decimal("5.00"), currency="USD"
    )
    supplied_invoice_id = uuid4()
    purchase = MagicMock(id=uuid4())
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=authoritative_invoice)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    stripe_refund = {
        "id": "re_wrong_invoice",
        "amount": 500,
        "currency": "usd",
        "charge": "ch_wrong_invoice",
        "payment_intent": "pi_authoritative",
    }
    with patch("app.services.StripeClient.retrieve_refund", return_value=stripe_refund):
        result = await svc.process_refund(
            "re_wrong_invoice",
            "ch_wrong_invoice",
            Decimal("5.00"),
            "USD",
            invoice_id=supplied_invoice_id,
        )
    assert result.status == RefundStatus.PENDING_REVIEW
    assert result.invoice_id is None
    svc.refund_repo.apply_to_invoice.assert_not_awaited()


@pytest.mark.asyncio
async def test_stripe_lookup_invoice_via_charge_invoice():
    svc = _make_service()
    stripe_inv = "in_stripe_123"
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("7.99"), currency="USD")
    svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=invoice)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=True)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    stripe_refund = {
        "id": "re_chinv_1",
        "amount": 799,
        "currency": "usd",
        "charge": "ch_inv_1",
        "payment_intent": None,
    }
    charge_obj = {"id": "ch_inv_1", "payment_intent": "pi_99", "invoice": stripe_inv}
    with patch("app.services.StripeClient.retrieve_refund", return_value=stripe_refund):
        with patch("app.services.StripeClient.retrieve_charge", return_value=charge_obj):
            result = await svc.process_refund("re_chinv_1", "ch_inv_1", Decimal("7.99"), "USD")
    svc.inv_repo.get_by_stripe_invoice_id.assert_awaited_with(stripe_inv)
    assert result.invoice_id == inv_id


@pytest.mark.asyncio
async def test_missing_metadata_unresolved_pending_review():
    svc = _make_service()
    svc.inv_repo.get = AsyncMock(return_value=None)
    svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=None)
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=True)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("nf")):
        with patch("app.services.StripeClient.retrieve_charge", side_effect=Exception("nf")):
            result = await svc.process_refund("re_unresolved", "ch_no", Decimal("5.00"), "USD")
    assert result.status == RefundStatus.PENDING_REVIEW
    assert result.invoice_id is None
    svc.refund_repo.apply_to_invoice.assert_not_awaited()


@pytest.mark.asyncio
async def test_amount_mismatch_stripe_vs_webhook_pending_review():
    svc = _make_service()
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("10.00"), currency="USD")
    svc.inv_repo.get = AsyncMock(return_value=invoice)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    stripe_refund = {
        "id": "re_mismatch",
        "amount": 500,
        "currency": "usd",
        "charge": "ch_1",
        "payment_intent": "pi_1",
    }
    with patch("app.services.StripeClient.retrieve_refund", return_value=stripe_refund):
        result = await svc.process_refund(
            "re_mismatch", "ch_1", Decimal("9.99"), "USD", invoice_id=inv_id
        )
    assert result.status == RefundStatus.PENDING_REVIEW
    svc.refund_repo.apply_to_invoice.assert_not_awaited()


@pytest.mark.asyncio
async def test_currency_mismatch_pending_review():
    svc = _make_service()
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("10.00"), currency="USD")
    svc.inv_repo.get = AsyncMock(return_value=invoice)
    payment_intent_id = _link_invoice_to_payment_intent(svc, invoice)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("nf")):
        with patch("app.services.StripeClient.retrieve_charge", side_effect=Exception("nf")):
            result = await svc.process_refund(
                "re_cur_mismatch", "ch_1", Decimal("5.00"), "EUR",
                invoice_id=inv_id, payment_intent_id=payment_intent_id,
            )
    assert result.status == RefundStatus.PENDING_REVIEW


@pytest.mark.asyncio
async def test_handler_refund_created_stripe_lookup():
    svc = MagicMock()
    svc.process_refund = AsyncMock(return_value=MagicMock())
    event = {
        "type": "refund.created",
        "data": {
            "object": _refund_obj(
                refund_id="re_h1",
                charge="ch_h1",
                pi="pi_h1",
                amount=799,
                currency="usd",
                metadata={"invoice_id": str(uuid4())},
            )
        },
    }
    await _handle_refund(event, svc)
    assert svc.process_refund.await_count == 1
    kwargs = svc.process_refund.await_args.kwargs
    assert kwargs["refund_id"] == "re_h1"
    assert kwargs["charge_id"] == "ch_h1"
    assert kwargs["payment_intent_id"] == "pi_h1"


@pytest.mark.asyncio
async def test_handler_charge_refunded_multiple():
    svc = MagicMock()
    svc.process_refund = AsyncMock(return_value=MagicMock())
    inv_id = str(uuid4())
    r1 = _refund_obj(
        refund_id="re_c1",
        charge="ch_c1",
        amount=300,
        currency="usd",
        metadata={"invoice_id": inv_id},
    )
    r2 = _refund_obj(
        refund_id="re_c2",
        charge="ch_c1",
        amount=200,
        currency="usd",
        metadata={"invoice_id": inv_id},
    )
    charge = _charge_obj(charge_id="ch_c1", pi="pi_c1", refunds={"data": [r1, r2]})
    event = {"type": "charge.refunded", "data": {"object": charge}}
    await _handle_refund(event, svc)
    assert svc.process_refund.await_count == 2
    ids = {c.kwargs["refund_id"] for c in svc.process_refund.await_args_list}
    assert ids == {"re_c1", "re_c2"}


@pytest.mark.asyncio
async def test_handler_charge_refunded_without_metadata_uses_stripe_fallback():
    svc = MagicMock()
    svc.process_refund = AsyncMock(return_value=MagicMock())
    r1 = _refund_obj(
        refund_id="re_nomem", charge="ch_nomem", pi=None, amount=100, currency="usd", metadata={}
    )
    charge = _charge_obj(
        charge_id="ch_nomem", pi="pi_nomem", invoice="in_123", refunds={"data": [r1]}
    )
    event = {"type": "charge.refunded", "data": {"object": charge}}
    await _handle_refund(event, svc)
    kwargs = svc.process_refund.await_args.kwargs
    assert kwargs["refund_id"] == "re_nomem"
    assert kwargs["stripe_invoice_id"] == "in_123"
    assert kwargs["payment_intent_id"] == "pi_nomem"


@pytest.mark.asyncio
async def test_duplicate_event_idempotent_via_refund_id():
    svc = _make_service()
    inv_id = uuid4()
    invoice = MagicMock(id=inv_id, amount=Decimal("10.00"), currency="USD")
    svc.inv_repo.get = AsyncMock(return_value=invoice)
    svc.refund_repo.apply_to_invoice = AsyncMock(return_value=True)
    svc.refund_repo.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    with patch("app.services.StripeClient.retrieve_refund", side_effect=Exception("nf")):
        with patch("app.services.StripeClient.retrieve_charge", side_effect=Exception("nf")):
            r1 = await svc.process_refund(
                "re_dup_evt", "ch_1", Decimal("2.00"), "USD", invoice_id=inv_id
            )
            svc.refund_repo.get_by_refund_id = AsyncMock(return_value=r1)
            r2 = await svc.process_refund(
                "re_dup_evt", "ch_1", Decimal("2.00"), "USD", invoice_id=inv_id
            )
    assert r2 is r1
    assert svc.refund_repo.create.await_count == 1


@pytest.mark.asyncio
async def test_process_single_refund_obj_missing_metadata_resolves_via_stripe():
    svc = MagicMock()
    svc.process_refund = AsyncMock(return_value=MagicMock())
    refund = _refund_obj(
        refund_id="re_single_nomem",
        charge="ch_abc",
        pi=None,
        amount=400,
        currency="usd",
        metadata={},
    )
    charge = _charge_obj(charge_id="ch_abc", pi="pi_abc", invoice="in_abc")
    await _process_single_refund_obj(refund, svc, charge_fallback="ch_abc", charge_obj=charge)
    kwargs = svc.process_refund.await_args.kwargs
    assert kwargs["refund_id"] == "re_single_nomem"
    assert kwargs["payment_intent_id"] == "pi_abc"
    assert kwargs["stripe_invoice_id"] == "in_abc"
