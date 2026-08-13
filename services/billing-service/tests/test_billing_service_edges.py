"""Edge-branch coverage for BillingService — errors, idempotency, tranches."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.models import MilestoneStatus, RevenueTier, SubscriptionStatus, TrancheStatus
from app.services import (
    BillingError,
    BillingService,
    DuplicatePayoutError,
    MilestoneKillError,
    TierInvalidError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service():
    return BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )


class TestSubscribe:
    async def test_invalid_tier_raises(self, service):
        with pytest.raises(TierInvalidError):
            await service.subscribe(uuid4(), "platinum")

    async def test_creates_when_no_existing(self, service):
        service.sub_repo.get_by_user.return_value = None

        await service.subscribe(uuid4(), "svod")

        service.sub_repo.create.assert_awaited_once()

    async def test_updates_when_existing(self, service):
        existing = MagicMock(status=SubscriptionStatus.ACTIVE)
        service.sub_repo.get_by_user.return_value = existing

        result = await service.subscribe(uuid4(), "svod")

        assert result is existing
        assert result.tier == RevenueTier.SVOD
        assert result.status == SubscriptionStatus.ACTIVE
        service.sub_repo.session.flush.assert_awaited_once()

    async def test_cancel_missing_returns_none(self, service):
        service.sub_repo.get_by_user.return_value = None

        assert await service.cancel_subscription(uuid4()) is None

    async def test_cancel_sets_avod_inactive(self, service):
        sub = MagicMock(status=SubscriptionStatus.ACTIVE)
        service.sub_repo.get_by_user.return_value = sub

        result = await service.cancel_subscription(uuid4())

        assert result.tier == RevenueTier.AVOD
        assert result.monthly_price == Decimal("0.00")
        assert result.is_active is False


class TestPurchase:
    async def test_duplicate_purchase_is_idempotent(self, service):
        user_id, content_id = uuid4(), uuid4()
        existing = MagicMock()
        service.purchase_repo.get_by_user_and_content.return_value = existing

        result = await service.purchase_title(user_id, content_id, Decimal("4.99"))

        assert result is existing
        service.purchase_repo.create.assert_not_awaited()

    async def test_new_purchase_creates_invoice(self, service):
        user_id, content_id = uuid4(), uuid4()
        purchase = MagicMock()
        purchase.id = uuid4()
        service.purchase_repo.get_by_user_and_content.return_value = None
        service.purchase_repo.create.return_value = purchase

        result = await service.purchase_title(user_id, content_id, Decimal("4.99"))

        assert result is purchase
        service.inv_repo.create.assert_awaited_once()


class TestAccruePayout:
    def _now(self):
        return datetime.now(UTC)

    async def test_new_payout_accrues(self, service):
        service.payout_repo.get_by_idempotency_key.return_value = None

        await service.accrue_payout(uuid4(), Decimal("5.00"), "USD", "k1", self._now(), self._now())

        service.payout_repo.accrue.assert_awaited_once()

    async def test_same_amount_is_idempotent(self, service):
        existing = MagicMock(amount=Decimal("5.00"))
        service.payout_repo.get_by_idempotency_key.return_value = existing

        result = await service.accrue_payout(
            uuid4(), Decimal("5.00"), "USD", "k1", self._now(), self._now()
        )

        assert result is existing
        service.payout_repo.accrue.assert_not_awaited()

    async def test_different_amount_conflicts(self, service):
        existing = MagicMock(amount=Decimal("1.00"))
        service.payout_repo.get_by_idempotency_key.return_value = existing

        with pytest.raises(DuplicatePayoutError):
            await service.accrue_payout(
                uuid4(), Decimal("5.00"), "USD", "k1", self._now(), self._now()
            )


class TestMilestone:
    async def test_release_tranche_missing_milestone(self, service):
        service.milestone_repo.get.return_value = None

        with pytest.raises(BillingError):
            await service.release_tranche(uuid4(), 1)

    async def test_release_tranche_killed_milestone(self, service):
        milestone = MagicMock(status=MilestoneStatus.KILLED)
        service.milestone_repo.get.return_value = milestone

        with pytest.raises(MilestoneKillError):
            await service.release_tranche(uuid4(), 1)

    async def test_release_tranche_missing_tranche(self, service):
        milestone = MagicMock(status=MilestoneStatus.PENDING)
        service.milestone_repo.get.return_value = milestone
        service.milestone_repo.get_tranches.return_value = [
            MagicMock(tranche_number=1, status=TrancheStatus.LOCKED)
        ]

        with pytest.raises(BillingError):
            await service.release_tranche(uuid4(), 2)

    async def test_release_tranche_not_locked(self, service):
        milestone = MagicMock(status=MilestoneStatus.PENDING)
        service.milestone_repo.get.return_value = milestone
        service.milestone_repo.get_tranches.return_value = [
            MagicMock(tranche_number=1, status=TrancheStatus.REVERTED)
        ]

        with pytest.raises(BillingError):
            await service.release_tranche(uuid4(), 1)

    async def test_release_tranche_success_accrues(self, service):
        milestone = MagicMock(
            status=MilestoneStatus.PENDING, creator_id=uuid4(), created_at=datetime.now(UTC)
        )
        tranche = MagicMock(
            tranche_number=1,
            status=TrancheStatus.LOCKED,
            amount=Decimal("100.00"),
        )
        service.milestone_repo.get.return_value = milestone
        service.milestone_repo.get_tranches.return_value = [tranche]

        result = await service.release_tranche(milestone.id, 1)

        assert result.status == TrancheStatus.RELEASED
        service.payout_repo.accrue.assert_awaited_once()

    async def test_kill_milestone_missing(self, service):
        service.milestone_repo.get.return_value = None

        with pytest.raises(BillingError):
            await service.kill_milestone(uuid4())

    async def test_kill_milestone_reverts_locked_only(self, service):
        milestone = MagicMock(status=MilestoneStatus.PENDING)
        locked = MagicMock(status=TrancheStatus.LOCKED)
        released = MagicMock(status=TrancheStatus.RELEASED)
        service.milestone_repo.get.return_value = milestone
        service.milestone_repo.get_tranches.return_value = [locked, released]

        result = await service.kill_milestone(uuid4())

        assert result.status == MilestoneStatus.KILLED
        assert locked.status == TrancheStatus.REVERTED
        assert released.status == TrancheStatus.RELEASED


class TestCreatorShare:
    def test_floor_is_55_percent(self):
        assert BillingService.calculate_creator_share(Decimal("100.00")) == Decimal("55.00")

    def test_floor_zero(self):
        assert BillingService.calculate_creator_share(Decimal("0")) == Decimal("0.00")


class TestMisc:
    async def test_get_pool_status(self, service):
        service.pool_repo.get_latest.return_value = MagicMock()

        assert await service.get_pool_status() is not None

    async def test_accrue_pool_delegates(self, service):
        now = datetime.now(UTC)
        entry = MagicMock()
        service.pool_repo.create_entry.return_value = entry

        result = await service.accrue_pool(now - timedelta(days=30), now, Decimal("1000"))

        assert result is entry
        service.pool_repo.create_entry.assert_awaited_once()

    async def test_get_floor(self, service):
        service.floor_repo.get_by_region.return_value = MagicMock()

        assert await service.get_floor("US") is not None

    async def test_get_payout_history(self, service):
        service.payout_repo.get_by_creator.return_value = [MagicMock()]

        assert len(await service.get_payout_history(uuid4())) == 1


class TestAccruePayoutIntegrity:
    async def test_unique_violation_returns_existing(self):
        from datetime import UTC, datetime

        from sqlalchemy.exc import IntegrityError

        from app.repositories import PayoutLedgerRepository

        session = AsyncMock()
        session.flush.side_effect = IntegrityError("stmt", {}, Exception("dup"))
        repo = PayoutLedgerRepository(session)
        existing = MagicMock()
        # First lookup (pre-insert) -> None; post-rollback lookup -> existing.
        session.execute.side_effect = [
            MagicMock(scalar_one_or_none=lambda: None),
            MagicMock(scalar_one_or_none=lambda: existing),
        ]
        now = datetime.now(UTC)

        result = await repo.accrue(uuid4(), Decimal("5.00"), "USD", "k", now, now)

        assert result is existing
        session.rollback.assert_awaited_once()

    async def test_acrues_when_new(self):
        from datetime import UTC, datetime

        from app.repositories import PayoutLedgerRepository

        session = AsyncMock()
        repo = PayoutLedgerRepository(session)
        session.execute.return_value = MagicMock(scalar_one_or_none=lambda: None)
        now = datetime.now(UTC)

        await repo.accrue(uuid4(), Decimal("5.00"), "USD", "k", now, now)

        session.add.assert_called_once()
        session.flush.assert_awaited_once()
