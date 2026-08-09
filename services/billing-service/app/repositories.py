"""Billing service repositories.

One repository per aggregate root. All database access goes through these
classes — the service layer never touches the session directly for queries.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreatorPoolEntry,
    Invoice,
    Milestone,
    MilestoneTranche,
    PayoutLedger,
    Purchase,
    RegionFloor,
    RevenueTier,
    Subscription,
)


class SubscriptionRepository:
    """CRUD for Subscription aggregate."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user(self, user_id: UUID) -> Subscription | None:
        stmt = select(Subscription).where(Subscription.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self, user_id: UUID, tier: RevenueTier, monthly_price: Decimal
    ) -> Subscription:
        sub = Subscription(user_id=user_id, tier=tier, monthly_price=monthly_price)
        self.session.add(sub)
        await self.session.flush()
        return sub

    async def update_tier(
        self, user_id: UUID, tier: RevenueTier, monthly_price: Decimal
    ) -> Subscription | None:
        sub = await self.get_by_user(user_id)
        if sub:
            sub.tier = tier
            sub.monthly_price = monthly_price
            await self.session.flush()
        return sub


class PurchaseRepository:
    """CRUD for TVOD purchase records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_and_content(self, user_id: UUID, content_id: UUID) -> Purchase | None:
        stmt = select(Purchase).where(
            and_(Purchase.user_id == user_id, Purchase.content_id == content_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self, user_id: UUID, content_id: UUID, price: Decimal, idempotency_key: str
    ) -> Purchase:
        purchase = Purchase(
            user_id=user_id,
            content_id=content_id,
            price=price,
            idempotency_key=idempotency_key,
        )
        self.session.add(purchase)
        await self.session.flush()
        return purchase


class InvoiceRepository:
    """CRUD for Invoice records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        amount: Decimal,
        subscription_id: UUID | None = None,
        purchase_id: UUID | None = None,
    ) -> Invoice:
        inv = Invoice(
            subscription_id=subscription_id,
            purchase_id=purchase_id,
            user_id=user_id,
            amount=amount,
        )
        self.session.add(inv)
        await self.session.flush()
        return inv

    async def get_by_user(self, user_id: UUID) -> list[Invoice]:
        stmt = select(Invoice).where(Invoice.user_id == user_id).order_by(Invoice.issued_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class RegionFloorRepository:
    """CRUD for RegionFloor (living-wage floor rates)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_region(self, region_code: str) -> RegionFloor | None:
        stmt = select(RegionFloor).where(RegionFloor.region_code == region_code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[RegionFloor]:
        stmt = select(RegionFloor).order_by(RegionFloor.region_code)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class CreatorPoolRepository:
    """CRUD for CreatorPoolEntry + distributions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_latest(self) -> CreatorPoolEntry | None:
        stmt = select(CreatorPoolEntry).order_by(CreatorPoolEntry.cycle_end.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_entry(
        self, cycle_start, cycle_end, net_revenue: Decimal, pool_percentage: Decimal
    ) -> CreatorPoolEntry:
        entry = CreatorPoolEntry(
            cycle_start=cycle_start,
            cycle_end=cycle_end,
            net_revenue=net_revenue,
            pool_percentage=pool_percentage,
            pool_amount=net_revenue * pool_percentage,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry


class MilestoneRepository:
    """CRUD for Milestone aggregate + tranches."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, milestone_id: UUID) -> Milestone | None:
        stmt = select(Milestone).where(Milestone.id == milestone_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self, creator_id: UUID, project_title: str, total_commitment: Decimal
    ) -> Milestone:
        ms = Milestone(
            creator_id=creator_id,
            project_title=project_title,
            total_commitment=total_commitment,
        )
        self.session.add(ms)
        await self.session.flush()
        # Auto-create the 4 tranches (10/20/30/40)
        percentages = [Decimal("10.00"), Decimal("20.00"), Decimal("30.00"), Decimal("40.00")]
        for i, pct in enumerate(percentages, start=1):
            tranche = MilestoneTranche(
                milestone_id=ms.id,
                tranche_number=i,
                percentage=pct,
                amount=total_commitment * pct / Decimal("100.00"),
            )
            self.session.add(tranche)
        await self.session.flush()
        return ms

    async def get_tranches(self, milestone_id: UUID) -> list[MilestoneTranche]:
        stmt = (
            select(MilestoneTranche)
            .where(MilestoneTranche.milestone_id == milestone_id)
            .order_by(MilestoneTranche.tranche_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PayoutLedgerRepository:
    """CRUD for the PayoutLedger (creator payout records).

    Idempotency: every write keys on idempotency_key. Attempting to
    create a duplicate silently returns the existing record.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_idempotency_key(self, key: str) -> PayoutLedger | None:
        stmt = select(PayoutLedger).where(PayoutLedger.idempotency_key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def accrue(
        self,
        creator_id: UUID,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        cycle_start,
        cycle_end,
        breakdown: dict | None = None,
    ) -> PayoutLedger:
        """Create an accrued payout entry, idempotently."""
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing
        entry = PayoutLedger(
            creator_id=creator_id,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
            breakdown=breakdown,
        )
        self.session.add(entry)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent replay lost the race: the unique constraint guarantees
            # one row per idempotency_key. Refresh in this transaction.
            await self.session.rollback()
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing:
                return existing
            raise
        return entry

    async def get_by_creator(self, creator_id: UUID) -> list[PayoutLedger]:
        stmt = (
            select(PayoutLedger)
            .where(PayoutLedger.creator_id == creator_id)
            .order_by(PayoutLedger.cycle_end.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
