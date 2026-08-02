from typing import Any

"""Stripe Connect client for the Wildframe billing service.

Wraps the Stripe SDK to provide:
  - SVOD subscription checkout sessions (recurring $7.99/mo)
  - TVOD one-off purchase checkout sessions
  - Webhook signature verification + event parsing
  - Stripe Connect account onboarding for creators
  - Transfers to creator Connect accounts (idempotent payouts)

All methods raise StripeError on failure so callers can translate
into domain-specific errors.
"""
import logging
from decimal import Decimal
from uuid import UUID

import stripe

from app.core.settings import settings

logger = logging.getLogger(__name__)

# Set the Stripe API key once at module load.
stripe.api_key = settings.STRIPE_API_KEY


class StripeError(Exception):
    """Raised when a Stripe API call fails."""


class StripeClient:
    """Low-level Stripe Connect integration.

    Each method corresponds to a single Stripe API interaction and
    handles the most common Stripe errors, logging them and raising
    a domain-friendly StripeError.
    """

    # -----------------------------------------------------------------------
    # SVOD subscription checkout
    # -----------------------------------------------------------------------

    @staticmethod
    def create_checkout_session(
        user_id: UUID,
        price_id: str,
        tier: str,
        success_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        """Create a Stripe Checkout Session for an SVOD subscription.

        The session is created in ``subscription`` mode so Stripe
        automatically handles recurring billing. We pass the user_id
        and tier in ``client_reference_id`` / ``metadata`` so the
        webhook handler can correlate the event back to our domain.

        Returns the Stripe Session object (as a dict-like object).
        """
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(user_id),
                metadata={
                    "user_id": str(user_id),
                    "tier": tier,
                },
            )
            logger.info(
                "Created SVOD checkout session for user %s (tier=%s): %s",
                user_id, tier, session.id,
            )
            return session
        except stripe.error.StripeError as exc:
            logger.error("Stripe create_checkout_session failed: %s", exc)
            raise StripeError(f"Failed to create checkout session: {exc}") from exc

    # -----------------------------------------------------------------------
    # TVOD one-off purchase checkout
    # -----------------------------------------------------------------------

    @staticmethod
    def create_tvod_purchase_session(
        user_id: UUID,
        content_id: UUID,
        price: Decimal,
        success_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        """Create a Stripe Checkout Session for a one-off TVOD purchase.

        The session is created in ``payment`` mode. The price is passed
        as a PriceData object so each title can have its own price
        without pre-creating Stripe Price objects.

        Returns the Stripe Session object.
        """
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": settings.DEFAULT_CURRENCY.lower(),
                        "product_data": {
                            "name": f"Title {content_id}",
                            "metadata": {"content_id": str(content_id)},
                        },
                        "unit_amount": int(price * 100),  # Stripe uses cents
                    },
                    "quantity": 1,
                }],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(user_id),
                metadata={
                    "user_id": str(user_id),
                    "content_id": str(content_id),
                    "type": "tvod",
                },
            )
            logger.info(
                "Created TVOD checkout session for user %s (content=%s): %s",
                user_id, content_id, session.id,
            )
            return session
        except stripe.error.StripeError as exc:
            logger.error("Stripe create_tvod_purchase_session failed: %s", exc)
            raise StripeError(f"Failed to create TVOD purchase session: {exc}") from exc

    # -----------------------------------------------------------------------
    # Webhook verification
    # -----------------------------------------------------------------------

    @staticmethod
    def handle_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
        """Verify and parse a Stripe webhook event.

        Uses the raw request body (not the parsed JSON) because
        Stripe's signature verification requires the exact bytes.

        Returns the parsed event dict on success.
        Raises StripeError if the signature is invalid.
        """
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise StripeError("STRIPE_WEBHOOK_SECRET is not configured")

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET,
            )
            logger.info("Verified Stripe webhook event %s (%s)", event.id, event.type)
            return event
        except stripe.error.SignatureVerificationError as exc:
            logger.warning("Invalid Stripe webhook signature: %s", exc)
            raise StripeError("Invalid webhook signature") from exc
        except stripe.error.StripeError as exc:
            logger.error("Stripe webhook construct_event failed: %s", exc)
            raise StripeError(f"Webhook processing error: {exc}") from exc

    # -----------------------------------------------------------------------
    # Stripe Connect — creator onboarding
    # -----------------------------------------------------------------------

    @staticmethod
    def create_connect_account(
        creator_id: UUID,
        country: str,
        email: str,
    ) -> dict[str, Any]:
        """Onboard a creator to Stripe Connect (Express account type).

        Express accounts are the recommended type for marketplaces —
        Stripe handles the dashboard, KYC, and payout UI for the
        creator. We store the returned account ID on the creator's
        profile so we can transfer to it later.

        Returns the Stripe Account object.
        """
        try:
            account = stripe.Account.create(
                type="express",
                country=country,
                email=email,
                metadata={
                    "creator_id": str(creator_id),
                    "platform": "wildframe",
                },
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
            )
            logger.info(
                "Created Stripe Connect account for creator %s: %s",
                creator_id, account.id,
            )
            return account
        except stripe.error.StripeError as exc:
            logger.error("Stripe create_connect_account failed: %s", exc)
            raise StripeError(f"Failed to create Connect account: {exc}") from exc

    # -----------------------------------------------------------------------
    # Stripe Connect — transfer to creator
    # -----------------------------------------------------------------------

    @staticmethod
    def transfer_to_creator(
        creator_stripe_account_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Transfer funds to a creator's Stripe Connect account.

        The amount is in the major currency unit (e.g. dollars) and
        Stripe converts to cents internally. The idempotency_key is
        forwarded to Stripe so retried calls don't create duplicate
        transfers.

        Returns the Stripe Transfer object.
        """
        try:
            transfer = stripe.Transfer.create(
                amount=int(amount * 100),  # Stripe uses cents
                currency=settings.DEFAULT_CURRENCY.lower(),
                destination=creator_stripe_account_id,
                metadata={"platform": "wildframe"},
                idempotency_key=idempotency_key,
            )
            logger.info(
                "Transferred %s %s to creator account %s (transfer=%s, idem_key=%s)",
                amount, settings.DEFAULT_CURRENCY,
                creator_stripe_account_id, transfer.id, idempotency_key,
            )
            return transfer
        except stripe.error.StripeError as exc:
            logger.error("Stripe transfer_to_creator failed: %s", exc)
            raise StripeError(f"Failed to transfer to creator: {exc}") from exc
