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
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.models import (
    CreatorPoolEntry,
    Milestone,
    MilestoneStatus,
    MilestoneTranche,
    PayoutLedger,
    Purchase,
    RevenueTier,
    Subscription,
    TrancheStatus,
)
from app.repositories import (
    CreatorPoolRepository,
    InvoiceRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
    PurchaseRepository,
    RegionFloorRepository,
    SubscriptionRepository,
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
    ):
        self.sub_repo = sub_repo
        self.purchase_repo = purchase_repo
        self.inv_repo = inv_repo
        self.floor_repo = floor_repo
        self.pool_repo = pool_repo
        self.milestone_repo = milestone_repo
        self.payout_repo = payout_repo

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
        """
        try:
            tier = RevenueTier(tier_str.lower())
        except ValueError:
            raise TierInvalidError(f"Invalid tier '{tier_str}'. Must be one of: avod, svod, tvod")

        price = TIER_PRICES[tier]
        existing = await self.sub_repo.get_by_user(user_id)
        if existing:
            return await self.sub_repo.update_tier(user_id, tier, price)
        return await self.sub_repo.create(user_id, tier, price)

    async def cancel_subscription(self, user_id: UUID) -> Subscription | None:
        """Cancel a subscription (reverts to AVOD)."""
        sub = await self.sub_repo.get_by_user(user_id)
        if not sub:
            return None
        sub.tier = RevenueTier.AVOD
        sub.monthly_price = Decimal("0.00")
        sub.cancelled_at = datetime.now(UTC)
        sub.is_active = False
        return sub

    # -----------------------------------------------------------------------
    # TVOD purchases
    # -----------------------------------------------------------------------

    async def purchase_title(
        self,
        user_id: UUID,
        content_id: UUID,
        price: Decimal,
    ) -> Purchase:
        """Record a one-off TVOD purchase (pay-per-view).

        Uses a deterministic idempotency key derived from user+content
        so duplicate requests are safe.
        """
        idem_key = f"tvod:{user_id}:{content_id}"
        existing = await self.purchase_repo.get_by_user_and_content(user_id, content_id)
        if existing:
            return existing

        purchase = await self.purchase_repo.create(user_id, content_id, price, idem_key)
        # Also create an invoice for the purchase.
        await self.inv_repo.create(
            user_id=user_id,
            amount=price,
            purchase_id=purchase.id,
        )
        return purchase

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
        if tranche.status != TrancheStatus.LOCKED:
            raise BillingError(
                f"Tranche {tranche_number} is not locked (status={tranche.status.value})"
            )

        tranche.status = TrancheStatus.RELEASED
        tranche.released_at = datetime.now(UTC)

        # Accrue payout for the released tranche amount.
        idem_key = f"tranche:{milestone_id}:{tranche_number}"
        await self.payout_repo.accrue(
            creator_id=milestone.creator_id,
            amount=tranche.amount,
            currency="USD",
            idempotency_key=idem_key,
            cycle_start=milestone.created_at,
            cycle_end=datetime.now(UTC),
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
        now = datetime.now(UTC)
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
        """
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
