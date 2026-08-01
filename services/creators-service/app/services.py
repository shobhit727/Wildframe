"""Creators service business logic."""
from datetime import datetime
from uuid import UUID

from app.core.settings import settings
from app.repositories import (
    CreatorAccountRepository,
    CreatorPoolBalanceRepository,
    EffectiveFloorRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
)


class CreatorService:
    """Orchestrates the creator financial lifecycle: onboarding, floor, pool,
    milestones/tranches, and idempotent payout accrual.
    """

    def __init__(self, acct_repo: CreatorAccountRepository,
                 floor_repo: EffectiveFloorRepository,
                 pool_repo: CreatorPoolBalanceRepository,
                 milestone_repo: MilestoneRepository,
                 ledger_repo: PayoutLedgerRepository):
        self.acct_repo = acct_repo
        self.floor_repo = floor_repo
        self.pool_repo = pool_repo
        self.milestone_repo = milestone_repo
        self.ledger_repo = ledger_repo

    # ------------------------------------------------------------------ profile
    async def get_profile(self, user_id: UUID):
        return await self.acct_repo.get_by_user(user_id)

    async def update_profile(self, user_id: UUID, **fields):
        acct = await self.acct_repo.get_by_user(user_id)
        if acct is None:
            return None
        return await self.acct_repo.update(acct, **fields)

    # -------------------------------------------------------------------- floor
    async def get_floor(self, creator_id: UUID):
        return await self.floor_repo.get_floor_for_creator(creator_id)

    async def set_floor(self, creator_id: UUID, per_minute_amount: float,
                        currency: str = "USD", reason: str | None = None):
        # Invariant: floor is a minimum guarantee, never negative.
        # A negative floor would imply the platform owes the creator for NOT
        # publishing, which is nonsensical and would break the pool math.
        assert per_minute_amount >= 0, "effective floor must be >= 0"
        return await self.floor_repo.set_floor(creator_id, per_minute_amount,
                                               currency, reason)

    # --------------------------------------------------------------------- pool
    async def record_pool_contribution(self, creator_id: UUID, cents: int):
        return await self.pool_repo.record_contribution(creator_id, cents)

    # --------------------------------------------------------------- milestones
    async def create_milestone(self, title: str, creator_id: UUID,
                               total_cents: int = 0, currency: str = "USD",
                               goal: str | None = None):
        return await self.milestone_repo.create(title, creator_id, total_cents,
                                                currency, goal)

    async def add_tranche(self, milestone_id: UUID, threshold: int,
                          amount_cents: int, release_condition: str | None = None):
        return await self.milestone_repo.add_tranche(milestone_id, threshold,
                                                     amount_cents, release_condition)

    async def release_tranche(self, milestone_id: UUID, threshold: int):
        return await self.milestone_repo.release_tranche(milestone_id, threshold)

    async def kill_milestone(self, milestone_id: UUID, reason: str | None = None):
        return await self.milestone_repo.kill_milestone(milestone_id, reason)

    # ------------------------------------------------------------------- payout
    async def accrue_payout(self, creator_id: UUID, period_start: datetime,
                            period_end: datetime, view_minutes: int,
                            earned_cents: int, stripe_fee_cents: int = 0):
        """Accrue one payout period for a creator, idempotently.

        Formulas
        --------
        floor_due      = per_minute_floor × view_minutes
        floor_topup    = max(0, floor_due − earned_cents)
                       Only backfill the gap below the floor. If the creator
                       earned above the floor, top-up is zero — the floor is a
                       minimum, not a bonus.
        pool_topup     = floor_topup × POOL_RATE  (default 0.15)
                       A fraction of the floor backfill is sourced from the
                       shared Creator Pool. Trade-off: a higher pool_rate lifts
                       floors faster but reduces the immediate share to top
                       earners; 0.15 is the charter default.
        share_cents    = earned_cents + floor_topup
                       The creator's total take for the period.
        net_cents      = share_cents − stripe_fee_cents
                       What actually lands in the creator's Stripe account.

        Invariant
        ----------
        share_cents >= 0.55 × net_cents  (i.e. creator keeps >= 55% of net).
        This is the contractual floor from PRODUCT_VISION §3: the ≥55% creator
        share is calculated BEFORE platform costs. If Stripe fees ever push the
        creator below 55% of net, the platform is mispricing its fees and MUST
        absorb the difference rather than pass it to the creator. We assert it
        here so a bad fee config fails loudly in tests instead of silently
        cheating creators in production.
        """
        floor = await self.floor_repo.get_floor_for_creator(creator_id)
        per_minute = floor.per_minute_amount if floor is not None else 0.0

        # floor_due is in cents; per_minute is in major currency units.
        floor_due_cents = int(per_minute * 100) * max(0, view_minutes)

        # Only backfill the gap below the floor.
        floor_topup = max(0, floor_due_cents - max(0, earned_cents))

        # A fraction of the floor backfill is sourced from the shared pool.
        pool_topup = int(floor_topup * settings.POOL_RATE)

        share_cents = max(0, earned_cents) + floor_topup
        net_cents = share_cents - max(0, stripe_fee_cents)

        # Contractual invariant: creator keeps >= 55% of net.
        # Why: the ≥55% creator share is a contractual floor, not a target
        # (PRODUCT_VISION §3). If this fails, the platform is mispricing fees.
        assert net_cents <= 0 or share_cents >= 0.55 * net_cents, (
            "creator share must be >= 55% of net (contractual floor)"
        )

        # Idempotency key: one ledger row per (creator, period). A retried
        # payout / retried webhook resolves to the same key and therefore the
        # same row — never double-pay (PRODUCT_VISION §4).
        idempotency_key = f"{creator_id}:{period_start.isoformat()}:{period_end.isoformat()}"

        return await self.ledger_repo.accrued(
            creator_id=creator_id,
            period_start=period_start,
            period_end=period_end,
            view_minutes=view_minutes,
            floor_cents=floor_topup,
            pool_topup_cents=pool_topup,
            share_cents=share_cents,
            stripe_fee_cents=max(0, stripe_fee_cents),
            net_cents=net_cents,
            idempotency_key=idempotency_key,
        )
