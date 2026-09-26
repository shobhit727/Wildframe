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
from app.core.money import CurrencyError, from_minor_units, to_minor_units, validate_currency
from app.core.settings import settings
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
from app.services import BillingError, BillingService

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
    session = event["data"]["object"]
    metadata = session.get("metadata", {})
    user_id_str = metadata.get("user_id") or session.get("client_reference_id")
    if not user_id_str:
        logger.warning("checkout.session.completed has no user_id: %s", session.get("id"))
        return

    user_id = UUID(user_id_str)
    tier = metadata.get("tier")
    if tier:
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
        content_id_str = metadata.get("content_id")
        if not content_id_str:
            logger.warning("TVOD checkout missing content_id: %s", session.get("id"))
            return

        content_id = UUID(content_id_str)
        payment_status = session.get("payment_status")
        if payment_status != "paid":
            raise BillingError(f"Checkout session not paid: {payment_status}")
        currency = session.get("currency", "USD").upper()
        try:
            validate_currency(currency)
        except CurrencyError as exc:
            raise BillingError(str(exc)) from exc
        if currency != settings.DEFAULT_CURRENCY:
            raise BillingError(
                f"Currency mismatch: expected {settings.DEFAULT_CURRENCY} got {currency}"
            )
        amount_total = session.get("amount_total")
        if amount_total is None:
            raise BillingError("Missing amount_total in checkout session")
        try:
            canonical_price = await service._fetch_content_price(content_id)
        except ValueError as exc:
            raise BillingError(str(exc)) from exc
        expected_minor = to_minor_units(canonical_price, currency)
        if int(amount_total) != expected_minor:
            raise BillingError(
                f"Amount mismatch: expected {expected_minor} got {amount_total} for content {content_id}"
            )
        stripe_payment_intent_id = session.get("payment_intent")
        await service.purchase_title(
            user_id,
            content_id,
            currency=currency,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        logger.info(
            "TVOD purchase recorded for user %s (content=%s, amount=%s)",
            user_id,
            content_id,
            canonical_price,
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
            currency = invoice_obj.get("currency", "USD").upper()
            try:
                validate_currency(currency)
                total_minor = int(invoice_obj["total"])
                # Convert using ISO-4217 minor units; dividing by 100 breaks JPY/BHD/etc.
                amount = from_minor_units(total_minor, currency)
            except (CurrencyError, KeyError, TypeError, ValueError) as exc:
                raise BillingError(f"Invalid invoice amount/currency: {exc}") from exc

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
    pi = event["data"]["object"]
    pi_id = pi.get("id")
    if not pi_id:
        raise BillingError("Missing payment_intent id")
    amount_minor = pi.get("amount")
    if amount_minor is None:
        amount_minor = pi.get("amount_received")
    if amount_minor is None:
        raise BillingError("Missing amount in payment_intent")
    try:
        amount_minor = int(amount_minor)
    except (TypeError, ValueError) as exc:
        raise BillingError(f"Invalid amount {amount_minor}") from exc
    currency_raw = pi.get("currency") or ""
    if not currency_raw:
        raise BillingError("Missing currency in payment_intent")
    currency = currency_raw.strip().upper()
    try:
        validate_currency(currency)
    except CurrencyError as exc:
        raise BillingError(str(exc)) from exc
    if currency != settings.DEFAULT_CURRENCY:
        raise BillingError(
            f"Currency mismatch: expected {settings.DEFAULT_CURRENCY} got {currency}"
        )
    from app.core.money import from_minor_units, to_minor_units

    amount = from_minor_units(amount_minor, currency)
    creator_share = BillingService.calculate_creator_share(amount)
    purchase = None
    content_id = None
    if hasattr(service.purchase_repo, "get_by_stripe_payment_intent_id"):
        purchase = await service.purchase_repo.get_by_stripe_payment_intent_id(pi_id)
    if purchase is not None:
        content_id = purchase.content_id
    else:
        metadata = pi.get("metadata") or {}
        content_id_str = metadata.get("content_id") or metadata.get("contentId")
        if content_id_str:
            try:
                content_id = UUID(str(content_id_str))
            except ValueError as exc:
                raise BillingError(
                    f"Invalid content_id in payment_intent metadata: {content_id_str}"
                ) from exc
        else:
            raise BillingError(
                "Missing content_id in payment_intent metadata and no purchase found for pi"
            )
    try:
        canonical_price, authoritative_creator_id = await service._fetch_content_details(content_id)
    except ValueError as exc:
        raise BillingError(str(exc)) from exc
    expected_minor = to_minor_units(canonical_price, currency)
    if int(amount_minor) != expected_minor:
        raise BillingError(
            f"Amount mismatch: expected {expected_minor} got {amount_minor} for content {content_id}"
        )
    invoice = None
    if purchase is not None:
        try:
            purchase_minor = to_minor_units(purchase.price, purchase.currency)
        except CurrencyError as exc:
            raise BillingError(str(exc)) from exc
        if purchase_minor != expected_minor or purchase_minor != int(amount_minor):
            raise BillingError(
                f"Amount mismatch: purchase amount {purchase.price} vs payment {amount_minor}"
            )
        if purchase.currency.upper() != currency:
            raise BillingError(
                f"Currency mismatch: purchase currency {purchase.currency} vs payment {currency}"
            )
        if hasattr(service.inv_repo, "get_by_purchase_id"):
            invoice = await service.inv_repo.get_by_purchase_id(purchase.id)
        if invoice is not None:
            try:
                invoice_minor = to_minor_units(invoice.amount, invoice.currency)
            except CurrencyError as exc:
                raise BillingError(str(exc)) from exc
            if invoice_minor != int(amount_minor):
                raise BillingError(
                    f"Invoice amount mismatch: expected {invoice_minor} got {amount_minor}"
                )
            if invoice.currency.upper() != currency:
                raise BillingError(
                    f"Currency mismatch: invoice currency {invoice.currency} vs payment {currency}"
                )
            if invoice.status != InvoiceStatus.PAID:
                invoice.status = InvoiceStatus.PAID
                invoice.paid_at = datetime.utcnow()
    idem_key = f"pi:{pi_id}"
    now = datetime.utcnow()
    cycle_start = now
    cycle_end = now
    if purchase is not None:
        try:
            cycle_start = purchase.purchased_at or now
        except AttributeError:
            cycle_start = now
        if invoice is not None:
            try:
                cycle_start = invoice.issued_at or cycle_start
            except AttributeError:
                pass
    breakdown = {
        "type": "tvod",
        "content_id": str(content_id),
        "payment_intent": pi_id,
        "event_id": event.get("id"),
        "gross": str(amount),
        "currency": currency,
    }
    await service.accrue_payout(
        creator_id=authoritative_creator_id,
        amount=creator_share,
        currency=currency,
        idempotency_key=idem_key,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        breakdown=breakdown,
    )
    logger.info(
        "Payout accrual created: gross=%s creator_share=%s creator=%s pi=%s event=%s",
        amount,
        creator_share,
        authoritative_creator_id,
        pi_id,
        event.get("id"),
    )


async def _process_single_refund_obj(
    refund_obj: dict[str, Any],
    service: BillingService,
    charge_fallback: str | None = None,
    charge_obj: dict[str, Any] | None = None,
) -> None:
    refund_id = refund_obj.get("id")
    if not refund_id:
        return
    charge_id = refund_obj.get("charge") or charge_fallback or ""
    pi_id = refund_obj.get("payment_intent")
    if not pi_id and charge_obj:
        pi_id = charge_obj.get("payment_intent")
    stripe_invoice_id = None
    if charge_obj:
        stripe_invoice_id = charge_obj.get("invoice")
    amount_minor = refund_obj.get("amount")
    if amount_minor is None:
        amount_minor = refund_obj.get("amount_refunded", 0)
    try:
        amount_minor = int(amount_minor)
    except Exception as exc:
        raise BillingError(f"Invalid refund amount {amount_minor}") from exc
    currency_raw = refund_obj.get("currency") or ""
    if not currency_raw and charge_obj:
        currency_raw = charge_obj.get("currency", "usd")
    currency = (currency_raw or "usd").strip().upper()
    try:
        validate_currency(currency)
    except CurrencyError as exc:
        raise BillingError(str(exc)) from exc
    from app.core.money import from_minor_units

    amount = from_minor_units(amount_minor, currency)
    user_id = None
    invoice_id = None
    metadata = refund_obj.get("metadata") or {}
    if not metadata and charge_obj:
        metadata = charge_obj.get("metadata") or {}
    if metadata.get("user_id"):
        try:
            user_id = UUID(str(metadata["user_id"]))
        except ValueError as exc:
            raise BillingError(f"Invalid user_id {metadata['user_id']}") from exc
    if metadata.get("invoice_id"):
        try:
            invoice_id = UUID(str(metadata["invoice_id"]))
        except ValueError as exc:
            raise BillingError(f"Invalid invoice_id {metadata['invoice_id']}") from exc
    reason = refund_obj.get("reason")
    await service.process_refund(
        refund_id=refund_id,
        charge_id=charge_id or "",
        amount=amount,
        currency=currency,
        invoice_id=invoice_id,
        user_id=user_id,
        reason=reason,
        payment_intent_id=str(pi_id) if pi_id else None,
        stripe_invoice_id=str(stripe_invoice_id) if stripe_invoice_id else None,
    )
    logger.info(
        "Refund %s processed for charge %s (amount=%s %s)",
        refund_id,
        charge_id,
        amount,
        currency,
    )


async def _handle_refund(
    event: dict[str, Any],
    service: BillingService,
) -> None:
    event_type = event.get("type")
    obj = event["data"]["object"]
    if event_type == "refund.created":
        await _process_single_refund_obj(obj, service)
    elif event_type == "charge.refunded":
        charge_id = obj.get("id")
        refunds = obj.get("refunds")
        data: list[dict[str, Any]] = []
        if isinstance(refunds, dict):
            data = refunds.get("data", []) or []
        elif isinstance(refunds, list):
            data = refunds
        if data:
            for refund_obj in data:
                await _process_single_refund_obj(
                    refund_obj, service, charge_fallback=charge_id, charge_obj=obj
                )
        else:
            try:
                charge_obj = StripeClient.retrieve_charge(str(charge_id))
                refunds2 = charge_obj.get("refunds", {})
                rdata: list[dict[str, Any]] = []
                if isinstance(refunds2, dict):
                    rdata = refunds2.get("data", []) or []
                elif isinstance(refunds2, list):
                    rdata = refunds2
                if rdata:
                    for refund_obj in rdata:
                        await _process_single_refund_obj(
                            refund_obj, service, charge_fallback=charge_id, charge_obj=charge_obj
                        )
                    return
                logger.warning("charge.refunded %s has no refunds", charge_id)
            except Exception as exc:
                logger.warning(
                    "charge.refunded %s without refunds data and lookup failed: %s", charge_id, exc
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
    return {"status": "ok", "event_id": event_id, "handled": True}
