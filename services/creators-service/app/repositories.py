"""Creators service repositories."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreatorAccount,
    CreatorPoolBalance,
    EffectiveFloor,
    KYCStatus,
    Milestone,
    MilestoneStatus,
    MilestoneTranche,
    PayoutLedger,
    PayoutStatus,
    TrancheStatus,
)


class CreatorAccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user(self, user_id: UUID) -> CreatorAccount | None:
        stmt = select(CreatorAccount).where(CreatorAccount.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, creator_id: UUID) -> CreatorAccount | None:
        stmt = select(CreatorAccount).where(CreatorAccount.id == creator_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: UUID,
        display_name: str = "",
        bio: str = "",
        region_code: str = "US",
        currency: str = "USD",
    ) -> CreatorAccount:
        acct = CreatorAccount(
            user_id=user_id,
            display_name=display_name,
            bio=bio,
            region_code=region_code,
            currency=currency,
            kyc_status=KYCStatus.PENDING,
        )
        self.session.add(acct)
        await self.session.flush()
        return acct

    async def update(self, acct: CreatorAccount, **fields) -> CreatorAccount:
        for k, v in fields.items():
            if v is not None:
                setattr(acct, k, v)
        await self.session.flush()
        return acct


class EffectiveFloorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_floor_for_creator(self, creator_id: UUID) -> EffectiveFloor | None:
        """Return the current (latest effective_from) floor for a creator."""
        stmt = (
            select(EffectiveFloor)
            .where(EffectiveFloor.creator_id == creator_id)
            .order_by(EffectiveFloor.effective_from.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def set_floor(
        self,
        creator_id: UUID,
        per_minute_amount: float,
        currency: str = "USD",
        reason: str | None = None,
    ) -> EffectiveFloor:
        now = datetime.now(UTC)
        floor = EffectiveFloor(
            creator_id=creator_id,
            per_minute_amount=per_minute_amount,
            currency=currency,
            effective_from=now,
            last_adjusted_at=now,
            reason=reason,
        )
        self.session.add(floor)
        await self.session.flush()
        return floor


class CreatorPoolBalanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_for_creator(self, creator_id: UUID) -> CreatorPoolBalance | None:
        stmt = select(CreatorPoolBalance).where(CreatorPoolBalance.creator_id == creator_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(self, creator_id: UUID) -> CreatorPoolBalance:
        bal = await self.get_for_creator(creator_id)
        if bal is None:
            bal = CreatorPoolBalance(creator_id=creator_id)
            self.session.add(bal)
            await self.session.flush()
        return bal

    async def record_contribution(self, creator_id: UUID, cents: int) -> CreatorPoolBalance:
        bal = await self.get_or_create(creator_id)
        bal.contributed_cents += cents
        await self.session.flush()
        return bal

    async def accrue(self, creator_id: UUID, cents: int) -> CreatorPoolBalance:
        bal = await self.get_or_create(creator_id)
        bal.accrued_cents += cents
        await self.session.flush()
        return bal


class MilestoneRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, milestone_id: UUID) -> Milestone | None:
        stmt = select(Milestone).where(Milestone.id == milestone_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        title: str,
        creator_id: UUID,
        total_cents: int = 0,
        currency: str = "USD",
        goal: str | None = None,
    ) -> Milestone:
        ms = Milestone(
            title=title,
            creator_id=creator_id,
            total_cents=total_cents,
            currency=currency,
            goal=goal,
            status=MilestoneStatus.DRAFT,
        )
        self.session.add(ms)
        await self.session.flush()
        return ms

    async def add_tranche(
        self,
        milestone_id: UUID,
        threshold: int,
        amount_cents: int,
        release_condition: str | None = None,
    ) -> MilestoneTranche:
        tranche = MilestoneTranche(
            milestone_id=milestone_id,
            threshold=threshold,
            amount_cents=amount_cents,
            release_condition=release_condition,
            status=TrancheStatus.LOCKED,
        )
        self.session.add(tranche)
        await self.session.flush()
        return tranche

    async def release_tranche(self, milestone_id: UUID, threshold: int) -> MilestoneTranche | None:
        """Mark a single tranche released and stamp released_at."""
        stmt = select(MilestoneTranche).where(
            MilestoneTranche.milestone_id == milestone_id,
            MilestoneTranche.threshold == threshold,
        )
        result = await self.session.execute(stmt)
        tranche = result.scalar_one_or_none()
        if tranche is None:
            return None
        tranche.status = TrancheStatus.RELEASED
        tranche.released_at = datetime.now(UTC)
        await self.session.flush()
        return tranche

    async def kill_milestone(
        self, milestone_id: UUID, reason: str | None = None
    ) -> Milestone | None:
        """Kill a milestone: status=killed, and flip EVERY non-released tranche
        to rolled_back in ONE transaction. Released tranches stay released —
        that is the capital protection guarantee (PRODUCT_VISION §2.3).
        """
        stmt = select(Milestone).where(Milestone.id == milestone_id)
        result = await self.session.execute(stmt)
        ms = result.scalar_one_or_none()
        if ms is None:
            return None
        ms.status = MilestoneStatus.KILLED
        ms.kill_reason = reason

        # Roll back only unreleased tranches. Released ones are immutable.
        tranches_stmt = select(MilestoneTranche).where(
            MilestoneTranche.milestone_id == milestone_id,
            MilestoneTranche.status != TrancheStatus.RELEASED,
        )
        tranches_result = await self.session.execute(tranches_stmt)
        for t in tranches_result.scalars().all():
            t.status = TrancheStatus.ROLLED_BACK

        await self.session.flush()
        return ms


class DuplicatePayoutError(Exception):
    """Raised when a payout for the same idempotency_key already exists."""


class PayoutLedgerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_idempotency_key(self, key: str) -> PayoutLedger | None:
        stmt = select(PayoutLedger).where(PayoutLedger.idempotency_key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def accrued(
        self,
        creator_id: UUID,
        period_start: datetime,
        period_end: datetime,
        view_minutes: int,
        floor_cents: int,
        pool_topup_cents: int,
        share_cents: int,
        stripe_fee_cents: int,
        net_cents: int,
        idempotency_key: str,
    ) -> PayoutLedger:
        """Idempotent accrual. If a row with this idempotency_key already exists,
        return it unchanged so the same period never double-counts.

        Trade-off: we do a read-then-insert rather than relying purely on the
        unique constraint + catch, because the service layer needs to know
        whether THIS call created the row (to emit billing.payout.accrued) vs.
        observed an already-accrued period. The unique constraint remains the
        last line of defense against races.
        """
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        row = PayoutLedger(
            creator_id=creator_id,
            idempotency_key=idempotency_key,
            period_start=period_start,
            period_end=period_end,
            view_minutes=view_minutes,
            floor_cents=floor_cents,
            pool_topup_cents=pool_topup_cents,
            share_cents=share_cents,
            stripe_fee_cents=stripe_fee_cents,
            net_cents=net_cents,
            status=PayoutStatus.ACCRUED,
        )
        self.session.add(row)
        await self.session.flush()
        return row
