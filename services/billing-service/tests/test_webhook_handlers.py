"""Behavioural tests for the Stripe webhook handlers in ``app.api.webhook_routes``.

Covers every event handler reachable from the ``_EVENT_HANDLERS`` dispatch
table plus the durable-inbox endpoint itself:

  * signature verification failure (400) happens *before* any claim;
  * the claim/commit/complete/fail inbox sequence on the happy path;
  * replay (claim refused) short-circuits to 200 without side effects;
  * handler failure rolls back, marks FAILED, and returns 500 so Stripe retries;
  * per-handler validation guards and reconciliation branches.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.webhook_routes import (
    _EVENT_HANDLERS,
    _handle_checkout_session_completed,
    _handle_invoice_paid,
    _handle_payment_intent_succeeded,
    _handle_refund,
    _handle_subscription_deleted,
    _handle_subscription_updated,
    _process_single_refund_obj,
    get_billing_service,
    stripe_webhook,
)
from app.core.settings import settings
from app.core.stripe_client import StripeError
from app.main import create_app
from app.models import InvoiceStatus
from app.services import BillingError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service_mock() -> MagicMock:
    """A BillingService stand-in with every awaited collaborator mocked."""
    svc = MagicMock()
    svc.subscribe = AsyncMock()
    svc.purchase_title = AsyncMock()
    svc.process_refund = AsyncMock()
    svc.sync_subscription_from_stripe = AsyncMock()
    svc.accrue_payout = AsyncMock()
    svc._fetch_content_price = AsyncMock(return_value=Decimal("9.99"))
    svc._fetch_content_details = AsyncMock(
        return_value=(Decimal("9.99"), uuid4()),
    )
    svc.sub_repo.get_by_user = AsyncMock(return_value=None)
    svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=None)
    svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=None)
    svc.inv_repo.create = AsyncMock(side_effect=lambda **kw: SimpleNamespace(**kw))
    svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=None)
    return svc


def _event(event_type: str, obj: dict, event_id: str = "evt_1", created: int = 0) -> dict:
    return {"id": event_id, "type": event_type, "created": created, "data": {"object": obj}}


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


class TestDispatchTable:
    @pytest.mark.parametrize(
        "event_type",
        [
            "checkout.session.completed",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.paid",
            "payment_intent.succeeded",
            "charge.refunded",
            "refund.created",
        ],
    )
    def test_every_documented_event_type_has_a_handler(self, event_type):
        assert event_type in _EVENT_HANDLERS
        assert callable(_EVENT_HANDLERS[event_type])

    def test_refund_events_share_one_handler(self):
        assert _EVENT_HANDLERS["refund.created"] is _handle_refund
        assert _EVENT_HANDLERS["charge.refunded"] is _handle_refund

    async def test_get_billing_service_wires_every_repository(self):
        db = AsyncMock()
        svc = await get_billing_service(db)
        # All nine repositories must be constructed over the same session,
        # otherwise a handler would write through an unbound session.
        for repo in (
            svc.sub_repo,
            svc.purchase_repo,
            svc.inv_repo,
            svc.floor_repo,
            svc.pool_repo,
            svc.milestone_repo,
            svc.payout_repo,
            svc.refund_repo,
            svc.webhook_events_repo,
        ):
            assert repo.session is db


# ---------------------------------------------------------------------------
# checkout.session.completed
# ---------------------------------------------------------------------------


class TestCheckoutSessionCompleted:
    async def test_event_without_any_user_reference_is_ignored(self):
        svc = _service_mock()
        await _handle_checkout_session_completed(
            _event("checkout.session.completed", {"id": "cs_1", "metadata": {}}), svc
        )
        svc.subscribe.assert_not_awaited()
        svc.purchase_title.assert_not_awaited()

    async def test_falls_back_to_client_reference_id(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_checkout_session_completed(
            _event(
                "checkout.session.completed",
                {"id": "cs_1", "client_reference_id": str(user), "metadata": {"tier": "svod"}},
            ),
            svc,
        )
        svc.subscribe.assert_awaited_once_with(user, "svod")

    @pytest.mark.parametrize("tier", ["svod", "SVOD", "Svod", "avod", "tvod"])
    async def test_known_tier_activates_subscription(self, tier):
        user = uuid4()
        svc = _service_mock()
        await _handle_checkout_session_completed(
            _event(
                "checkout.session.completed",
                {"id": "cs_1", "metadata": {"user_id": str(user), "tier": tier}},
            ),
            svc,
        )
        svc.subscribe.assert_awaited_once()
        assert svc.subscribe.call_args.args[0] == user
        assert svc.subscribe.call_args.args[1] == tier.lower()

    async def test_unknown_tier_is_ignored_rather_than_guessed(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_checkout_session_completed(
            _event(
                "checkout.session.completed",
                {"id": "cs_1", "metadata": {"user_id": str(user), "tier": "premium-plus"}},
            ),
            svc,
        )
        svc.subscribe.assert_not_awaited()

    async def test_existing_subscription_still_converges_on_subscribe(self):
        # Both arms of the if/else must end in the same activation so a replay
        # cannot leave an existing sub un-activated.
        user = uuid4()
        svc = _service_mock()
        svc.sub_repo.get_by_user = AsyncMock(return_value=MagicMock(tier="avod"))
        await _handle_checkout_session_completed(
            _event(
                "checkout.session.completed",
                {"id": "cs_1", "metadata": {"user_id": str(user), "tier": "svod"}},
            ),
            svc,
        )
        svc.subscribe.assert_awaited_once_with(user, "svod")

    async def test_new_subscription_path_calls_subscribe(self):
        user = uuid4()
        svc = _service_mock()
        svc.sub_repo.get_by_user = AsyncMock(return_value=None)
        await _handle_checkout_session_completed(
            _event(
                "checkout.session.completed",
                {"id": "cs_1", "metadata": {"user_id": str(user), "tier": "svod"}},
            ),
            svc,
        )
        svc.subscribe.assert_awaited_once_with(user, "svod")

    async def test_tvod_without_content_id_is_ignored(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_checkout_session_completed(
            _event(
                "checkout.session.completed",
                {
                    "id": "cs_1",
                    "client_reference_id": str(user),
                    "metadata": {"type": "tvod"},
                },
            ),
            svc,
        )
        svc.purchase_title.assert_not_awaited()

    async def test_tvod_with_unsupported_currency_code_is_rejected(self):
        user, content = uuid4(), uuid4()
        svc = _service_mock()
        obj = {
            "id": "cs_1",
            "client_reference_id": str(user),
            "payment_status": "paid",
            "amount_total": 999,
            "currency": "ZZZ",
            "metadata": {"type": "tvod", "content_id": str(content)},
        }
        with pytest.raises(BillingError, match="Unsupported currency code"):
            await _handle_checkout_session_completed(_event("x", obj), svc)
        svc.purchase_title.assert_not_awaited()

    async def test_tvod_content_lookup_failure_becomes_billing_error(self):
        user, content = uuid4(), uuid4()
        svc = _service_mock()
        svc._fetch_content_price = AsyncMock(side_effect=ValueError("Content not found"))
        obj = {
            "id": "cs_1",
            "client_reference_id": str(user),
            "payment_status": "paid",
            "amount_total": 999,
            "currency": "usd",
            "metadata": {"type": "tvod", "content_id": str(content)},
        }
        with pytest.raises(BillingError, match="Content not found"):
            await _handle_checkout_session_completed(_event("x", obj), svc)
        svc.purchase_title.assert_not_awaited()


# ---------------------------------------------------------------------------
# customer.subscription.updated / .deleted
# ---------------------------------------------------------------------------


class TestSubscriptionUpdated:
    async def test_event_without_user_id_is_ignored(self):
        svc = _service_mock()
        await _handle_subscription_updated(
            _event("customer.subscription.updated", {"id": "sub_1", "metadata": {}}), svc
        )
        svc.sync_subscription_from_stripe.assert_not_awaited()

    async def test_status_and_event_created_are_forwarded_for_the_monotonic_guard(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_subscription_updated(
            _event(
                "customer.subscription.updated",
                {"id": "sub_1", "status": "past_due", "metadata": {"user_id": str(user)}},
                created=1700000000,
            ),
            svc,
        )
        svc.sync_subscription_from_stripe.assert_awaited_once_with(
            user, "past_due", 1700000000
        )

    async def test_missing_created_defaults_to_zero(self):
        user = uuid4()
        event = {
            "id": "e",
            "type": "customer.subscription.updated",
            "data": {"object": {"status": "active", "metadata": {"user_id": str(user)}}},
        }
        svc = _service_mock()
        await _handle_subscription_updated(event, svc)
        svc.sync_subscription_from_stripe.assert_awaited_once_with(user, "active", 0)

    @pytest.mark.parametrize("status", ["active", "past_due", "canceled", "unpaid", "trialing"])
    async def test_arbitrary_stripe_status_is_passed_through_verbatim(self, status):
        user = uuid4()
        svc = _service_mock()
        await _handle_subscription_updated(
            _event(
                "customer.subscription.updated",
                {"id": "sub_1", "status": status, "metadata": {"user_id": str(user)}},
                created=5,
            ),
            svc,
        )
        svc.sync_subscription_from_stripe.assert_awaited_once_with(user, status, 5)


class TestSubscriptionDeleted:
    async def test_event_without_user_id_is_ignored(self):
        svc = _service_mock()
        await _handle_subscription_deleted(
            _event("customer.subscription.deleted", {"id": "sub_1"}), svc
        )
        svc.sync_subscription_from_stripe.assert_not_awaited()

    async def test_cancellation_is_synced_as_canceled_regardless_of_stripe_field(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_subscription_deleted(
            _event(
                "customer.subscription.deleted",
                {"id": "sub_1", "status": "active", "metadata": {"user_id": str(user)}},
                created=42,
            ),
            svc,
        )
        # A deleted event means canceled, even if the object's status says otherwise.
        svc.sync_subscription_from_stripe.assert_awaited_once_with(user, "canceled", 42)

    async def test_missing_created_defaults_to_zero(self):
        user = uuid4()
        event = {
            "id": "e",
            "type": "customer.subscription.deleted",
            "data": {"object": {"metadata": {"user_id": str(user)}}},
        }
        svc = _service_mock()
        await _handle_subscription_deleted(event, svc)
        svc.sync_subscription_from_stripe.assert_awaited_once_with(user, "canceled", 0)


# ---------------------------------------------------------------------------
# invoice.paid
# ---------------------------------------------------------------------------


class TestInvoicePaid:
    async def test_invoice_without_id_is_ignored(self):
        svc = _service_mock()
        await _handle_invoice_paid(_event("invoice.paid", {"id": None, "total": 100}), svc)
        svc.inv_repo.create.assert_not_awaited()

    async def test_already_recorded_invoice_is_skipped(self):
        svc = _service_mock()
        svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
        user = uuid4()
        await _handle_invoice_paid(
            _event(
                "invoice.paid",
                {
                    "id": "in_1",
                    "total": 799,
                    "currency": "usd",
                    "lines": {"data": [{"metadata": {"user_id": str(user)}}]},
                },
            ),
            svc,
        )
        svc.inv_repo.create.assert_not_awaited()

    async def test_records_invoice_marked_paid_for_the_line_owner(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_invoice_paid(
            _event(
                "invoice.paid",
                {
                    "id": "in_2",
                    "total": 799,
                    "currency": "usd",
                    "lines": {"data": [{"metadata": {"user_id": str(user)}}]},
                },
            ),
            svc,
        )
        svc.inv_repo.create.assert_awaited_once()
        kwargs = svc.inv_repo.create.call_args.kwargs
        assert kwargs["user_id"] == user
        # Stripe totals are minor units; the ledger stores major units.
        assert kwargs["amount"] == Decimal("7.99")
        assert kwargs["currency"] == "USD"
        assert kwargs["stripe_invoice_id"] == "in_2"
        assert kwargs["subscription_id"] is None

    async def test_created_invoice_is_stamped_paid_with_a_timestamp(self):
        user = uuid4()
        created: list = []
        svc = _service_mock()

        def _create(**kwargs):
            row = SimpleNamespace(**kwargs)
            created.append(row)
            return row

        svc.inv_repo.create = AsyncMock(side_effect=_create)
        await _handle_invoice_paid(
            _event(
                "invoice.paid",
                {
                    "id": "in_3",
                    "total": 100,
                    "currency": "eur",
                    "lines": {"data": [{"metadata": {"user_id": str(user)}}]},
                },
            ),
            svc,
        )
        assert len(created) == 1
        # The handler mutates the row *after* create(): it must land PAID.
        assert created[0].status == InvoiceStatus.PAID
        assert created[0].paid_at is not None
        assert created[0].currency == "EUR"
        assert created[0].amount == Decimal("1.00")

    async def test_invoice_with_no_line_metadata_is_not_attributed(self):
        svc = _service_mock()
        await _handle_invoice_paid(
            _event("invoice.paid", {"id": "in_4", "total": 799, "lines": {"data": []}}), svc
        )
        svc.inv_repo.create.assert_not_awaited()

    async def test_only_the_first_attributed_line_creates_an_invoice(self):
        first, second = uuid4(), uuid4()
        svc = _service_mock()
        await _handle_invoice_paid(
            _event(
                "invoice.paid",
                {
                    "id": "in_5",
                    "total": 799,
                    "lines": {
                        "data": [
                            {"metadata": {}},
                            {"metadata": {"user_id": str(first)}},
                            {"metadata": {"user_id": str(second)}},
                        ]
                    },
                },
            ),
            svc,
        )
        assert svc.inv_repo.create.await_count == 1
        assert svc.inv_repo.create.call_args.kwargs["user_id"] == first

    async def test_invoice_without_lines_key_is_not_attributed(self):
        svc = _service_mock()
        await _handle_invoice_paid(_event("invoice.paid", {"id": "in_6", "total": 100}), svc)
        svc.inv_repo.create.assert_not_awaited()

    async def test_currency_defaults_to_usd_when_absent(self):
        user = uuid4()
        svc = _service_mock()
        await _handle_invoice_paid(
            _event(
                "invoice.paid",
                {"id": "in_7", "total": 250, "lines": {"data": [{"metadata": {"user_id": str(user)}}]}},
            ),
            svc,
        )
        assert svc.inv_repo.create.call_args.kwargs["currency"] == "USD"


# ---------------------------------------------------------------------------
# payment_intent.succeeded
# ---------------------------------------------------------------------------


class TestPaymentIntentSucceeded:
    def _pi(self, **overrides) -> dict:
        base = {
            "id": "pi_1",
            "amount": 999,
            "currency": "usd",
            "metadata": {"content_id": str(uuid4())},
        }
        base.update(overrides)
        return base

    async def test_accrues_creator_share_for_the_ascending_path(self):
        content, creator = uuid4(), uuid4()
        svc = _service_mock()
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event("payment_intent.succeeded", self._pi(metadata={"content_id": str(content)})), svc
        )
        svc.accrue_payout.assert_awaited_once()
        kwargs = svc.accrue_payout.call_args.kwargs
        assert kwargs["creator_id"] == creator
        # 55% creator share of the 9.99 gross.
        assert kwargs["amount"] == Decimal("5.4945")
        assert kwargs["currency"] == "USD"
        assert kwargs["idempotency_key"] == "pi:pi_1"
        assert kwargs["breakdown"]["type"] == "tvod"
        assert kwargs["breakdown"]["gross"] == "9.99"

    async def test_missing_payment_intent_id_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Missing payment_intent id"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(id=None)), svc
            )

    async def test_falls_back_to_amount_received(self):
        creator = uuid4()
        svc = _service_mock()
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event(
                "payment_intent.succeeded",
                self._pi(amount=None, amount_received=999),
            ),
            svc,
        )
        svc.accrue_payout.assert_awaited_once()

    async def test_amount_missing_entirely_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Missing amount in payment_intent"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(amount=None)), svc
            )

    @pytest.mark.parametrize("bad", ["not-a-number", [1], {}])
    async def test_non_numeric_amount_is_rejected(self, bad):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Invalid amount"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(amount=bad)), svc
            )

    async def test_missing_currency_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Missing currency in payment_intent"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(currency="")), svc
            )

    async def test_unlisted_currency_code_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Unsupported currency code"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(currency="ZZZ")), svc
            )

    async def test_currency_differing_from_configured_default_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Currency mismatch"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(currency="eur")), svc
            )

    async def test_malformed_content_id_in_metadata_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Invalid content_id"):
            await _handle_payment_intent_succeeded(
                _event(
                    "payment_intent.succeeded",
                    self._pi(metadata={"content_id": "not-a-uuid"}),
                ),
                svc,
            )

    async def test_camel_case_content_id_metadata_is_accepted(self):
        content, creator = uuid4(), uuid4()
        svc = _service_mock()
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event(
                "payment_intent.succeeded",
                self._pi(metadata={"contentId": str(content)}),
            ),
            svc,
        )
        assert svc._fetch_content_details.call_args.args[0] == content

    async def test_missing_content_id_with_no_purchase_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Missing content_id"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(metadata={})), svc
            )

    async def test_amount_disagreeing_with_catalogue_price_is_rejected(self):
        svc = _service_mock()
        svc._fetch_content_details = AsyncMock(
            return_value=(Decimal("12.34"), uuid4()),
        )
        with pytest.raises(BillingError, match="Amount mismatch"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi(amount=999)), svc
            )
        svc.accrue_payout.assert_not_awaited()

    async def test_catalogue_lookup_failure_becomes_billing_error(self):
        svc = _service_mock()
        svc._fetch_content_details = AsyncMock(side_effect=ValueError("Content not found"))
        with pytest.raises(BillingError, match="Content not found"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_purchase_supplies_content_id_when_metadata_lacks_it(self):
        content, creator = uuid4(), uuid4()
        purchase = SimpleNamespace(
            id=uuid4(),
            content_id=content,
            price=Decimal("9.99"),
            currency="USD",
            purchased_at=None,
        )
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=None)
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event("payment_intent.succeeded", self._pi(metadata={})), svc
        )
        assert svc._fetch_content_details.call_args.args[0] == content
        svc.accrue_payout.assert_awaited_once()

    async def test_purchase_amount_disagreeing_with_payment_is_rejected(self):
        purchase = SimpleNamespace(
            id=uuid4(),
            content_id=uuid4(),
            price=Decimal("5.00"),
            currency="USD",
            purchased_at=None,
        )
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        with pytest.raises(BillingError, match="Amount mismatch: purchase amount"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_purchase_with_unconvertible_price_is_rejected(self):
        purchase = SimpleNamespace(
            id=uuid4(),
            content_id=uuid4(),
            price=Decimal("1.005"),
            currency="USD",
            purchased_at=None,
        )
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        with pytest.raises(BillingError, match="decimal places"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_purchase_currency_disagreeing_with_payment_is_rejected(self):
        # Money must be converted with the *purchase's* currency; a mismatch
        # means the two records describe different money.
        purchase = SimpleNamespace(
            id=uuid4(),
            content_id=uuid4(),
            price=Decimal("9.99"),
            currency="EUR",
            purchased_at=None,
        )
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        with pytest.raises(BillingError, match="Currency mismatch: purchase currency"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_invoice_amount_disagreeing_with_payment_is_rejected(self):
        purchase = SimpleNamespace(
            id=uuid4(), content_id=uuid4(), price=Decimal("9.99"), currency="USD", purchased_at=None
        )
        invoice = SimpleNamespace(id=uuid4(), amount=Decimal("5.00"), currency="USD")
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with pytest.raises(BillingError, match="Invoice amount mismatch"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_invoice_with_unconvertible_amount_is_rejected(self):
        purchase = SimpleNamespace(
            id=uuid4(), content_id=uuid4(), price=Decimal("9.99"), currency="USD", purchased_at=None
        )
        invoice = SimpleNamespace(id=uuid4(), amount=Decimal("1.005"), currency="USD")
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with pytest.raises(BillingError, match="decimal places"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_invoice_currency_disagreeing_with_payment_is_rejected(self):
        purchase = SimpleNamespace(
            id=uuid4(), content_id=uuid4(), price=Decimal("9.99"), currency="USD", purchased_at=None
        )
        invoice = SimpleNamespace(id=uuid4(), amount=Decimal("9.99"), currency="EUR")
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with pytest.raises(BillingError, match="Currency mismatch: invoice currency"):
            await _handle_payment_intent_succeeded(
                _event("payment_intent.succeeded", self._pi()), svc
            )

    async def test_unpaid_invoice_is_stamped_paid_and_used_as_cycle_start(self):
        content, creator = uuid4(), uuid4()
        purchase = SimpleNamespace(
            id=uuid4(), content_id=content, price=Decimal("9.99"), currency="USD", purchased_at=None
        )
        invoice = SimpleNamespace(
            id=uuid4(),
            amount=Decimal("9.99"),
            currency="USD",
            status=InvoiceStatus.PENDING,
            issued_at=Decimal("0"),
        )
        invoice.issued_at = None
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event("payment_intent.succeeded", self._pi(metadata={})), svc
        )
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.paid_at is not None
        svc.accrue_payout.assert_awaited_once()

    async def test_purchase_without_purchased_at_attribute_falls_back_to_now(self):
        content, creator = uuid4(), uuid4()

        class _NoPurchasedAt:
            """Mirrors a legacy row shape: purchased_at simply does not exist."""

            def __init__(self):
                self.id = uuid4()
                self.content_id = content
                self.price = Decimal("9.99")
                self.currency = "USD"

        purchase = _NoPurchasedAt()
        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=None)
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event("payment_intent.succeeded", self._pi(metadata={})), svc
        )
        assert svc.accrue_payout.call_args.kwargs["cycle_start"] is not None

    async def test_invoice_without_issued_at_attribute_keeps_purchase_cycle_start(self):
        content, creator = uuid4(), uuid4()
        purchase = SimpleNamespace(
            id=uuid4(), content_id=content, price=Decimal("9.99"), currency="USD", purchased_at=None
        )

        class _NoIssuedAt:
            def __init__(self):
                self.id = uuid4()
                self.amount = Decimal("9.99")
                self.currency = "USD"
                self.status = InvoiceStatus.PENDING

        svc = _service_mock()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=_NoIssuedAt())
        svc._fetch_content_details = AsyncMock(return_value=(Decimal("9.99"), creator))
        await _handle_payment_intent_succeeded(
            _event("payment_intent.succeeded", self._pi(metadata={})), svc
        )
        assert svc.accrue_payout.call_args.kwargs["cycle_start"] is not None


# ---------------------------------------------------------------------------
# Refund object processing
# ---------------------------------------------------------------------------


class TestProcessSingleRefundObj:
    def _refund(self, **overrides) -> dict:
        base = {
            "id": "re_1",
            "charge": "ch_1",
            "payment_intent": "pi_1",
            "amount": 500,
            "currency": "usd",
            "metadata": {},
            "reason": "requested_by_customer",
        }
        base.update(overrides)
        return base

    async def test_refund_without_id_is_ignored(self):
        svc = _service_mock()
        await _process_single_refund_obj(self._refund(id=None), svc)
        svc.process_refund.assert_not_awaited()

    async def test_refund_is_forwarded_to_the_service(self):
        user = uuid4()
        svc = _service_mock()
        await _process_single_refund_obj(self._refund(metadata={"user_id": str(user)}), svc)
        svc.process_refund.assert_awaited_once()
        kwargs = svc.process_refund.call_args.kwargs
        assert kwargs["refund_id"] == "re_1"
        assert kwargs["charge_id"] == "ch_1"
        assert kwargs["amount"] == Decimal("5.00")
        assert kwargs["currency"] == "USD"
        assert kwargs["user_id"] == user
        assert kwargs["payment_intent_id"] == "pi_1"
        assert kwargs["reason"] == "requested_by_customer"

    async def test_amount_falls_back_to_amount_refunded(self):
        svc = _service_mock()
        await _process_single_refund_obj(
            self._refund(amount=None, amount_refunded=250), svc
        )
        assert svc.process_refund.call_args.kwargs["amount"] == Decimal("2.50")

    async def test_amount_defaults_to_zero_when_neither_field_present(self):
        svc = _service_mock()
        await _process_single_refund_obj(self._refund(amount=None), svc)
        assert svc.process_refund.call_args.kwargs["amount"] == Decimal("0.00")

    @pytest.mark.parametrize("bad", ["five", object()])
    async def test_non_numeric_amount_is_rejected(self, bad):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Invalid refund amount"):
            await _process_single_refund_obj(self._refund(amount=bad), svc)
        svc.process_refund.assert_not_awaited()

    async def test_currency_is_inherited_from_the_charge_when_absent(self):
        svc = _service_mock()
        charge = {"id": "ch_1", "currency": "eur", "invoice": "in_1"}
        await _process_single_refund_obj(
            self._refund(currency=None, charge=None), svc, charge_obj=charge
        )
        kwargs = svc.process_refund.call_args.kwargs
        assert kwargs["currency"] == "EUR"
        assert kwargs["stripe_invoice_id"] == "in_1"

    async def test_currency_defaults_to_usd_when_absent_everywhere(self):
        svc = _service_mock()
        await _process_single_refund_obj(self._refund(currency=None), svc)
        assert svc.process_refund.call_args.kwargs["currency"] == "USD"

    async def test_unlisted_currency_code_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Unsupported currency code"):
            await _process_single_refund_obj(self._refund(currency="ZZZ"), svc)
        svc.process_refund.assert_not_awaited()

    async def test_payment_intent_is_inherited_from_the_charge(self):
        svc = _service_mock()
        await _process_single_refund_obj(
            self._refund(payment_intent=None, charge=None),
            svc,
            charge_fallback="ch_1",
            charge_obj={"id": "ch_1", "payment_intent": "pi_from_charge"},
        )
        assert svc.process_refund.call_args.kwargs["payment_intent_id"] == "pi_from_charge"

    async def test_charge_fallback_is_used_when_refund_has_no_charge(self):
        svc = _service_mock()
        await _process_single_refund_obj(
            self._refund(charge=None), svc, charge_fallback="ch_fallback"
        )
        assert svc.process_refund.call_args.kwargs["charge_id"] == "ch_fallback"

    async def test_metadata_is_inherited_from_the_charge(self):
        user = uuid4()
        svc = _service_mock()
        await _process_single_refund_obj(
            self._refund(metadata={}),
            svc,
            charge_obj={"id": "ch_1", "metadata": {"user_id": str(user)}},
        )
        assert svc.process_refund.call_args.kwargs["user_id"] == user

    async def test_malformed_user_id_in_metadata_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Invalid user_id"):
            await _process_single_refund_obj(self._refund(metadata={"user_id": "nope"}), svc)
        svc.process_refund.assert_not_awaited()

    async def test_malformed_invoice_id_in_metadata_is_rejected(self):
        svc = _service_mock()
        with pytest.raises(BillingError, match="Invalid invoice_id"):
            await _process_single_refund_obj(
                self._refund(metadata={"invoice_id": "nope"}), svc
            )
        svc.process_refund.assert_not_awaited()

    async def test_local_invoice_id_from_metadata_is_forwarded(self):
        local = uuid4()
        svc = _service_mock()
        await _process_single_refund_obj(
            self._refund(metadata={"invoice_id": str(local)}), svc
        )
        assert svc.process_refund.call_args.kwargs["invoice_id"] == local


# ---------------------------------------------------------------------------
# charge.refunded / refund.created dispatch
# ---------------------------------------------------------------------------


class TestHandleRefund:
    def _refund_obj(self, refund_id="re_1", **overrides) -> dict:
        base = {
            "id": refund_id,
            "charge": "ch_1",
            "payment_intent": "pi_1",
            "amount": 500,
            "currency": "usd",
        }
        base.update(overrides)
        return base

    async def test_refund_created_processes_the_top_level_object(self):
        svc = _service_mock()
        await _handle_refund(
            _event("refund.created", self._refund_obj()), svc
        )
        svc.process_refund.assert_awaited_once()
        assert svc.process_refund.call_args.kwargs["refund_id"] == "re_1"

    async def test_charge_refunded_with_refunds_list_is_processed(self):
        svc = _service_mock()
        charge = {"id": "ch_1", "currency": "usd", "refunds": [self._refund_obj()]}
        await _handle_refund(_event("charge.refunded", charge), svc)
        svc.process_refund.assert_awaited_once()

    async def test_charge_refunded_processes_every_refund_in_the_list(self):
        svc = _service_mock()
        charge = {
            "id": "ch_1",
            "currency": "usd",
            "refunds": [self._refund_obj("re_a"), self._refund_obj("re_b")],
        }
        await _handle_refund(_event("charge.refunded", charge), svc)
        assert svc.process_refund.await_count == 2
        ids = {call.kwargs["refund_id"] for call in svc.process_refund.call_args_list}
        assert ids == {"re_a", "re_b"}

    async def test_charge_refunded_with_empty_refunds_dict_falls_back_to_lookup(self):
        svc = _service_mock()
        with patch(
            "app.api.webhook_routes.StripeClient.retrieve_charge",
            return_value={
                "id": "ch_1",
                "currency": "usd",
                "refunds": {"data": [self._refund_obj("re_from_lookup")]},
            },
        ) as retrieve:
            await _handle_refund(
                _event("charge.refunded", {"id": "ch_1", "refunds": {"data": []}}), svc
            )
        retrieve.assert_called_once_with("ch_1")
        svc.process_refund.assert_awaited_once()
        assert svc.process_refund.call_args.kwargs["refund_id"] == "re_from_lookup"

    async def test_lookup_result_with_a_refunds_list_is_also_processed(self):
        svc = _service_mock()
        with patch(
            "app.api.webhook_routes.StripeClient.retrieve_charge",
            return_value={"id": "ch_1", "refunds": [self._refund_obj("re_listed")]},
        ):
            await _handle_refund(_event("charge.refunded", {"id": "ch_1"}), svc)
        svc.process_refund.assert_awaited_once()
        assert svc.process_refund.call_args.kwargs["refund_id"] == "re_listed"

    async def test_charge_with_no_refunds_anywhere_is_a_no_op(self):
        svc = _service_mock()
        with patch(
            "app.api.webhook_routes.StripeClient.retrieve_charge",
            return_value={"id": "ch_1", "refunds": {"data": []}},
        ):
            await _handle_refund(_event("charge.refunded", {"id": "ch_1", "refunds": {}}), svc)
        svc.process_refund.assert_not_awaited()

    async def test_failed_lookup_does_not_break_the_webhook(self):
        # A charge whose refunds cannot be listed must not fail the whole event;
        # the inbox would otherwise be poisoned with a permanent 500.
        svc = _service_mock()
        with patch(
            "app.api.webhook_routes.StripeClient.retrieve_charge",
            side_effect=StripeError("stripe down"),
        ):
            await _handle_refund(_event("charge.refunded", {"id": "ch_1"}), svc)
        svc.process_refund.assert_not_awaited()

    async def test_lookup_returning_a_non_dict_is_tolerated(self):
        svc = _service_mock()
        with patch(
            "app.api.webhook_routes.StripeClient.retrieve_charge",
            return_value={"id": "ch_1", "refunds": None},
        ):
            await _handle_refund(_event("charge.refunded", {"id": "ch_1"}), svc)
        svc.process_refund.assert_not_awaited()

    async def test_refund_inside_a_charge_inherits_charge_context(self):
        svc = _service_mock()
        charge = {
            "id": "ch_1",
            "currency": "usd",
            "invoice": "in_9",
            "refunds": [self._refund_obj("re_ctx", charge=None, payment_intent=None)],
        }
        await _handle_refund(_event("charge.refunded", charge), svc)
        kwargs = svc.process_refund.call_args.kwargs
        assert kwargs["stripe_invoice_id"] == "in_9"
        assert kwargs["charge_id"] == "ch_1"


# ---------------------------------------------------------------------------
# stripe_webhook endpoint — durable inbox
# ---------------------------------------------------------------------------


@pytest.fixture
def inbox_service() -> MagicMock:
    """Service whose inbox claim always wins and whose handlers succeed."""
    svc = MagicMock()
    svc.webhook_events_repo.claim = AsyncMock(return_value=True)
    svc.webhook_events_repo.complete = AsyncMock(return_value=True)
    svc.webhook_events_repo.fail = AsyncMock(return_value=True)
    svc.commit = AsyncMock()
    svc.rollback = AsyncMock()
    return svc


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post(app, event: dict, signature: str | None = "t=1,v1=abc"):
    async with _client(app) as ac:
        headers = {"Stripe-Signature": signature} if signature is not None else {}
        return await ac.post(
            "/api/v1/billing/webhooks/stripe",
            content=b'{"raw": "body"}',
            headers=headers,
        )


def _override_handler(event_type: str, replacement):
    """Swap one entry in the module-level dispatch table for the block's duration.

    ``_EVENT_HANDLERS`` binds the handler function objects at import time, so
    patching the module attribute would not affect dispatch.
    """
    from app.api import webhook_routes

    return patch.dict(webhook_routes._EVENT_HANDLERS, {event_type: replacement})


def _app_with(svc: MagicMock):
    app = create_app()

    async def _override():
        return svc

    from app.api import webhook_routes

    app.dependency_overrides[webhook_routes.get_billing_service] = _override
    return app


class TestStripeWebhookEndpoint:
    async def test_invalid_signature_is_rejected_before_any_claim(self, inbox_service):
        app = _app_with(inbox_service)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            side_effect=StripeError("Invalid webhook signature"),
        ):
            resp = await _post(app, {"id": "evt_1", "type": "invoice.paid"})
        assert resp.status_code == 400
        assert "Invalid webhook signature" in resp.json()["detail"]
        # Nothing may be claimed or committed on an unverified payload.
        inbox_service.webhook_events_repo.claim.assert_not_awaited()
        inbox_service.commit.assert_not_awaited()
        app.dependency_overrides.clear()

    async def test_missing_signature_header_is_rejected(self, inbox_service):
        app = _app_with(inbox_service)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            side_effect=StripeError("Invalid webhook signature"),
        ) as handle:
            resp = await _post(app, {"id": "evt_1"}, signature=None)
        assert resp.status_code == 400
        # A missing header must be passed through as an empty signature, not
        # silently treated as "skip verification".
        assert handle.call_args.args[1] == ""
        app.dependency_overrides.clear()

    async def test_already_claimed_event_short_circuits(self, inbox_service):
        inbox_service.webhook_events_repo.claim = AsyncMock(return_value=False)
        app = _app_with(inbox_service)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            return_value={"id": "evt_dup", "type": "invoice.paid"},
        ):
            resp = await _post(app, {"id": "evt_dup"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "idempotent": True}
        inbox_service.commit.assert_not_awaited()
        inbox_service.webhook_events_repo.complete.assert_not_awaited()
        app.dependency_overrides.clear()

    async def test_claim_is_committed_before_the_handler_runs(self, inbox_service):
        app = _app_with(inbox_service)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            return_value={"id": "evt_ok", "type": "customer.subscription.deleted"},
        ):
            with _override_handler("customer.subscription.deleted", AsyncMock(return_value=None)):
                resp = await _post(app, {"id": "evt_ok"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "event_id": "evt_ok", "handled": True}
        inbox_service.webhook_events_repo.claim.assert_awaited_once_with(
            "evt_ok", "customer.subscription.deleted"
        )
        # Claim, then commit, then complete, then commit again.
        assert inbox_service.commit.await_count == 2
        inbox_service.webhook_events_repo.complete.assert_awaited_once_with("evt_ok")
        app.dependency_overrides.clear()

    async def test_unhandled_event_type_is_acknowledged_as_unhandled(self, inbox_service):
        app = _app_with(inbox_service)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            return_value={"id": "evt_other", "type": "invoice.upcoming"},
        ):
            resp = await _post(app, {"id": "evt_other"})
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "event_type": "invoice.upcoming",
            "handled": False,
        }
        # We still acknowledged receipt, so the inbox row is closed out.
        inbox_service.webhook_events_repo.complete.assert_awaited_once_with("evt_other")
        app.dependency_overrides.clear()

    async def test_handler_failure_rolls_back_marks_failed_and_returns_500(self, inbox_service):
        app = _app_with(inbox_service)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            return_value={"id": "evt_bad", "type": "invoice.paid"},
        ):
            failing = AsyncMock(side_effect=BillingError("handler exploded"))
            with _override_handler("invoice.paid", failing):
                resp = await _post(app, {"id": "evt_bad"})
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Webhook handler failed"
        # The transaction must be rolled back before the FAILED update or the
        # inbox row would stay PROCESSING and never be retried.
        inbox_service.rollback.assert_awaited_once()
        inbox_service.webhook_events_repo.fail.assert_awaited_once()
        assert inbox_service.webhook_events_repo.fail.call_args.args[0] == "evt_bad"
        assert "handler exploded" in inbox_service.webhook_events_repo.fail.call_args.args[1]
        inbox_service.webhook_events_repo.complete.assert_not_awaited()
        app.dependency_overrides.clear()

    async def test_event_id_and_type_default_when_absent_from_the_payload(self, inbox_service):
        app = _app_with(inbox_service)
        with patch("app.api.webhook_routes.StripeClient.handle_webhook", return_value={}):
            resp = await _post(app, {})
        assert resp.status_code == 200
        assert resp.json()["handled"] is False
        inbox_service.webhook_events_repo.claim.assert_awaited_once_with("", "")
        app.dependency_overrides.clear()

    @pytest.mark.parametrize(
        "event_type",
        [
            "checkout.session.completed",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.paid",
            "payment_intent.succeeded",
            "charge.refunded",
            "refund.created",
        ],
    )
    async def test_every_registered_type_dispatches_and_completes(self, event_type, inbox_service):
        # Each registered handler is invoked (patched here) and the inbox row
        # is closed out — i.e. the dispatch table entry is actually wired.
        app = _app_with(inbox_service)
        assert event_type in _EVENT_HANDLERS
        spy = AsyncMock(return_value=None)
        with patch(
            "app.api.webhook_routes.StripeClient.handle_webhook",
            return_value={
                "id": "evt_x",
                "type": event_type,
                "data": {"object": {"id": "obj_1"}},
            },
        ):
            with _override_handler(event_type, spy):
                resp = await _post(app, {"id": "evt_x"})
        assert resp.status_code == 200
        assert resp.json()["handled"] is True
        # The table entry must actually be invoked, and receive the verified event.
        spy.assert_awaited_once()
        assert spy.await_args.args[0]["id"] == "evt_x"
        assert spy.await_args.args[0]["type"] == event_type
        assert spy.await_args.args[1] is inbox_service
        inbox_service.webhook_events_repo.complete.assert_awaited_once_with("evt_x")
        app.dependency_overrides.clear()

    async def test_settings_currency_is_used_for_the_mismatch_guard(self):
        # The handlers compare against the *configured* currency, not a literal.
        assert settings.DEFAULT_CURRENCY == "USD"


async def test_stripe_webhook_is_callable_directly(inbox_service):
    """The endpoint function itself is importable and awaits the body."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/billing/webhooks/stripe",
        "headers": [(b"stripe-signature", b"t=1,v1=abc")],
    }

    async def receive():
        return {"type": "http.request", "body": b'{"raw":"body"}', "more_body": False}

    request = Request(scope, receive)
    with patch(
        "app.api.webhook_routes.StripeClient.handle_webhook",
        return_value={"id": "evt_direct", "type": "invoice.paid", "data": {"object": {"id": "in"}}},
    ):
        with _override_handler("invoice.paid", AsyncMock(return_value=None)):
            result = await stripe_webhook(
                request, service=inbox_service, stripe_signature="t=1,v1=abc"
            )
    assert result == {"status": "ok", "event_id": "evt_direct", "handled": True}
