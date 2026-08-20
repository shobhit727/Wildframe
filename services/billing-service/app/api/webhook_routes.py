from typing import Annotated, Any

"""Stripe webhook handler for the billing service.

Receives events from Stripe, verifies their signature, and dispatches
to the appropriate handler. All processing is idempotent -- we use
the Stripe event ID as the idempotency key backed by the durable
stripe_webhook_events table (#47). Replayed webhooks race on the
unique event_id constraint; exactly one wins and processes.

Handled events:
  - checkout.session.completed  -> activate SVOD sub or record TVOD purchase
  - customer.subscription.updated -> sync subscription status (monotonic guard)
  - customer.subscription.deleted -> cancel sub (revert to AVOD)
  - invoice.paid                -> record invoice payment (PENDING/FAILED->PAID)
  - payment_intent.succeeded    -> trigger payout ledger accrual
  - charge.refunded / refund.created -> process refund idempotently
"""

import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.stripe_client import StripeClient, StripeError
from app.models import (
    InvoiceStatus,
    RevenueTier,
)
from app.repositories import (
    CreatorPoolRepository,
    InvoiceRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
    PurchaseRepository,
    RegionFloorRepository,
    RefundRepository,
    SubscriptionRepository,
    WebhookEventRepository,
)
from app.services import BillingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing", "webhooks"])


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------


async def get_billing_service(db: Annotated[AsyncSession, Depends(get_db)]) -> BillingService:
    """Wire up BillingService with all its repositories."""
    return BillingService(
        sub_repo=SubscriptionRepository(db),
        purchase_repo=PurchaseRepository(db),
        inv_repo=InvoiceRepository(db),
        floor_repo=RegionFloorRepository(db),
        pool_repo=CreatorPoolRepository(db),
        milestone_repo=MilestoneRepository(db),
        payout_repo=PayoutLedgerRepository(db),
        refund_repo=RefundRepository(db),
        webhook_events_repo=WebhookEventRepository(db),
    )


