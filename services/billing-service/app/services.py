"""Billing service business logic — the Sustenance Engine core.

This module implements the economic model from PRODUCT_VISION.md:
  1. Revenue tiers: AVOD (free), SVOD ($7.99/mo), TVOD (per-title)
  2. >=55% creator share on SVOD revenue (contractual floor)
  3. Living-wage floor per region (guaranteed minimum per minute)
  4. Creator Pool redistribution (15% of net, pro-rata to below-floor)
  5. Milestone-Tranched funding (10/20/30/40 with kill clauses)
  6. Idempotent payout ledger (Stripe Connect transfers)

Invariant: the 55% creator share is calculated BEFORE platform costs.
A creator's *effective floor* is a minimum guarantee, not a cap — top
performers always earn their full pro-rata share above the floor.

State machine invariants (#190/#220/#482):
  - Subscription transitions: ACTIVE <-> CANCELLED (monotonic
    last_stripe_event_ts prevents stale events from regressing state).
  - Invoice transitions: PENDING -> {PAID, FAILED}; FAILED -> {PAID,
    PENDING}; PAID -> REFUNDED; REFUNDED is terminal.
  - Tranche/Payout transitions defined in TRANCHE_TRANSITIONS /
    PAYOUT_TRANSITIONS. All mutations go through ``validate_transition``.

Monetary invariants (#477/#478):
  - All amounts stored as Decimal (exact). Provider boundaries use
    integer minor units via ``to_minor_units`` / ``from_minor_units``.
  - Currency codes validated against ISO-4217 allowlist at financial
    boundaries (stripe_client, service methods, settings validator).
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Mapping
from uuid import UUID

from app.core.money import validate_currency
from app.models import (
    CreatorPoolEntry,
    InvoiceStatus,
    Milestone,
    MilestoneStatus,
    MilestoneTranche,
    PayoutLedger,
    Purchase,
    Refund,
    RefundStatus,
    RevenueTier,
    Subscription,
    SubscriptionStatus,
    TrancheStatus,
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

# ---------------------------------------------------------------------------
# Price table (§3 of PRODUCT_VISION.md)
# ---------------------------------------------------------------------------

TIER_PRICES: dict[RevenueTier, Decimal] = {
    RevenueTier.AVOD: Decimal("0.00"),
    RevenueTier.SVOD: Decimal("7.99"),
    RevenueTier.TVOD: Decimal("0.00"),  # per-title, set at purchase time
}

# ≥55% of net SVOD revenue goes to creators. This is the contractual floor.
CREATOR_SHARE_PERCENTAGE = Decimal("0.55")

# Default Creator Pool percentage of net revenue (§2.2).
CREATOR_POOL_PERCENTAGE = Decimal("0.15")


class BillingError(Exception):
    """Base for billing-domain errors."""


class InvalidStateTransitionError(BillingError):
    """Raised when a state machine transition is not allowed."""


class TierInvalidError(BillingError):
    """Raised when an invalid tier is requested."""


class DuplicatePurchaseError(BillingError):
    """Raised on duplicate TVOD purchase (same user + content)."""


class DuplicatePayoutError(BillingError):
    """Raised when a payout with the same idempotency_key already exists
    with a different amount (real conflict). If same amount, it's a safe
    retry and the accrue is idempotent."""


class MilestoneKillError(BillingError):
    """Raised when trying to release a tranche on a killed milestone."""


# ---------------------------------------------------------------------------
# FSM transition tables (#190/#220)
# ---------------------------------------------------------------------------

SUBSCRIPTION_TRANSITIONS: Mapping[SubscriptionStatus, tuple[SubscriptionStatus, ...]] = {
    SubscriptionStatus.ACTIVE: (SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED),
    SubscriptionStatus.CANCELLED: (SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED),
}

INVOICE_TRANSITIONS: Mapping[InvoiceStatus, tuple[InvoiceStatus, ...]] = {
    InvoiceStatus.PENDING: (InvoiceStatus.PENDING, InvoiceStatus.PAID, InvoiceStatus.FAILED),
    InvoiceStatus.FAILED: (InvoiceStatus.FAILED, InvoiceStatus.PAID, InvoiceStatus.PENDING),
    InvoiceStatus.PAID: (InvoiceStatus.PAID, InvoiceStatus.REFUNDED),
    InvoiceStatus.REFUNDED: (InvoiceStatus.REFUNDED,),
}

TRANCHE_TRANSITIONS: Mapping[TrancheStatus, tuple[TrancheStatus, ...]] = {
    TrancheStatus.LOCKED: (TrancheStatus.LOCKED, TrancheStatus.RELEASED, TrancheStatus.REVERTED),
    TrancheStatus.RELEASED: (TrancheStatus.RELEASED,),
    TrancheStatus.REVERTED: (TrancheStatus.REVERTED,),
}

PAYOUT_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    "accrued": ("accrued", "paid", "cancelled"),
    "paid": ("paid",),
    "cancelled": ("cancelled",),
}


def validate_transition(
    current: str | SubscriptionStatus | InvoiceStatus | TrancheStatus,
    target: str | SubscriptionStatus | InvoiceStatus | TrancheStatus,
    allowed: Mapping,
    *,
    context: str = "transition",
) -> None:
    """Raise InvalidStateTransitionError if (current -> target) is not allowed."""
    cur_key = current.value if hasattr(current, "value") else current
    tgt_key = target.value if hasattr(target, "value") else target
    if cur_key not in allowed or tgt_key not in allowed[cur_key]:
        raise InvalidStateTransitionError(
            f"Invalid {context}: {cur_key} -> {tgt_key} not in {list(allowed.keys())}"
        )


# ---------------------------------------------------------------------------
# BillingService
# ---------------------------------------------------------------------------


class BillingService:
    """Orchestrates all billing-domain operations.

    Depends on repositories (injected) and never accesses the DB session
    directly. Event publishing is delegated to an EventPublisher port so
    the core domain stays free of infrastructure concerns.
    """

    def __init__(
        self,
        sub_repo: SubscriptionRepository,
        purchase_repo: PurchaseRepository,
        inv_repo: InvoiceRepository,
        floor_repo: RegionFloorRepository,
        pool_repo: CreatorPoolRepository,
        milestone_repo: MilestoneRepository,
        payout_repo: PayoutLedgerRepository,
        refund_repo: RefundRepository,
        webhook_events_repo: WebhookEventRepository,
    ):
        self.sub_repo = sub_repo
        self.purchase_repo = purchase_repo
        self.inv_repo = inv_repo
        self.floor_repo = floor_repo
        self.pool_repo = pool_repo
        self.milestone_repo = milestone_repo
        self.payout_repo = payout_repo
        self.refund_repo = refund_repo
        self.webhook_events_repo = webhook_events_repo
        self._logger = logging.getLogger(__name__)

    # -----------------------------------------------------------------------
    # Subscription management
    # -----------------------------------------------------------------------

    async def get_subscription(self, user_id: UUID) -> Subscription | None:
        """Get a user's current subscription tier and details."""
        return await self.sub_repo.get_by_user(user_id)

    async def subscribe(self, user_id: UUID, tier_str: str) -> Subscription:
        """Create or upgrade a subscription to the given tier.

        Validates that the tier is one of AVOD/SVOD/TVOD and applies the
        correct monthly price. TVOD is handled through purchases, not
        subscriptions — but the tier enum is kept for analytics.

        Idempotent: calling with the same tier is a no-op (returns current).
        Reactivates a CANCELLED subscription to ACTIVE on upgrade.
        """
        try:
            tier = RevenueTier(tier_str.lower())
        except ValueError:
            raise TierInvalidError(f"Invalid tier '{tier_str}'. Must be one of: avod, svod, tvod")

        price = TIER_PRICES[tier]
        existing = await self.sub_repo.get_by_user(user_id)
        if existing:
            if existing.tier == tier:
                return existing  # Idempotent no-op.
            # FSM transition on tier change.
            validate_transition(
                existing.status,
                SubscriptionStatus.ACTIVE,
                SUBSCRIPTION_TRANSITIONS,
                context="subscribe",
            )
            existing.tier = tier
            existing.monthly_price = price
            existing.status = SubscriptionStatus.ACTIVE
            existing.is_active = True
            existing.cancelled_at = None
            existing.renewal_date = datetime.utcnow()
            await self.sub_repo.session.flush()
            return existing
        return await self.sub_repo.create(user_id, tier, price)

    async def cancel_subscription(self, user_id: UUID) -> Subscription | None:
        """Cancel a subscription (reverts to AVOD).

        Idempotent: already CANCELLED is a no-op (returns current).
        """
        sub = await self.sub_repo.get_by_user(user_id)
        if not sub:
            return None
        if sub.status == SubscriptionStatus.CANCELLED:
            return sub  # Idempotent no-op.
        validate_transition(
            sub.status, SubscriptionStatus.CANCELLED, SUBSCRIPTION_TRANSITIONS, context="cancel"
        )
        sub.tier = RevenueTier.AVOD
        sub.monthly_price = Decimal("0.00")
        sub.cancelled_at = datetime.utcnow()
        sub.is_active = False
        sub.status = SubscriptionStatus.CANCELLED
        return sub

    # -----------------------------------------------------------------------
    # TVOD purchases
    # -----------------------------------------------------------------------

    async def purchase_title(
        self,
        user_id: UUID,
        content_id: UUID,
        price: Decimal,
        currency: str = "USD",
        stripe_payment_intent_id: str | None = None,
    ) -> Purchase:
        """Record a one-off TVOD purchase (pay-per-view).

        Uses a deterministic idempotency key derived from user+content
        so duplicate requests are safe. Currency validated against ISO-4217.
        """
        validate_currency(currency)
        idem_key = f"tvod:{user_id}:{content_id}"
        existing = await self.purchase_repo.get_by_user_and_content(user_id, content_id)
        if existing:
            return existing

        purchase = await self.purchase_repo.create(
            user_id=user_id,
            content_id=content_id,
            price=price,
            idempotency_key=idem_key,
            currency=currency,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        # Also create an invoice for the purchase.
        await self.inv_repo.create(
            user_id=user_id,
            amount=price,
            currency=currency,
            purchase_id=purchase.id,
        )
        return purchase

    # -----------------------------------------------------------------------
    # Stripe webhook reconciliation
    # -----------------------------------------------------------------------

    async def sync_subscription_from_stripe(
        self, user_id: UUID, stripe_status: str, event_created: int
    ) -> Subscription | None:
        """Reconcile local subscription from a Stripe event (#482).

        Monotonic guard: events with ``created`` older than the last
        applied event are ignored (stale retries). ``stripe_status`` maps
        to our FSM:
          - "active" -> ACTIVE
          - "canceled" / "unpaid" / "incomplete_expired" -> CANCELLED
        """
        sub = await self.sub_repo.get_by_user(user_id)
        if not sub:
            return None
        if sub.last_stripe_event_ts is not None and event_created < sub.last_stripe_event_ts:
            self._logger.warning(
                "Ignoring stale Stripe event for user %s: event_created=%d < last_applied=%d",
                user_id,
                event_created,
                sub.last_stripe_event_ts,
            )
            return sub

        target: SubscriptionStatus | None = None
        if stripe_status == "active":
            target = SubscriptionStatus.ACTIVE
        elif stripe_status in {"canceled", "unpaid", "incomplete_expired"}:
            target = SubscriptionStatus.CANCELLED
        if target is None:
            return sub

        if sub.status != target:
            validate_transition(sub.status, target, SUBSCRIPTION_TRANSITIONS, context="stripe_sync")
            sub.status = target
            sub.is_active = target == SubscriptionStatus.ACTIVE
            if target == SubscriptionStatus.CANCELLED:
                sub.cancelled_at = datetime.utcnow()
                sub.tier = RevenueTier.AVOD
                sub.monthly_price = Decimal("0.00")
        sub.last_stripe_event_ts = event_created
        await self.sub_repo.session.flush()
        return sub

    async def process_refund(
        self,
        refund_id: str,
        charge_id: str,
        amount: Decimal,
        currency: str,
        *,
        invoice_id: UUID | None = None,
        user_id: UUID | None = None,
        reason: str | None = None,
    ) -> Refund:
        """Apply a refund idempotently (#191/#478).

        Duplicate ``refund_id`` returns the existing record (no double-apply).
        If ``invoice_id`` is provided, increments the invoice's
        ``refunded_amount`` under a guarded UPDATE; on bounds violation
        records a REJECTED refund and logs (does not raise — money has
        already left Stripe). If no ``invoice_id`` / ``user_id`` can be
        resolved, records the refund as PROCESSED for audit trail.
        """
        validate_currency(currency)
        existing = await self.refund_repo.get_by_refund_id(refund_id)
        if existing:
            return existing

        # Try to find the invoice if not provided.
        if invoice_id is None:
            if user_id is not None:
                inv = await self.inv_repo.get_latest_for_user(user_id)
                if inv:
                    invoice_id = inv.id

        if invoice_id is not None:
            applied = await self.refund_repo.apply_to_invoice(invoice_id, amount)
            status = RefundStatus.PROCESSED if applied else RefundStatus.REJECTED
        else:
            # No invoice to apply to — record only.
            status = RefundStatus.PROCESSED

        if invoice_id is not None and status == RefundStatus.REJECTED:
            self._logger.warning(
                "Refund %s rejected: would exceed invoice %s amount (refunded=%s, amount=%s)",
                refund_id,
                invoice_id,
                amount,
                amount,  # simplified for log
            )

        refund = await self.refund_repo.create(
            refund_id=refund_id,
            charge_id=charge_id,
            amount=amount,
            currency=currency,
            invoice_id=invoice_id,
            user_id=user_id,
            reason=reason,
            status=status,
        )
        return refund

    # -----------------------------------------------------------------------
    # Sustenance Engine — Floor
    # -----------------------------------------------------------------------

    async def get_floor(self, region_code: str):
        """Fetch the living-wage floor for a region (admin-editable)."""
        return await self.floor_repo.get_by_region(region_code)

    async def list_floors(self):
        """List all regional floor configurations."""
        return await self.floor_repo.list_all()

    # -----------------------------------------------------------------------
    # Sustenance Engine — Creator Pool
    # -----------------------------------------------------------------------

    async def get_pool_status(self):
        """Get the latest Creator Pool entry (balance / redistribution status)."""
        return await self.pool_repo.get_latest()

    async def accrue_pool(
        self,
        cycle_start: datetime,
        cycle_end: datetime,
        net_revenue: Decimal,
    ) -> CreatorPoolEntry:
        """Accrue a Creator Pool entry for a payout cycle.

        15% of net revenue flows into the pool. Actual redistribution to
        below-floor creators happens in the creators-service; this method
        just records the pool contribution.
        """
        pool_pct = CREATOR_POOL_PERCENTAGE
        entry = await self.pool_repo.create_entry(
            cycle_start,
            cycle_end,
            net_revenue,
            pool_pct,
        )
        return entry

    # -----------------------------------------------------------------------
    # Sustenance Engine — Milestone-Tranched Funding
    # -----------------------------------------------------------------------

    async def create_milestone(
        self,
        creator_id: UUID,
        project_title: str,
        total_commitment: Decimal,
    ) -> Milestone:
        """Create a milestone commitment with 4 tranches (10/20/30/40%).

        All tranches start as LOCKED. They are released one at a time as
        milestones are verified.
        """
        return await self.milestone_repo.create(creator_id, project_title, total_commitment)

    async def release_tranche(self, milestone_id: UUID, tranche_number: int) -> MilestoneTranche:
        """Release a specific tranche after its milestone has been verified.

        Tranche must be in LOCKED status and the milestone must not be
        KILLED. On release, a corresponding payout accrual is created.
        """
        milestone = await self.milestone_repo.get(milestone_id)
        if not milestone:
            raise BillingError(f"Milestone {milestone_id} not found")
        if milestone.status == MilestoneStatus.KILLED:
            raise MilestoneKillError("Cannot release tranches on a killed milestone")

        tranches = await self.milestone_repo.get_tranches(milestone_id)
        tranche = next((t for t in tranches if t.tranche_number == tranche_number), None)
        if not tranche:
            raise BillingError(f"Tranche {tranche_number} not found for milestone {milestone_id}")

        validate_transition(
            tranche.status, TrancheStatus.RELEASED, TRANCHE_TRANSITIONS, context="release_tranche"
        )
        tranche.status = TrancheStatus.RELEASED
        tranche.released_at = datetime.utcnow()

        # Accrue payout for the released tranche amount.
        idem_key = f"tranche:{milestone_id}:{tranche_number}"
        validate_currency("USD")  # Always USD for milestone payouts
        await self.payout_repo.accrue(
            creator_id=milestone.creator_id,
            amount=tranche.amount,
            currency="USD",
            idempotency_key=idem_key,
            cycle_start=milestone.created_at,
            cycle_end=datetime.utcnow(),
            breakdown={"type": "milestone_tranche", "tranche_number": tranche_number},
        )
        return tranche

    async def kill_milestone(self, milestone_id: UUID) -> Milestone:
        """Kill a milestone: revert all unreleased tranches to the Creator Pool.

        Already-released tranches are NOT clawed back — only LOCKED ones
        revert. The reverted funds become available for redistribution in
        the next pool cycle.
        """
        milestone = await self.milestone_repo.get(milestone_id)
        if not milestone:
            raise BillingError(f"Milestone {milestone_id} not found")

        milestone.status = MilestoneStatus.KILLED

        tranches = await self.milestone_repo.get_tranches(milestone_id)
        now = datetime.utcnow()
        for tranche in tranches:
            if tranche.status == TrancheStatus.LOCKED:
                tranche.status = TrancheStatus.REVERTED
                tranche.reverted_at = now

        return milestone

    # -----------------------------------------------------------------------
    # Payout Ledger
    # -----------------------------------------------------------------------

    async def get_payout_history(self, creator_id: UUID):
        """Get all payout ledger entries for a creator."""
        return await self.payout_repo.get_by_creator(creator_id)

    async def accrue_payout(
        self,
        creator_id: UUID,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        cycle_start: datetime,
        cycle_end: datetime,
        breakdown: dict | None = None,
    ) -> PayoutLedger:
        """Accrue a creator payout, idempotently.

        If a payout with the same idempotency_key already exists with
        the same amount, the existing entry is returned (safe retry).
        If it exists with a DIFFERENT amount, that's a conflict.
        Currency validated against ISO-4217 allowlist.
        """
        validate_currency(currency)
        existing = await self.payout_repo.get_by_idempotency_key(idempotency_key)
        if existing:
            if existing.amount != amount:
                raise DuplicatePayoutError(
                    f"Payout {idempotency_key} already exists with amount "
                    f"{existing.amount}, cannot re-accrue as {amount}"
                )
            return existing  # Idempotent retry — safe.

        return await self.payout_repo.accrue(
            creator_id=creator_id,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
            breakdown=breakdown,
        )

    # -----------------------------------------------------------------------
    # Creator share calculation
    # -----------------------------------------------------------------------

    @staticmethod
    def calculate_creator_share(svod_revenue: Decimal) -> Decimal:
        """Calculate the minimum creator share from SVOD revenue.

        The >=55% creator share is a contractual floor, not a cap.
        This method computes the floor; actual payouts may be higher
        when the Creator Pool top-up is applied.
        """
        return svod_revenue * CREATOR_SHARE_PERCENTAGE
