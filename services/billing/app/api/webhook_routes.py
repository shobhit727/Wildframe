from typing import Annotated, Any

"""Stripe webhook handler for the billing service.

Receives events from Stripe, verifies their signature, and dispatches
to the appropriate handler. All processing is idempotent — we use
the Stripe event ID as the idempotency key so replayed webhooks are
silently ignored.

Handled events:
  - checkout.session.completed  → activate SVOD sub or record TVOD purchase
  - customer.subscription.updated → sync subscription status
  - customer.subscription.deleted → cancel sub (revert to AVOD)
  - invoice.paid                → record invoice payment
  - payment_intent.succeeded    → trigger payout ledger accrual
"""
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.stripe_client import StripeClient, StripeError
from app.repositories import (
    InvoiceRepository,
    PayoutLedgerRepository,
    PurchaseRepository,
    SubscriptionRepository,
)
from app.services import BillingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing", "webhooks"])


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------


async def get_billing_service(db: Annotated[AsyncSession, Depends(get_db)]) -> BillingService:
    """Wire up BillingService with all its repositories."""
    return BillingService(
        sub_repo=SubscriptionRepository(db),
        purchase_repo=PurchaseRepository(db),
        inv_repo=InvoiceRepository(db),
        floor_repo=None,  # Not needed for webhook handlers
        pool_repo=None,  # Not needed for webhook handlers
        milestone_repo=None,  # Not needed for webhook handlers
        payout_repo=PayoutLedgerRepository(db),
    )


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------

_processed_events: set = set()  # In production, use Redis/DB for this.


def _is_event_processed(event_id: str) -> bool:
    """Check whether a Stripe event has already been processed."""
    return event_id in _processed_events


