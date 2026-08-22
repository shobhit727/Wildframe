"""Billing service repositories.

One repository per aggregate root. All database access goes through these
classes — the service layer never touches the session directly for queries.

Financial invariants (#191/#220/#428):
  - State-changing writes use guarded/conditional UPDATEs or unique
    constraints (equivalent transactional strategy at READ COMMITTED) so
    concurrent requests cannot double-apply or regress state.
  - Financial records (purchases, invoices, payouts, refunds) are
    append-only: no repository exposes a delete operation.

Deadlock retry (#631): financial UPDATEs are retried with exponential backoff
on PostgreSQL deadlock (40P01) and lock_not_available (55P03) errors.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncio
from sqlalchemy import text, and_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreatorPoolEntry,
    Invoice,
    InvoiceStatus,
    Milestone,
    MilestoneTranche,
    PayoutLedger,
    Purchase,
    Refund,
    RefundStatus,
    RegionFloor,
    RevenueTier,
    StripeWebhookEvent,
    Subscription,
    WebhookEventStatus,
)

# ---------------------------------------------------------------------------
# Deadlock retry helper (#631)
# ---------------------------------------------------------------------------

_DEADLOCK_CODES = {"40P01", "55P03"}  # deadlock_detected, lock_not_available


async def _execute_with_deadlock_retry(
    session: AsyncSession,
    stmt,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.05,
    max_delay: float = 0.5,
):
    """Execute a statement with exponential backoff on deadlock/lock errors.

    Retries on PostgreSQL error codes:
      - 40P01: deadlock_detected
      - 55P03: lock_not_available (could not obtain lock within timeout)

    Also enforces a per-statement timeout via SET LOCAL statement_timeout
    to cap transaction duration per #631.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            # Cap statement execution time (ms). Adjust based on workload.
            await session.execute(text("SET LOCAL statement_timeout = '10s'"))
            result = await session.execute(stmt)
            return result
        except OperationalError as exc:
            code = getattr(exc.orig, "pgcode", None)
            if code in _DEADLOCK_CODES and attempt < max_attempts:
                last_exc = exc
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                await asyncio.sleep(delay)
                await session.rollback()  # reset transaction state
                continue
            raise
        except BaseException as exc:
            last_exc = exc
            raise
    # Should not reach here (re-raised above), but for type safety:
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unexpected state in _execute_with_deadlock_retry")


