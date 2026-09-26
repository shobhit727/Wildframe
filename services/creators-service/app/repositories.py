"""Creators service repositories."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreatorAccount,
    CreatorPoolBalance,
    CreatorSuspendedError,
    EffectiveFloor,
    InboundEvent,
    InboundEventStatus,
    KYCStatus,
    Milestone,
    MilestoneStatus,
    MilestoneTranche,
    PayoutLedger,
    PayoutStatus,
    TrancheStatus,
)


def utc_naive(value: datetime) -> datetime:
    """Interpret naive values as UTC, normalize aware values before persistence."""
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


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
        await self.session.commit()
        return acct

    async def update(self, acct: CreatorAccount, **fields) -> CreatorAccount:
        for k, v in fields.items():
            if v is not None:
                setattr(acct, k, v)
        await self.session.flush()
        await self.session.commit()
        return acct


class EffectiveFloorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_floor_for_creator(self, creator_id: UUID) -> EffectiveFloor | None:
        """Return the current (latest effective_from) floor for a creator."""
        stmt = (
            select(EffectiveFloor)
            .where(EffectiveFloor.creator_id == creator_id)
            .order_by(EffectiveFloor.effective_from.desc(), EffectiveFloor.id.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def set_floor(
        self,
        creator_id: UUID,
        per_minute_amount: Decimal,
        currency: str = "USD",
        reason: str | None = None,
    ) -> EffectiveFloor:
        now = datetime.now(UTC).replace(tzinfo=None)
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
        await self.session.commit()
        return floor


class CreatorPoolBalanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_for_creator(self, creator_id: UUID) -> CreatorPoolBalance | None:
        stmt = select(CreatorPoolBalance).where(CreatorPoolBalance.creator_id == creator_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(self, creator_id: UUID) -> CreatorPoolBalance:
        await self.session.execute(
            insert(CreatorPoolBalance)
            .values(creator_id=creator_id)
            .on_conflict_do_nothing(index_elements=[CreatorPoolBalance.creator_id])
        )
        await self.session.commit()
        bal = await self.get_for_creator(creator_id)
        if bal is None:  # pragma: no cover - the insert above guarantees a row
            raise RuntimeError(f"pool balance missing for creator {creator_id}")
        return bal

    async def record_contribution(self, creator_id: UUID, cents: int) -> CreatorPoolBalance:
        if cents < 0:
            raise ValueError("contribution must be nonnegative")
        stmt = insert(CreatorPoolBalance).values(creator_id=creator_id, contributed_cents=cents)
        result = await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CreatorPoolBalance.creator_id],
                set_={"contributed_cents": CreatorPoolBalance.contributed_cents + cents},
            )
            .returning(CreatorPoolBalance)
            .execution_options(populate_existing=True)
        )
        bal = result.scalar_one()
        await self.session.commit()
        return bal

    async def accrue(self, creator_id: UUID, cents: int) -> None:
        """Increment within the caller's ledger transaction; never commit separately."""
        if cents < 0:
            raise ValueError("accrual must be nonnegative")
        stmt = insert(CreatorPoolBalance).values(creator_id=creator_id, accrued_cents=cents)
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CreatorPoolBalance.creator_id],
                set_={"accrued_cents": CreatorPoolBalance.accrued_cents + cents},
            )
        )


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
        await self.session.commit()
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
        await self.session.commit()
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
        tranche.released_at = datetime.now(UTC).replace(tzinfo=None)
        await self.session.flush()
        await self.session.commit()
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
        await self.session.commit()
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
        """Insert a period once; credit its pool balance in the same transaction."""
        period_start, period_end = utc_naive(period_start), utc_naive(period_end)
        if period_end <= period_start:
            raise ValueError("period_end must follow period_start")
        if (
            min(
                view_minutes,
                floor_cents,
                pool_topup_cents,
                share_cents,
                stripe_fee_cents,
                net_cents,
            )
            < 0
        ):
            raise ValueError("payout amounts must be nonnegative")
        try:
            result = await self.session.execute(
                select(CreatorAccount)
                .where(CreatorAccount.id == creator_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            creator = result.scalar_one_or_none()
            if creator is None or not creator.is_active:
                raise CreatorSuspendedError(f"Creator {creator_id} is suspended or does not exist")
            insert_result = await self.session.execute(
                insert(PayoutLedger)
                .values(
                    creator_id=creator_id,
                    idempotency_key=f"{creator_id}:{period_start.isoformat()}:{period_end.isoformat()}",
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
                .on_conflict_do_nothing()
                .returning(PayoutLedger)
            )
            row = insert_result.scalar_one_or_none()
            if row is None:
                existing_result = await self.session.execute(
                    select(PayoutLedger).where(
                        PayoutLedger.creator_id == creator_id,
                        PayoutLedger.period_start == period_start,
                        PayoutLedger.period_end == period_end,
                    )
                )
                row = existing_result.scalar_one()
            else:
                await CreatorPoolBalanceRepository(self.session).accrue(
                    creator_id, pool_topup_cents
                )
            await self.session.commit()
            return row
        except BaseException:
            await self.session.rollback()
            raise


class InboundEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_event_key(self, event_key: str) -> InboundEvent | None:
        stmt = select(InboundEvent).where(InboundEvent.event_key == event_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_pending(self, topic: str, event_key: str, payload: dict) -> InboundEvent:
        """Create a pending inbound event. Returns existing if event_key already exists."""
        existing = await self.get_by_event_key(event_key)
        if existing is not None:
            return existing
        row = InboundEvent(topic=topic, event_key=event_key, payload=payload)
        self.session.add(row)
        await self.session.flush()
        return row

    async def mark_processed(self, event_id: UUID) -> None:
        row = await self.session.get(InboundEvent, event_id)
        if row is not None:
            row.status = InboundEventStatus.PROCESSED
            row.processed_at = datetime.now(UTC).replace(tzinfo=None)
            await self.session.flush()

    async def mark_failed(self, event_id: UUID) -> None:
        row = await self.session.get(InboundEvent, event_id)
        if row is not None:
            row.status = InboundEventStatus.FAILED
            await self.session.flush()

    async def get_pending(self, limit: int = 100) -> list[InboundEvent]:
        stmt = (
            select(InboundEvent)
            .where(InboundEvent.status == InboundEventStatus.PENDING)
            .order_by(InboundEvent.created_at.asc(), InboundEvent.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