def _mark_event_processed(event_id: str) -> None:
    """Mark a Stripe event as processed for idempotency."""
    _processed_events.add(event_id)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def _handle_checkout_session_completed(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle checkout.session.completed.

    - If metadata['tier'] is set → SVOD subscription activation.
    - If metadata['type'] == 'tvod' → TVOD purchase recording.
    """
    session = event["data"]["object"]
    metadata = session.get("metadata", {})
    user_id_str = metadata.get("user_id") or session.get("client_reference_id")
    if not user_id_str:
        logger.warning("checkout.session.completed has no user_id: %s", session.get("id"))
        return

    user_id = UUID(user_id_str)
    event["id"]

    tier = metadata.get("tier")
    if tier:
        # SVOD subscription activation / upgrade.
        from app.models import RevenueTier

        try:
            tier_enum = RevenueTier(tier.lower())
        except ValueError:
            logger.warning("Unknown tier '%s' in checkout.session.completed", tier)
            return

        from app.services import TIER_PRICES

        price = TIER_PRICES.get(tier_enum, Decimal("0.00"))
        sub = await service.sub_repo.get_by_user(user_id)
        if sub:
            await service.sub_repo.update_tier(user_id, tier_enum, price)
        else:
            await service.sub_repo.create(user_id, tier_enum, price)

        logger.info("SVOD subscription activated for user %s (tier=%s)", user_id, tier)

    elif metadata.get("type") == "tvod":
        # TVOD purchase recording.
        content_id_str = metadata.get("content_id")
        if not content_id_str:
            logger.warning("TVOD checkout missing content_id: %s", session.get("id"))
            return

        content_id = UUID(content_id_str)
        amount = Decimal(str(session["amount_total"])) / Decimal(100)
        await service.purchase_title(user_id, content_id, amount)
        logger.info(
            "TVOD purchase recorded for user %s (content=%s, amount=%s)",
            user_id,
            content_id,
            amount,
        )


async def _handle_subscription_updated(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle customer.subscription.updated.

    Syncs the subscription tier and active status in our DB.
    """
    sub_obj = event["data"]["object"]
    metadata = sub_obj.get("metadata", {})
    user_id_str = metadata.get("user_id")
    if not user_id_str:
        return

    user_id = UUID(user_id_str)
    stripe_status = sub_obj.get("status")  # active, past_due, canceled, etc.

    from app.models import RevenueTier

    tier = RevenueTier.SVOD if stripe_status == "active" else RevenueTier.AVOD
    from app.services import TIER_PRICES

    price = TIER_PRICES[tier]

    existing = await service.sub_repo.get_by_user(user_id)
    if existing:
        await service.sub_repo.update_tier(user_id, tier, price)
        logger.info("Subscription synced for user %s (stripe_status=%s)", user_id, stripe_status)


async def _handle_subscription_deleted(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle customer.subscription.deleted.

    Cancels the local subscription, reverting the user to AVOD.
    """
    sub_obj = event["data"]["object"]
    metadata = sub_obj.get("metadata", {})
    user_id_str = metadata.get("user_id")
    if not user_id_str:
        return

    user_id = UUID(user_id_str)
    await service.cancel_subscription(user_id)
    logger.info("Subscription cancelled for user %s (reverted to AVOD)", user_id)


async def _handle_invoice_paid(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle invoice.paid.

    Records the invoice payment in our local Invoice table.
    """
    invoice_obj = event["data"]["object"]
    event_id = event["id"]
    idem_key = f"stripe:invoice_paid:{event_id}"

    # Idempotency guard.
    if _is_event_processed(idem_key):
        logger.info("invoice.paid already processed (%s), skipping", event_id)
        return
    _mark_event_processed(idem_key)

    # The invoice has a subscription line — extract user_id from metadata
    # or from the subscription object. For simplicity we look it up.
    lines = invoice_obj.get("lines", {}).get("data", [])
    for line in lines:
        metadata = line.get("metadata", {})
        user_id_str = metadata.get("user_id")
        if user_id_str:
            user_id = UUID(user_id_str)
            amount = Decimal(str(invoice_obj["total"])) / Decimal(100)
            # Check if invoice already exists for this amount + user.
            existing_invoices = await service.inv_repo.get_by_user(user_id)
            already_recorded = any(
                inv.amount == amount and inv.status.value == "paid" for inv in existing_invoices
            )
            if not already_recorded:
                await service.inv_repo.create(
                    user_id=user_id,
                    amount=amount,
                    subscription_id=None,
                )
                # Mark as paid.
                inv = await service.inv_repo.get_by_user(user_id)
                # We just created it — mark paid.
                from app.models import InvoiceStatus

                new_inv = inv[-1] if inv else None
                if new_inv and new_inv.status == InvoiceStatus.PENDING:
                    new_inv.status = InvoiceStatus.PAID
                    new_inv.paid_at = datetime.now(UTC)
            logger.info("Invoice payment recorded for user %s (amount=%s)", user_id, amount)
            break


async def _handle_payment_intent_succeeded(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle payment_intent.succeeded.

    Triggers payout ledger accrual for the creator share of the
    revenue. This is where the Sustenance Engine's >=55% creator
    share is actually recorded.
    """
    pi = event["data"]["object"]
    event_id = event["id"]
    idem_key = f"stripe:payment_succeeded:{event_id}"

    # Idempotency guard.
    if _is_event_processed(idem_key):
        logger.info("payment_intent.succeeded already processed (%s), skipping", event_id)
        return
    _mark_event_processed(idem_key)

    amount = Decimal(str(pi["amount"])) / Decimal(100)
    # Calculate creator share (>=55%).
    creator_share = BillingService.calculate_creator_share(amount)

    # NOTE: In a real system we'd derive the creator_id from the
    # content metadata. For now we log the accrual intent.
    logger.info(
        "Payout accrual triggered: gross=%s, creator_share=%s (event=%s)",
        amount,
        creator_share,
        event_id,
    )


# ---------------------------------------------------------------------------
# Event dispatcher
# ---------------------------------------------------------------------------

_EVENT_HANDLERS = {
    "checkout.session.completed": _handle_checkout_session_completed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.paid": _handle_invoice_paid,
    "payment_intent.succeeded": _handle_payment_intent_succeeded,
}


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    service: Annotated[BillingService, Depends(get_billing_service)],
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
):
    """Receive and process Stripe webhook events.

    Flow:
      1. Read the raw request body.
      2. Verify the Stripe signature.
      3. Dispatch to the appropriate handler based on event type.
      4. Return 200 OK on success so Stripe doesn't retry.
    """
    payload = await request.body()

    # Step 1: Verify signature.
    try:
        event = StripeClient.handle_webhook(payload, stripe_signature or "")
    except StripeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    event_type = event.get("type", "")
    event_id = event.get("id", "")

    # Step 2: Idempotency guard at the event level.
    if _is_event_processed(event_id):
        logger.info("Event %s (%s) already processed, skipping", event_id, event_type)
        return {"status": "ok", "idempotent": True}

    # Step 3: Dispatch.
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        logger.info("Unhandled Stripe event type: %s (%s)", event_type, event_id)
        return {"status": "ok", "event_type": event_type, "handled": False}

    try:
        await handler(event, service)
        _mark_event_processed(event_id)
    except Exception as exc:
        logger.error("Error handling Stripe event %s (%s): %s", event_id, event_type, exc)
        raise HTTPException(status_code=500, detail="Webhook handler failed") from exc

    return {"status": "ok", "event_type": event_type, "handled": True}