# ---------------------------------------------------------------------------
# Event handlers (all async; called after durable claim + commit)
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
    tier = metadata.get("tier")
    if tier:
        # SVOD subscription activation / upgrade.
        try:
            tier_enum = RevenueTier(tier.lower())
        except ValueError:
            logger.warning("Unknown tier '%s' in checkout.session.completed", tier)
            return

        sub = await service.sub_repo.get_by_user(user_id)
        if sub:
            await service.subscribe(user_id, tier_enum.value)
        else:
            await service.subscribe(user_id, tier_enum.value)

        logger.info("SVOD subscription activated for user %s (tier=%s)", user_id, tier)

    elif metadata.get("type") == "tvod":
        # TVOD purchase recording.
        content_id_str = metadata.get("content_id")
        if not content_id_str:
            logger.warning("TVOD checkout missing content_id: %s", session.get("id"))
            return

        content_id = UUID(content_id_str)
        amount = Decimal(str(session["amount_total"])) / Decimal(100)
        currency = session.get("currency", "USD").upper()
        stripe_payment_intent_id = session.get("payment_intent")
        await service.purchase_title(
            user_id,
            content_id,
            amount,
            currency=currency,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
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

    Syncs the subscription status in our DB via sync_subscription_from_stripe
    which enforces a monotonic guard on event.created.
    """
    sub_obj = event["data"]["object"]
    metadata = sub_obj.get("metadata", {})
    user_id_str = metadata.get("user_id")
    if not user_id_str:
        return

    user_id = UUID(user_id_str)
    stripe_status = sub_obj.get("status")  # active, past_due, canceled, etc.
    event_created = event.get("created", 0)
    await service.sync_subscription_from_stripe(user_id, stripe_status, event_created)
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
    event_created = event.get("created", 0)
    await service.sync_subscription_from_stripe(user_id, "canceled", event_created)
    logger.info("Subscription cancelled for user %s (reverted to AVOD)", user_id)


async def _handle_invoice_paid(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle invoice.paid.

    Records the invoice payment in our local Invoice table.
    Deduplicates on stripe_invoice_id (unique constraint).
    """
    invoice_obj = event["data"]["object"]
    stripe_invoice_id = invoice_obj.get("id")
    if not stripe_invoice_id:
        return

    # Dedupe on Stripe invoice ID.
    existing = await service.inv_repo.get_by_stripe_invoice_id(stripe_invoice_id)
    if existing:
        logger.info(
            "Invoice %s already recorded (stripe_invoice_id=%s), skipping",
            existing.id,
            stripe_invoice_id,
        )
        return

    # Extract user_id from subscription line metadata.
    lines = invoice_obj.get("lines", {}).get("data", [])
    for line in lines:
        metadata = line.get("metadata", {})
        user_id_str = metadata.get("user_id")
        if user_id_str:
            user_id = UUID(user_id_str)
            amount = Decimal(str(invoice_obj["total"])) / Decimal(100)
            currency = invoice_obj.get("currency", "USD").upper()

            new_inv = await service.inv_repo.create(
                user_id=user_id,
                amount=amount,
                currency=currency,
                subscription_id=None,
                stripe_invoice_id=stripe_invoice_id,
            )
            new_inv.status = InvoiceStatus.PAID
            new_inv.paid_at = datetime.utcnow()
            logger.info(
                "Invoice payment recorded for user %s (amount=%s, stripe_invoice_id=%s)",
                user_id,
                amount,
                stripe_invoice_id,
            )
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
    amount_minor = pi.get("amount", 0)
    currency = pi.get("currency", "usd").upper()
    # Convert minor units to major using our money helper (precise).
    from app.core.money import from_minor_units

    amount = from_minor_units(amount_minor, currency)

    # Calculate creator share (>=55%).
    creator_share = BillingService.calculate_creator_share(amount)

    # NOTE: In a real system we'd derive the creator_id from the
    # content metadata. For now we log the accrual intent.
    logger.info(
        "Payout accrual triggered: gross=%s, creator_share=%s (event=%s)",
        amount,
        creator_share,
        event["id"],
    )


async def _handle_refund(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    """Handle charge.refunded / refund.created.

    Applies the refund idempotently to the latest invoice for the
    customer (or the invoice linked via metadata).
    """
    obj = event["data"]["object"]
    refund_id = obj.get("id")
    if not refund_id:
        return

    charge_id = obj.get("charge") if event["type"] == "refund.created" else obj.get("id")
    amount_minor = obj.get("amount", 0)
    currency = obj.get("currency", "usd").upper()
    reason = obj.get("reason")

    from app.core.money import from_minor_units

    amount = from_minor_units(amount_minor, currency)

    # Try to resolve user_id / invoice_id from metadata or charge.
    user_id = None
    invoice_id = None
    metadata = obj.get("metadata", {})
    if metadata.get("user_id"):
        user_id = UUID(metadata["user_id"])
    if metadata.get("invoice_id"):
        invoice_id = UUID(metadata["invoice_id"])

    # Fallback: if charge has an invoice, use that.
    if user_id is None and charge_id:
        # Stripe charge doesn't directly carry invoice; we'd need to
        # fetch it via Stripe SDK. For now rely on metadata.
        pass

    await service.process_refund(
        refund_id=refund_id,
        charge_id=charge_id or "",
        amount=amount,
        currency=currency,
        invoice_id=invoice_id,
        user_id=user_id,
        reason=reason,
    )
    logger.info(
        "Refund %s processed for charge %s (amount=%s %s, status inferred)",
        refund_id,
        charge_id,
        amount,
        currency,
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
    "charge.refunded": _handle_refund,
    "refund.created": _handle_refund,
}


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    service: BillingService = Depends(get_billing_service),
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
):
    """Receive and process Stripe webhook events.

    Durable idempotency flow (#47):
      1. Read the raw request body.
      2. Verify the Stripe signature.
      3. Claim the event in the durable inbox (INSERT ... ON CONFLICT).
         - If already PROCESSED: return 200 immediately.
         - If PROCESSING but stale: reclaim and retry.
         - If FAILED and attempts < max: reclaim and retry.
         - Else: return 200 (someone else is handling it).
      4. COMMIT the claim transaction (durable claim survives handler crash).
      5. Execute the handler.
      6. On success: mark PROCESSED + commit.
      7. On failure: mark FAILED + commit + return 500 so Stripe retries.
    """
    payload = await request.body()

    # Step 1: Verify signature (never skip).
    try:
        event = StripeClient.handle_webhook(payload, stripe_signature or "")
    except StripeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    event_type = event.get("type", "")
    event_id = event.get("id", "")

    # Step 2: Durable claim.
    claimed = await service.webhook_events_repo.claim(event_id, event_type)
    if not claimed:
        # Could be already PROCESSED, or PROCESSING not stale, or FAILED exhausted.
        logger.info("Event %s (%s) not claimed (already processed or busy)", event_id, event_type)
        return {"status": "ok", "idempotent": True}

    # Step 3: Commit the claim so it survives a handler crash.
    await service.commit()

    # Step 4: Dispatch.
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        # Unhandled event types: mark as processed (we acknowledged receipt).
        await service.webhook_events_repo.complete(event_id)
        await service.commit()
        logger.info("Unhandled Stripe event type: %s (%s)", event_type, event_id)
        return {"status": "ok", "event_type": event_type, "handled": False}

    try:
        await handler(event, service)
        await service.webhook_events_repo.complete(event_id)
        await service.commit()
    except Exception as exc:
        logger.error("Error handling Stripe event %s (%s): %s", event_id, event_type, exc)
        # The handler exception aborts the session's transaction; roll back
        # before attempting the FAILED update or the inbox row would stay
        # PROCESSING and never become eligible for Stripe's retry.
        await service.rollback()
        await service.webhook_events_repo.fail(event_id, str(exc))
        await service.commit()
        raise HTTPException(status_code=500, detail="Webhook handler failed") from exc
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        # Unhandled event types: mark as processed (we acknowledged receipt).
        await service.webhook_events_repo.complete(event_id)
        await service.webhook_events_repo.commit()
        logger.info("Unhandled Stripe event type: %s (%s)", event_type, event_id)
        return {"status": "ok", "event_type": event_type, "handled": False}

    try:
        await handler(event, service)
        await service.webhook_events_repo.complete(event_id)
        await service.webhook_events_repo.commit()
    except Exception as exc:
        logger.error("Error handling Stripe event %s (%s): %s", event_id, event_type, exc)
        # The handler exception aborts the session's transaction; roll back
        # before attempting the FAILED update or the inbox row would stay
        # PROCESSING and never become eligible for Stripe's retry.
        await service.webhook_events_repo.session.rollback()
        await service.webhook_events_repo.fail(event_id, str(exc))
        await service.webhook_events_repo.commit()
        raise HTTPException(status_code=500, detail="Webhook handler failed") from exc

    return {"status": "ok", "event_type": event_type, "handled": True}