class WebhookEventRepository:
    """Durable inbox for Stripe webhook events (#47).

    ``claim`` is the atomic arbitration point: concurrent deliveries and
    post-restart replays all INSERT the same unique ``event_id`` and the
    unique constraint lets exactly one win. A FAILED row (or a PROCESSING
    row whose lease is stale) is reclaimed through a guarded UPDATE so
    two retries cannot both take it over.
    """

    DEFAULT_STALE_AFTER_SECONDS = 60
    DEFAULT_MAX_ATTEMPTS = 3

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, event_id: str) -> StripeWebhookEvent | None:
        stmt = select(StripeWebhookEvent).where(StripeWebhookEvent.event_id == event_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def claim(
        self,
        event_id: str,
        event_type: str,
        *,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> bool:
        """Atomically claim the event for this request.

        Returns True when this request wins the claim and may run side
        effects, False when the event is already claimed/processed.
        """
        row = StripeWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            status=WebhookEventStatus.PROCESSING,
            attempts=1,
            claimed_at=datetime.utcnow(),
        )
        self.session.add(row)
        try:
            await self.session.flush()
            return True
        except IntegrityError:
            # Lost the insert race (or a replay after a crash). The unique
            # constraint guarantees one row per event_id.
            await self.session.rollback()
            existing = await self.get(event_id)
            if existing is None:
                return False
            if existing.status == WebhookEventStatus.PROCESSED:
                return False

            now = datetime.utcnow()
            reclaimable = False
            if existing.status == WebhookEventStatus.FAILED and existing.attempts < max_attempts:
                reclaimable = True
            elif existing.status == WebhookEventStatus.PROCESSING and (
                existing.claimed_at is None
                or (now - existing.claimed_at).total_seconds() >= stale_after_seconds
            ):
                reclaimable = True
            if not reclaimable:
                return False

            # Guarded reclaim: only one concurrent retry can match the
            # current status/attempts, so the row cannot be double-taken.
            stmt = (
                update(StripeWebhookEvent)
                .where(
                    StripeWebhookEvent.event_id == event_id,
                    StripeWebhookEvent.status == existing.status,
                    StripeWebhookEvent.attempts == existing.attempts,
                )
                .values(
                    status=WebhookEventStatus.PROCESSING,
                    attempts=existing.attempts + 1,
                    claimed_at=now,
                    last_error=None,
                )
            )
            result = await self.session.execute(stmt)
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def complete(self, event_id: str) -> bool:
        """Mark a PROCESSING event as PROCESSED (guarded; no-op if already done)."""
        stmt = (
            update(StripeWebhookEvent)
            .where(
                StripeWebhookEvent.event_id == event_id,
                StripeWebhookEvent.status == WebhookEventStatus.PROCESSING,
            )
            .values(status=WebhookEventStatus.PROCESSED, processed_at=datetime.utcnow())
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def fail(self, event_id: str, error: str) -> bool:
        """Mark a PROCESSING event as FAILED with the error, for bounded retry."""
        stmt = (
            update(StripeWebhookEvent)
            .where(
                StripeWebhookEvent.event_id == event_id,
                StripeWebhookEvent.status == WebhookEventStatus.PROCESSING,
            )
            .values(status=WebhookEventStatus.FAILED, last_error=error[:500])
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def commit(self) -> None:
        """Commit the inbox transaction so a claim survives handler failure."""
        await self.session.commit()


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
        self,
        user_id: UUID,
        content_id: UUID,
        price: Decimal,
        idempotency_key: str,
        currency: str = "USD",
        stripe_payment_intent_id: str | None = None,
    ) -> Purchase:
        purchase = Purchase(
            user_id=user_id,
            content_id=content_id,
            price=price,
            currency=currency,
            idempotency_key=idempotency_key,
            stripe_payment_intent_id=stripe_payment_intent_id,
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
        currency: str = "USD",
        stripe_invoice_id: str | None = None,
    ) -> Invoice:
        inv = Invoice(
            subscription_id=subscription_id,
            purchase_id=purchase_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            stripe_invoice_id=stripe_invoice_id,
        )
        self.session.add(inv)
        await self.session.flush()
        return inv

    async def get(self, invoice_id: UUID) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.id == invoice_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_stripe_invoice_id(self, stripe_invoice_id: str) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_for_user(self, user_id: UUID) -> Invoice | None:
        """Newest invoice for a user (refund target lookup)."""
        stmt = (
            select(Invoice)
            .where(Invoice.user_id == user_id)
            .order_by(Invoice.issued_at.desc(), Invoice.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user(self, user_id: UUID) -> list[Invoice]:
        stmt = (
            select(Invoice)
            .where(Invoice.user_id == user_id)
            .order_by(Invoice.issued_at.desc(), Invoice.id.desc())
        )
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

    async def redistribute_pool(self, entry_id: UUID, delta: Decimal) -> bool:
        """Atomically increment redistributed_amount, bounded by pool_amount.

        Uses a guarded UPDATE to prevent read-modify-write race conditions
        on the Creator Pool balance (#254).
        """
        from sqlalchemy import update

        stmt = (
            update(CreatorPoolEntry)
            .where(
                CreatorPoolEntry.id == entry_id,
                CreatorPoolEntry.redistributed_amount + delta <= CreatorPoolEntry.pool_amount,
            )
            .values(redistributed_amount=CreatorPoolEntry.redistributed_amount + delta)
        )
        result = await _execute_with_deadlock_retry(self.session, stmt)
        return bool(result.rowcount)


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
            .order_by(PayoutLedger.cycle_end.desc(), PayoutLedger.id.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class RefundRepository:
    """CRUD for ``Refund`` records (#191/#478).

    Refunds are append-only: ``create`` is idempotent on ``refund_id``
    (the Stripe refund id). The Stripe webhook can therefore be replayed
    safely. ``apply_to_invoice`` performs the only refund-bounds mutation
    (incrementing ``Invoice.refunded_amount``) under a guarded UPDATE so
    concurrent refunds cannot exceed ``Invoice.amount``.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_refund_id(self, refund_id: str) -> Refund | None:
        stmt = select(Refund).where(Refund.refund_id == refund_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        refund_id: str,
        amount: Decimal,
        currency: str,
        *,
        charge_id: str | None = None,
        invoice_id: UUID | None = None,
        user_id: UUID | None = None,
        reason: str | None = None,
        status: RefundStatus = RefundStatus.PROCESSED,
    ) -> Refund:
        """Insert idempotently on ``refund_id``; return existing on duplicate."""
        existing = await self.get_by_refund_id(refund_id)
        if existing:
            return existing
        refund = Refund(
            refund_id=refund_id,
            charge_id=charge_id,
            invoice_id=invoice_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            reason=reason,
            status=status,
        )
        self.session.add(refund)
        try:
            await self.session.flush()
            return refund
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_refund_id(refund_id)
            if existing:
                return existing
            raise

    async def apply_to_invoice(self, invoice_id: UUID, amount: Decimal) -> bool:
        """Increment ``refunded_amount`` atomically, bounded by ``amount``.

        Returns True when the guard matched (refund applied) and False
        when the bounds would be exceeded (caller must record a REJECTED
        refund instead of raising — money has already left Stripe).
        """
        stmt = (
            update(Invoice)
            .where(
                Invoice.id == invoice_id,
                Invoice.refunded_amount + amount <= Invoice.amount,
            )
            .values(
                refunded_amount=Invoice.refunded_amount + amount,
                status=InvoiceStatus.REFUNDED,
            )
        )
        result = await _execute_with_deadlock_retry(self.session, stmt)
        return bool(result.rowcount)
