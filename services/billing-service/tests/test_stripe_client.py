"""Behavioural tests for ``app.core.stripe_client.StripeClient``.

The Stripe SDK is mocked at its own resource boundaries
(``stripe.checkout.Session.create``, ``stripe.Account.create``, ...) so these
tests exercise *our* logic: argument construction, minor-unit conversion,
currency normalisation, and the translation of every documented SDK failure
into a domain-friendly :class:`StripeError`.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import stripe
from stripe import StripeError as _StripeError

from app.core.settings import settings
from app.core.stripe_client import StripeClient, StripeError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_error(message: str = "boom") -> _StripeError:
    """A real SDK StripeError so the ``except`` clauses match the SDK type."""
    return _StripeError(message)


def _signature_error(message: str = "no signatures found", sig_header: str = "t=1,v1=forged"):
    """A real SDK SignatureVerificationError (two-arg constructor)."""
    return stripe.SignatureVerificationError(message, sig_header)


class _StripeObj:
    """Stand-in for a non-dict SDK object (Event / Refund / Charge / PI).

    The SDK resource objects support both attribute and mapping access and are
    converted with ``dict(obj)`` by the client, so this reproduces the two
    interfaces the production code actually uses.
    """

    def __init__(self, payload: dict):
        self._payload = payload
        self.id = payload.get("id")

    @property
    def type(self):
        return self._payload.get("type")

    def keys(self):
        return self._payload.keys()

    def __getitem__(self, key):
        return self._payload[key]

    def __iter__(self):
        return iter(self._payload)

    def __len__(self):
        return len(self._payload)


# ---------------------------------------------------------------------------
# Module import-time policy (#67)
# ---------------------------------------------------------------------------


class TestSdkPolicy:
    def test_network_retries_are_bounded(self):
        # #67: a slow Stripe response must not hang the webhook handler.
        assert stripe.max_network_retries == 2

    def test_api_key_is_loaded_from_settings(self):
        assert stripe.api_key == settings.STRIPE_API_KEY

    def test_domain_error_is_not_the_sdk_error(self):
        # Callers catch StripeError; it must not be the SDK's exception or
        # every caller would also swallow SDK errors raised elsewhere.
        assert not issubclass(StripeError, _StripeError)
        assert issubclass(StripeError, Exception)


# ---------------------------------------------------------------------------
# create_checkout_session — SVOD
# ---------------------------------------------------------------------------


class TestCreateCheckoutSession:
    def test_returns_the_stripe_session_on_success(self):
        session = MagicMock(id="cs_test_1")
        with patch("stripe.checkout.Session.create", return_value=session) as create:
            result = StripeClient.create_checkout_session(
                user_id=uuid4(),
                price_id="price_svod_123",
                tier="svod",
                success_url="https://x/success",
                cancel_url="https://x/cancel",
            )
        assert result is session
        create.assert_called_once()

    def test_uses_subscription_mode_and_card_only(self):
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_checkout_session(
                uuid4(), "price_1", "svod", "https://x/s", "https://x/c"
            )
        kwargs = create.call_args.kwargs
        assert kwargs["mode"] == "subscription"
        assert kwargs["payment_method_types"] == ["card"]

    def test_line_item_is_single_quantity_of_given_price(self):
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_checkout_session(
                uuid4(), "price_abc", "svod", "https://x/s", "https://x/c"
            )
        assert create.call_args.kwargs["line_items"] == [{"price": "price_abc", "quantity": 1}]

    def test_correlates_user_and_tier_for_the_webhook_handler(self):
        user = uuid4()
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_checkout_session(user, "price_1", "premium", "https://x/s", "https://x/c")
        kwargs = create.call_args.kwargs
        # client_reference_id and metadata must both carry the user so the
        # checkout.session.completed handler can correlate either way.
        assert kwargs["client_reference_id"] == str(user)
        assert kwargs["metadata"] == {"user_id": str(user), "tier": "premium"}
        assert kwargs["success_url"] == "https://x/s"
        assert kwargs["cancel_url"] == "https://x/c"

    def test_wraps_sdk_error_into_domain_error(self):
        with patch("stripe.checkout.Session.create", side_effect=_api_error("card declined")):
            with pytest.raises(StripeError) as exc:
                StripeClient.create_checkout_session(
                    uuid4(), "price_1", "svod", "https://x/s", "https://x/c"
                )
        assert "Failed to create checkout session" in str(exc.value)
        assert "card declined" in str(exc.value)
        assert isinstance(exc.value.__cause__, _StripeError)

    def test_non_stripe_exception_is_not_swallowed(self):
        # Only SDK errors are translated; a programming error must surface.
        with patch("stripe.checkout.Session.create", side_effect=TypeError("bad kwarg")):
            with pytest.raises(TypeError):
                StripeClient.create_checkout_session(
                    uuid4(), "price_1", "svod", "https://x/s", "https://x/c"
                )


# ---------------------------------------------------------------------------
# create_tvod_purchase_session — TVOD
# ---------------------------------------------------------------------------


class TestCreateTvodPurchaseSession:
    def test_returns_the_stripe_session_on_success(self):
        session = MagicMock(id="cs_tvod_1")
        with patch("stripe.checkout.Session.create", return_value=session):
            result = StripeClient.create_tvod_purchase_session(
                uuid4(), uuid4(), Decimal("4.99"), "https://x/s", "https://x/c"
            )
        assert result is session

    def test_uses_payment_mode_with_inline_price_data(self):
        content = uuid4()
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_tvod_purchase_session(
                uuid4(), content, Decimal("4.99"), "https://x/s", "https://x/c"
            )
        kwargs = create.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert kwargs["payment_method_types"] == ["card"]
        line = kwargs["line_items"][0]
        assert line["quantity"] == 1
        assert line["price_data"]["currency"] == settings.DEFAULT_CURRENCY.lower()
        assert line["price_data"]["unit_amount"] == 499
        assert line["price_data"]["product_data"]["metadata"] == {"content_id": str(content)}

    def test_product_name_identifies_the_title(self):
        content = uuid4()
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_tvod_purchase_session(
                uuid4(), content, Decimal("1.00"), "https://x/s", "https://x/c"
            )
        name = create.call_args.kwargs["line_items"][0]["price_data"]["product_data"]["name"]
        assert name == f"Title {content}"

    def test_metadata_marks_the_event_as_tvod(self):
        user, content = uuid4(), uuid4()
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_tvod_purchase_session(
                user, content, Decimal("1.00"), "https://x/s", "https://x/c"
            )
        assert create.call_args.kwargs["metadata"] == {
            "user_id": str(user),
            "content_id": str(content),
            "type": "tvod",
        }

    def test_amount_is_converted_to_minor_units(self):
        with patch("stripe.checkout.Session.create") as create:
            StripeClient.create_tvod_purchase_session(
                uuid4(), uuid4(), Decimal("12.34"), "https://x/s", "https://x/c"
            )
        assert create.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 1234

    def test_excess_price_precision_is_rejected_before_calling_stripe(self):
        # A 3-dp USD price must never reach Stripe as a silently-rounded value.
        with patch("stripe.checkout.Session.create") as create:
            with pytest.raises(Exception) as exc:
                StripeClient.create_tvod_purchase_session(
                    uuid4(), uuid4(), Decimal("1.005"), "https://x/s", "https://x/c"
                )
        assert "decimal places" in str(exc.value)
        create.assert_not_called()

    def test_rejects_configured_currency_outside_the_iso_allowlist(self):
        # validate_currency runs on the *configured* currency, so a bad
        # DEFAULT_CURRENCY must fail the call rather than hit Stripe.
        with patch.object(settings, "DEFAULT_CURRENCY", "ZZZ"):
            with patch("stripe.checkout.Session.create") as create:
                with pytest.raises(Exception, match="Unsupported currency code"):
                    StripeClient.create_tvod_purchase_session(
                        uuid4(), uuid4(), Decimal("1.00"), "https://x/s", "https://x/c"
                    )
        create.assert_not_called()

    def test_wraps_sdk_error_into_domain_error(self):
        with patch("stripe.checkout.Session.create", side_effect=_api_error("no such price")):
            with pytest.raises(StripeError) as exc:
                StripeClient.create_tvod_purchase_session(
                    uuid4(), uuid4(), Decimal("1.00"), "https://x/s", "https://x/c"
                )
        assert "Failed to create TVOD purchase session" in str(exc.value)
        assert "no such price" in str(exc.value)
        assert isinstance(exc.value.__cause__, _StripeError)


# ---------------------------------------------------------------------------
# handle_webhook — signature verification
# ---------------------------------------------------------------------------


class TestHandleWebhook:
    def test_returns_the_parsed_event_on_success(self):
        payload = {"id": "evt_ok", "type": "invoice.paid", "data": {"object": {"id": "in_1"}}}
        event = _StripeObj(payload)
        with patch("stripe.Webhook.construct_event", return_value=event) as construct:
            result = StripeClient.handle_webhook(b'{"a":1}', "t=1,v1=abc")
        assert result["id"] == "evt_ok"
        construct.assert_called_once_with(b'{"a":1}', "t=1,v1=abc", settings.STRIPE_WEBHOOK_SECRET)

    def test_uses_the_configured_webhook_secret(self):
        with patch("stripe.Webhook.construct_event", return_value=_StripeObj({})) as construct:
            StripeClient.handle_webhook(b"{}", "sig")
        secret = construct.call_args.args[2]
        assert secret == settings.STRIPE_WEBHOOK_SECRET
        assert secret

    def test_missing_webhook_secret_fails_closed(self):
        # Fails closed: an unconfigured secret must never mean "skip checking".
        with patch.object(settings, "STRIPE_WEBHOOK_SECRET", ""):
            with patch("stripe.Webhook.construct_event") as construct:
                with pytest.raises(StripeError, match="STRIPE_WEBHOOK_SECRET is not configured"):
                    StripeClient.handle_webhook(b"{}", "sig")
        construct.assert_not_called()

    def test_invalid_signature_is_rejected(self):
        err = _signature_error("no signatures found matching the expected signature")
        with patch("stripe.Webhook.construct_event", side_effect=err):
            with pytest.raises(StripeError) as exc:
                StripeClient.handle_webhook(b"{}", "t=1,v1=forged")
        assert str(exc.value) == "Invalid webhook signature"
        assert isinstance(exc.value.__cause__, stripe.SignatureVerificationError)

    def test_invalid_signature_error_does_not_echo_sdk_detail(self):
        # The forged payload / secret material must not leak into our message.
        err = _signature_error("secret=whsec_abc payload={}")
        with patch("stripe.Webhook.construct_event", side_effect=err):
            with pytest.raises(StripeError) as exc:
                StripeClient.handle_webhook(b"{}", "forged")
        assert "whsec_abc" not in str(exc.value)

    def test_generic_stripe_error_becomes_webhook_processing_error(self):
        with patch("stripe.Webhook.construct_event", side_effect=_api_error("malformed payload")):
            with pytest.raises(StripeError) as exc:
                StripeClient.handle_webhook(b"not json", "sig")
        assert "Webhook processing error" in str(exc.value)
        assert "malformed payload" in str(exc.value)
        assert isinstance(exc.value.__cause__, _StripeError)

    def test_signature_error_is_not_double_wrapped(self):
        # A SignatureVerificationError is a StripeError subclass, but it must
        # take the specific branch, not the generic one.
        err = _signature_error("bad sig")
        with patch("stripe.Webhook.construct_event", side_effect=err):
            with pytest.raises(StripeError) as exc:
                StripeClient.handle_webhook(b"{}", "bad")
        assert str(exc.value) == "Invalid webhook signature"

    def test_uses_raw_bytes_not_parsed_json(self):
        with patch("stripe.Webhook.construct_event", return_value=_StripeObj({})) as construct:
            StripeClient.handle_webhook(b'{"id": "evt"}', "sig")
        assert construct.call_args.args[0] == b'{"id": "evt"}'


# ---------------------------------------------------------------------------
# create_connect_account
# ---------------------------------------------------------------------------


class TestCreateConnectAccount:
    def test_returns_the_account_on_success(self):
        account = MagicMock(id="acct_1")
        with patch("stripe.Account.create", return_value=account) as create:
            result = StripeClient.create_connect_account(uuid4(), "US", "creator@example.com")
        assert result is account
        create.assert_called_once()

    def test_creates_an_express_account_for_the_creator(self):
        creator = uuid4()
        with patch("stripe.Account.create") as create:
            StripeClient.create_connect_account(creator, "US", "creator@example.com")
        kwargs = create.call_args.kwargs
        assert kwargs["type"] == "express"
        assert kwargs["country"] == "US"
        assert kwargs["email"] == "creator@example.com"
        assert kwargs["metadata"] == {"creator_id": str(creator), "platform": "wildframe"}

    def test_requests_card_payments_and_transfers_capabilities(self):
        with patch("stripe.Account.create") as create:
            StripeClient.create_connect_account(uuid4(), "GB", "c@example.com")
        capabilities = create.call_args.kwargs["capabilities"]
        assert capabilities == {
            "card_payments": {"requested": True},
            "transfers": {"requested": True},
        }

    def test_wraps_sdk_error_into_domain_error(self):
        with patch("stripe.Account.create", side_effect=_api_error("country not supported")):
            with pytest.raises(StripeError) as exc:
                StripeClient.create_connect_account(uuid4(), "ZZ", "c@example.com")
        assert "Failed to create Connect account" in str(exc.value)
        assert "country not supported" in str(exc.value)
        assert isinstance(exc.value.__cause__, _StripeError)


# ---------------------------------------------------------------------------
# transfer_to_creator
# ---------------------------------------------------------------------------


class TestTransferToCreator:
    def test_returns_the_transfer_on_success(self):
        transfer = MagicMock(id="tr_1")
        with patch("stripe.Transfer.create", return_value=transfer) as create:
            result = StripeClient.transfer_to_creator("acct_1", Decimal("25.00"), "idem-1")
        assert result is transfer
        create.assert_called_once()

    def test_amount_is_converted_to_minor_units(self):
        with patch("stripe.Transfer.create") as create:
            StripeClient.transfer_to_creator("acct_1", Decimal("25.00"), "idem-1")
        assert create.call_args.kwargs["amount"] == 2500

    def test_forwards_idempotency_key_so_retries_cannot_duplicate(self):
        # #67: the idempotency key is what makes a retried transfer safe.
        with patch("stripe.Transfer.create") as create:
            StripeClient.transfer_to_creator("acct_1", Decimal("1.00"), "tranche:mid-1:2")
        assert create.call_args.kwargs["idempotency_key"] == "tranche:mid-1:2"

    def test_targets_the_creator_destination_account(self):
        with patch("stripe.Transfer.create") as create:
            StripeClient.transfer_to_creator("acct_creator_9", Decimal("1.00"), "idem-1")
        kwargs = create.call_args.kwargs
        assert kwargs["destination"] == "acct_creator_9"
        assert kwargs["currency"] == settings.DEFAULT_CURRENCY.lower()
        assert kwargs["metadata"] == {"platform": "wildframe"}

    def test_excess_precision_is_rejected_before_calling_stripe(self):
        with patch("stripe.Transfer.create") as create:
            with pytest.raises(Exception, match="decimal places"):
                StripeClient.transfer_to_creator("acct_1", Decimal("1.005"), "idem-1")
        create.assert_not_called()

    def test_rejects_configured_currency_outside_the_iso_allowlist(self):
        with patch.object(settings, "DEFAULT_CURRENCY", "ZZZ"):
            with patch("stripe.Transfer.create") as create:
                with pytest.raises(Exception, match="Unsupported currency code"):
                    StripeClient.transfer_to_creator("acct_1", Decimal("1.00"), "idem-1")
        create.assert_not_called()

    def test_wraps_sdk_error_into_domain_error(self):
        with patch("stripe.Transfer.create", side_effect=_api_error("destination missing")):
            with pytest.raises(StripeError) as exc:
                StripeClient.transfer_to_creator("acct_x", Decimal("1.00"), "idem-1")
        assert "Failed to transfer to creator" in str(exc.value)
        assert "destination missing" in str(exc.value)
        assert isinstance(exc.value.__cause__, _StripeError)

    def test_idempotent_replay_returns_same_amount_conversion(self):
        # Two calls with the same key must build byte-identical arguments so
        # Stripe recognises the replay rather than creating a second transfer.
        with patch("stripe.Transfer.create") as create:
            StripeClient.transfer_to_creator("acct_1", Decimal("10.00"), "idem-1")
            StripeClient.transfer_to_creator("acct_1", Decimal("10.00"), "idem-1")
        first, second = create.call_args_list
        assert first.kwargs == second.kwargs
        assert first.kwargs["amount"] == 1000


# ---------------------------------------------------------------------------
# retrieve_* — reconciliation lookups
# ---------------------------------------------------------------------------


class TestRetrieveHelpers:
    def test_retrieve_refund_returns_dict(self):
        with patch("stripe.Refund.retrieve", return_value=_StripeObj({"id": "re_1"})) as retrieve:
            result = StripeClient.retrieve_refund("re_1")
        retrieve.assert_called_once_with("re_1")
        assert result["id"] == "re_1"

    def test_retrieve_refund_passes_through_a_plain_dict(self):
        # The SDK sometimes already hands back a dict; do not re-wrap it.
        payload = {"id": "re_2", "amount": 100}
        with patch("stripe.Refund.retrieve", return_value=payload):
            result = StripeClient.retrieve_refund("re_2")
        assert result is payload

    def test_retrieve_refund_wraps_sdk_error(self):
        with patch("stripe.Refund.retrieve", side_effect=_api_error("no such refund")):
            with pytest.raises(StripeError) as exc:
                StripeClient.retrieve_refund("re_missing")
        assert "Failed to retrieve refund re_missing" in str(exc.value)
        assert "no such refund" in str(exc.value)

    def test_retrieve_charge_returns_dict(self):
        with patch("stripe.Charge.retrieve", return_value=_StripeObj({"id": "ch_1"})) as retrieve:
            result = StripeClient.retrieve_charge("ch_1")
        retrieve.assert_called_once_with("ch_1")
        assert result["id"] == "ch_1"

    def test_retrieve_charge_passes_through_a_plain_dict(self):
        payload = {"id": "ch_2"}
        with patch("stripe.Charge.retrieve", return_value=payload):
            assert StripeClient.retrieve_charge("ch_2") is payload

    def test_retrieve_charge_wraps_sdk_error(self):
        with patch("stripe.Charge.retrieve", side_effect=_api_error("gone")):
            with pytest.raises(StripeError) as exc:
                StripeClient.retrieve_charge("ch_missing")
        assert "Failed to retrieve charge ch_missing" in str(exc.value)
        assert isinstance(exc.value.__cause__, _StripeError)

    def test_retrieve_payment_intent_returns_dict(self):
        with patch("stripe.PaymentIntent.retrieve", return_value=_StripeObj({"id": "pi_1"})) as retrieve:
            result = StripeClient.retrieve_payment_intent("pi_1")
        retrieve.assert_called_once_with("pi_1")
        assert result["id"] == "pi_1"

    def test_retrieve_payment_intent_passes_through_a_plain_dict(self):
        payload = {"id": "pi_2"}
        with patch("stripe.PaymentIntent.retrieve", return_value=payload):
            assert StripeClient.retrieve_payment_intent("pi_2") is payload

    def test_retrieve_payment_intent_wraps_sdk_error(self):
        with patch("stripe.PaymentIntent.retrieve", side_effect=_api_error("expired")):
            with pytest.raises(StripeError) as exc:
                StripeClient.retrieve_payment_intent("pi_missing")
        assert "Failed to retrieve payment_intent pi_missing" in str(exc.value)
        assert "expired" in str(exc.value)
