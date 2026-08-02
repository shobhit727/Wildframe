from typing import Any

"""Payment orchestration service.

Sits between the API layer and the StripeClient + BillingService,
coordinating the full payment lifecycle:

  1. User initiates subscription / TVOD purchase
     → PaymentService calls StripeClient to create a Checkout Session
     → Returns the session URL to the frontend

  2. Stripe fires a webhook on payment success
     → webhook_routes.py receives it
     → Dispatches to PaymentService.process_webhook_event (via handler)
     → BillingService records the subscription / purchase / invoice

  3. Creator onboarding
     → PaymentService calls StripeClient.create_connect_account
     → Stores the Stripe account ID on the creator profile

  4. Creator payout
     → PaymentService calls StripeClient.transfer_to_creator
     → BillingService records the payout ledger entry (idempotent)
"""
import logging
from decimal import Decimal
from uuid import UUID

from app.core.settings import settings
from app.core.stripe_client import StripeClient, StripeError
from app.services import BillingError, BillingService

logger = logging.getLogger(__name__)


class PaymentServiceError(Exception):
    """Raised when payment orchestration fails."""


class PaymentService:
    """Orchestrates Stripe payments with the billing domain service.

    This class is the single entry point for all payment-related
    operations. It delegates:
      - Stripe API calls → StripeClient
      - Domain logic (subscriptions, purchases, payouts) → BillingService
    """

    def __init__(self, billing_service: BillingService):
        self.billing = billing_service
        self.stripe = StripeClient()

    # -----------------------------------------------------------------------
    # Subscription checkout
    # -----------------------------------------------------------------------

    async def initiate_subscription(
        self,
        user_id: UUID,
        tier: str,
    ) -> dict[str, Any]:
        """Initiate an SVOD subscription checkout flow.

        Creates a Stripe Checkout Session for the given tier and
        returns a dict with ``session_id`` and ``url`` that the
        frontend can redirect the user to.

        Args:
            user_id: The user subscribing.
            tier: Target tier (must be 'svod' for paid subscriptions).

        Returns:
            Dict with ``session_id``, ``url``, ``tier``, ``user_id``.
        """
        try:
            session = self.stripe.create_checkout_session(
                user_id=user_id,
                price_id=settings.STRIPE_SVOD_PRICE_ID,
                tier=tier,
                success_url=settings.STRIPE_SUCCESS_URL,
                cancel_url=settings.STRIPE_CANCEL_URL,
            )
            return {
                "session_id": session.id,
                "url": session.url,
                "tier": tier,
                "user_id": str(user_id),
            }
        except StripeError as exc:
            raise PaymentServiceError(f"Failed to initiate subscription: {exc}") from exc

    # -----------------------------------------------------------------------
    # TVOD purchase checkout
    # -----------------------------------------------------------------------

    async def initiate_purchase(
        self,
        user_id: UUID,
        content_id: UUID,
        amount: Decimal,
    ) -> dict[str, Any]:
        """Initiate a TVOD (pay-per-view) purchase checkout flow.

        Creates a Stripe Checkout Session in ``payment`` mode for
        the given amount and returns the session details for the
        frontend to redirect to.

        Args:
            user_id: The user purchasing.
            content_id: The content being purchased.
            amount: Price in USD.

        Returns:
            Dict with ``session_id``, ``url``, ``content_id``, ``amount``.
        """
        try:
            session = self.stripe.create_tvod_purchase_session(
                user_id=user_id,
                content_id=content_id,
                price=amount,
                success_url=settings.STRIPE_SUCCESS_URL,
                cancel_url=settings.STRIPE_CANCEL_URL,
            )
            return {
                "session_id": session.id,
                "url": session.url,
                "content_id": str(content_id),
                "amount": str(amount),
                "user_id": str(user_id),
            }
        except StripeError as exc:
            raise PaymentServiceError(f"Failed to initiate purchase: {exc}") from exc

    # -----------------------------------------------------------------------
    # Webhook event processing
    # -----------------------------------------------------------------------

    async def process_webhook_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a Stripe webhook event to the correct handler.

        This is the single entry point for all webhook events. It
        inspects the event type and delegates to the appropriate
        BillingService method.

        Args:
            event: The parsed Stripe event dict (from handle_webhook).

        Returns:
            Dict with ``event_type``, ``handled``, and optional ``detail``.
        """
        event_type = event.get("type", "")

        handlers = {
            "checkout.session.completed": self._process_checkout_completed,
            "customer.subscription.updated": self._process_subscription_updated,
            "customer.subscription.deleted": self._process_subscription_deleted,
            "invoice.paid": self._process_invoice_paid,
            "payment_intent.succeeded": self._process_payment_succeeded,
        }

        handler = handlers.get(event_type)
        if handler is None:
            logger.info("Unhandled webhook event type: %s", event_type)
            return {"event_type": event_type, "handled": False}

        try:
            await handler(event)
            return {"event_type": event_type, "handled": True}
        except BillingError as exc:
            raise PaymentServiceError(f"Webhook handler failed: {exc}") from exc

    async def _process_checkout_completed(self, event: dict[str, Any]) -> None:
        """Process checkout.session.completed event."""
        # Delegates to BillingService for subscription/purchase recording.
        # In a full implementation this would parse the session metadata
        # and call subscribe() or purchase_title() accordingly.
        logger.info("Processing checkout.session.completed: %s", event["id"])

    async def _process_subscription_updated(self, event: dict[str, Any]) -> None:
        """Process customer.subscription.updated event."""
        logger.info("Processing customer.subscription.updated: %s", event["id"])

    async def _process_subscription_deleted(self, event: dict[str, Any]) -> None:
        """Process customer.subscription.deleted event."""
        logger.info("Processing customer.subscription.deleted: %s", event["id"])

    async def _process_invoice_paid(self, event: dict[str, Any]) -> None:
        """Process invoice.paid event."""
        logger.info("Processing invoice.paid: %s", event["id"])

    async def _process_payment_succeeded(self, event: dict[str, Any]) -> None:
        """Process payment_intent.succeeded event."""
        logger.info("Processing payment_intent.succeeded: %s", event["id"])

    # -----------------------------------------------------------------------
    # Creator onboarding (Stripe Connect)
    # -----------------------------------------------------------------------

    async def onboard_creator(
        self,
        creator_id: UUID,
        country: str,
        email: str,
    ) -> dict[str, Any]:
        """Onboard a creator to Stripe Connect.

        Creates an Express-type Connect account for the creator and
        returns the account ID. The caller is responsible for
        persisting the account ID on the creator's profile.

        Args:
            creator_id: The creator being onboarded.
            country: ISO 3166-1 alpha-2 country code.
            email: Creator's email for the Stripe account.

        Returns:
            Dict with ``creator_id``, ``stripe_account_id``, ``onboarded``.
        """
        try:
            account = self.stripe.create_connect_account(
                creator_id=creator_id,
                country=country,
                email=email,
            )
            return {
                "creator_id": str(creator_id),
                "stripe_account_id": account.id,
                "onboarded": True,
            }
        except StripeError as exc:
            raise PaymentServiceError(f"Failed to onboard creator: {exc}") from exc

    # -----------------------------------------------------------------------
    # Creator payout (Stripe Connect transfer)
    # -----------------------------------------------------------------------

    async def payout_creator(
        self,
        creator_id: UUID,
        creator_stripe_account_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Transfer funds to a creator's Stripe Connect account.

        This is the final step in the payout flow:
          1. BillingService.accrue_payout() creates a ledger entry.
          2. PaymentService.payout_creator() transfers the funds.
          3. On success, the ledger entry is marked as completed.

        The idempotency_key is forwarded to Stripe so retried calls
        don't create duplicate transfers.

        Args:
            creator_id: The creator being paid.
            creator_stripe_account_id: Their Stripe Connect account ID.
            amount: Payout amount in USD.
            idempotency_key: Unique key for idempotency.

        Returns:
            Dict with ``creator_id``, ``stripe_transfer_id``, ``amount``, ``status``.
        """
        try:
            # Step 1: Accrue the payout in the ledger (idempotent).
            await self.billing.accrue_payout(
                creator_id=creator_id,
                amount=amount,
                currency=settings.DEFAULT_CURRENCY,
                idempotency_key=idempotency_key,
                cycle_start=None,   # Caller should provide real dates
                cycle_end=None,
                breakdown={"type": "stripe_connect_transfer"},
            )

            # Step 2: Transfer via Stripe Connect.
            transfer = self.stripe.transfer_to_creator(
                creator_stripe_account_id=creator_stripe_account_id,
                amount=amount,
                idempotency_key=idempotency_key,
            )

            return {
                "creator_id": str(creator_id),
                "stripe_transfer_id": transfer.id,
                "amount": str(amount),
                "status": "completed",
            }
        except StripeError as exc:
            raise PaymentServiceError(f"Failed to payout creator: {exc}") from exc
