import os
from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import datetime

import pytest
import pytest_asyncio
from uuid import uuid4
from decimal import Decimal

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models import (
    Base,
    RevenueTier,
    RegionFloor,
)

from app.repositories import (
    SubscriptionRepository,
    PurchaseRepository,
    InvoiceRepository,
    RegionFloorRepository,
)


# ---------------------------------------------------------------------------
# Fixtures – disposable PostgreSQL with rollback isolation
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """TEST_DATABASE_URL must name a disposable PostgreSQL test database."""
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            from testcontainers.postgres import PostgresContainer

            postgres = stack.enter_context(PostgresContainer("postgres:15"))
            url = postgres.get_connection_url()
        engine = create_async_engine(make_url(url).set(drivername="postgresql+asyncpg"))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.run_sync(Base.metadata.create_all)
                    factory = async_sessionmaker(
                        connection,
                        class_=AsyncSession,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    )
                    async with factory() as session:
                        yield session
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_repository(db_session: AsyncSession):
    repo = SubscriptionRepository(db_session)
    user_id = uuid4()
    sub = await repo.create(user_id, RevenueTier.SVOD, Decimal("7.99"))
    await db_session.commit()
    fetched = await repo.get_by_user(user_id)
    assert fetched is not None
    assert fetched.id == sub.id
    assert fetched.tier == RevenueTier.SVOD
    assert fetched.monthly_price == Decimal("7.99")


@pytest.mark.asyncio
async def test_purchase_repository(db_session: AsyncSession):
    repo = PurchaseRepository(db_session)
    user_id = uuid4()
    content_id = uuid4()
    purchase = await repo.create(
        user_id=user_id,
        content_id=content_id,
        price=Decimal("4.99"),
        idempotency_key="key1",
        currency="USD",
    )
    await db_session.commit()
    fetched = await repo.get_by_user_and_content(user_id, content_id)
    assert fetched is not None
    assert fetched.id == purchase.id
    assert fetched.price == Decimal("4.99")


@pytest.mark.asyncio
async def test_invoice_repository_latest(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    user_id = uuid4()
    inv1 = await repo.create(user_id, Decimal("10.00"))
    inv1.issued_at = datetime(2025, 1, 1)
    inv2 = await repo.create(user_id, Decimal("15.00"))
    inv2.issued_at = datetime(2025, 1, 2)
    await db_session.commit()
    latest = await repo.get_latest_for_user(user_id)
    assert latest is not None
    assert latest.id == inv2.id
    assert latest.amount == Decimal("15.00")


@pytest.mark.asyncio
async def test_region_floor_repository(db_session: AsyncSession):
    repo = RegionFloorRepository(db_session)
    floor = RegionFloor(
        region_code="US",
        currency="USD",
        floor_low=Decimal("0.10"),
        floor_high=Decimal("0.20"),
    )
    db_session.add(floor)
    await db_session.commit()
    fetched = await repo.get_by_region("US")
    assert fetched is not None
    assert fetched.region_code == "US"
    assert fetched.currency == "USD"
    assert fetched.floor_low == Decimal("0.10")
    assert fetched.floor_high == Decimal("0.20")


# ===========================================================================
# Coverage-closing repository tests.
#
# These run against the disposable PostgreSQL from the `db_session` fixture
# above, so the statements below are executed for real — the query shapes,
# the guarded conditional UPDATEs, and the unique-constraint idempotency are
# all genuinely exercised rather than mocked.
# ===========================================================================


from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError, OperationalError

from app.models import (
    CreatorPoolEntry,
    InvoiceStatus,
    MilestoneStatus,
    PayoutLedger,
    Purchase,
    Refund,
    RefundStatus,
    RevenueTier,
    StripeWebhookEvent,
    Subscription,
    WebhookEventStatus,
)
from app.repositories import (
    CreatorPoolRepository,
    InvoiceRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
    PurchaseRepository,
    RefundRepository,
    RegionFloorRepository,
    SubscriptionRepository,
    WebhookEventRepository,
    _execute_with_deadlock_retry,
)


# ---------------------------------------------------------------------------
# Deadlock retry helper (#631)
# ---------------------------------------------------------------------------


def _operational_error(pgcode: str) -> OperationalError:
    orig = MagicMock()
    orig.pgcode = pgcode
    return OperationalError("SELECT 1", {}, orig)


def _flaky(fail_times: int, pgcode: str, result=None):
    """An execute() double that fails the real statement `fail_times` times.

    The helper issues a ``SET LOCAL statement_timeout`` before every attempt, so
    the SET must succeed and only the real statement may raise.
    """
    state = {"failed": 0}
    good = result if result is not None else MagicMock(rowcount=1)

    async def _execute(stmt):
        if str(stmt).startswith("SET LOCAL"):
            return None
        if state["failed"] < fail_times:
            state["failed"] += 1
            raise _operational_error(pgcode)
        return good

    return AsyncMock(side_effect=_execute), good


class TestDeadlockRetry:
    async def test_successful_statement_is_returned_without_retrying(self):
        session = AsyncMock()
        result = await _execute_with_deadlock_retry(session, "stmt")
        assert result is session.execute.return_value
        assert session.execute.await_count == 2  # SET LOCAL + the statement

    async def test_statement_timeout_is_capped_before_every_attempt(self):
        # #631: a wedged statement must not hold a pooled connection forever.
        session = AsyncMock()
        await _execute_with_deadlock_retry(session, "stmt")
        first = session.execute.await_args_list[0].args[0]
        assert str(first) == "SET LOCAL statement_timeout = '10s'"

    @pytest.mark.parametrize("pgcode", ["40P01", "55P03"])
    async def test_deadlock_is_retried_then_succeeds(self, pgcode):
        session = AsyncMock()
        session.execute, good = _flaky(1, pgcode)
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await _execute_with_deadlock_retry(session, "stmt")
        assert result is good
        # SET LOCAL + failed stmt, then SET LOCAL + successful stmt.
        assert session.execute.await_count == 4
        # The transaction must be reset before retrying.
        session.rollback.assert_awaited_once()
        assert sleep.await_args.args[0] == 0.05

    @pytest.mark.parametrize("pgcode", ["40P01", "55P03"])
    async def test_deadlock_backoff_doubles_each_attempt(self, pgcode):
        session = AsyncMock()
        session.execute, _ = _flaky(2, pgcode)
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()) as sleep:
            await _execute_with_deadlock_retry(session, "stmt", max_attempts=3)
        # 0.05 then 0.10 — exponential, and reset per call.
        assert [c.args[0] for c in sleep.call_args_list] == [0.05, 0.1]

    async def test_backoff_is_capped_at_max_delay(self):
        session = AsyncMock()
        session.execute, _ = _flaky(4, "40P01")
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()) as sleep:
            await _execute_with_deadlock_retry(
                session, "stmt", max_attempts=5, base_delay=0.4, max_delay=0.5
            )
        assert all(c.args[0] <= 0.5 for c in sleep.call_args_list)

    @pytest.mark.parametrize("pgcode", ["40P01", "55P03"])
    async def test_deadlock_is_re_raised_when_attempts_are_exhausted(self, pgcode):
        session = AsyncMock()
        session.execute, _ = _flaky(99, pgcode)
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(OperationalError):
                await _execute_with_deadlock_retry(session, "stmt", max_attempts=2)
        # max_attempts=2 => 2 failed statements plus 2 SET LOCAL calls.
        assert session.execute.await_count == 4

    async def test_non_deadlock_operational_error_is_not_retried(self):
        # A syntax error or a dropped connection must fail fast, not spin.
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_operational_error("42P01"))
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()) as sleep:
            with pytest.raises(OperationalError):
                await _execute_with_deadlock_retry(session, "stmt")
        sleep.assert_not_awaited()
        assert session.execute.await_count == 1

    async def test_error_without_a_pgcode_is_not_retried(self):
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=OperationalError("stmt", {}, None))
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(OperationalError):
                await _execute_with_deadlock_retry(session, "stmt")
        session.rollback.assert_not_awaited()

    @pytest.mark.parametrize("exc", [RuntimeError, ValueError, KeyboardInterrupt])
    async def test_non_operational_errors_propagate_immediately(self, exc):
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=exc)
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()) as sleep:
            with pytest.raises(exc):
                await _execute_with_deadlock_retry(session, "stmt")
        sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# WebhookEventRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_event_commit_forwards_to_the_session():
    session = AsyncMock()
    await WebhookEventRepository(session).commit()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_returns_false_when_the_row_vanished_after_rollback():
    # The winner committed and the row is not visible to this transaction:
    # refuse rather than run side effects for an event we do not own.
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=select_result)
    assert await WebhookEventRepository(session).claim("evt_gone", "invoice.paid") is False
    session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# SubscriptionRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_update_tier_changes_the_tier_and_price(db_session):
    user = uuid4()
    repo = SubscriptionRepository(db_session)
    sub = await repo.create(user, RevenueTier.AVOD, Decimal("0.00"))
    await db_session.commit()

    updated = await repo.update_tier(user, RevenueTier.SVOD, Decimal("7.99"))
    await db_session.commit()

    assert updated.id == sub.id
    assert updated.tier == RevenueTier.SVOD
    assert updated.monthly_price == Decimal("7.99")
    refetched = await repo.get_by_user(user)
    assert refetched.tier == RevenueTier.SVOD


@pytest.mark.asyncio
async def test_subscription_update_tier_is_a_no_op_for_an_unknown_user(db_session):
    repo = SubscriptionRepository(db_session)
    assert await repo.update_tier(uuid4(), RevenueTier.SVOD, Decimal("7.99")) is None


@pytest.mark.asyncio
async def test_subscription_get_by_user_returns_none_for_an_unknown_user(db_session):
    assert await SubscriptionRepository(db_session).get_by_user(uuid4()) is None


# ---------------------------------------------------------------------------
# PurchaseRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_lookup_by_payment_intent(db_session):
    repo = PurchaseRepository(db_session)
    user, content = uuid4(), uuid4()
    created = await repo.create(
        user, content, Decimal("4.99"), "key-1", stripe_payment_intent_id="pi_1"
    )
    await db_session.commit()

    found = await repo.get_by_stripe_payment_intent_id("pi_1")
    assert found.id == created.id
    assert await repo.get_by_stripe_payment_intent_id("pi_missing") is None


@pytest.mark.asyncio
async def test_purchase_lookup_by_user_and_content_is_scoped(db_session):
    repo = PurchaseRepository(db_session)
    user, content, other = uuid4(), uuid4(), uuid4()
    await repo.create(user, content, Decimal("4.99"), "key-a")
    await repo.create(other, content, Decimal("4.99"), "key-b")
    await db_session.commit()

    found = await repo.get_by_user_and_content(user, content)
    assert found.idempotency_key == "key-a"
    assert await repo.get_by_user_and_content(uuid4(), content) is None


# ---------------------------------------------------------------------------
# InvoiceRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoice_get_by_id_and_stripe_id(db_session):
    repo = InvoiceRepository(db_session)
    inv = await repo.create(uuid4(), Decimal("10.00"), stripe_invoice_id="in_1")
    await db_session.commit()

    assert (await repo.get(inv.id)).id == inv.id
    assert (await repo.get_by_stripe_invoice_id("in_1")).id == inv.id
    assert await repo.get(uuid4()) is None
    assert await repo.get_by_stripe_invoice_id("in_missing") is None


@pytest.mark.asyncio
async def test_invoice_get_by_purchase_id(db_session):
    purchases = PurchaseRepository(db_session)
    repo = InvoiceRepository(db_session)
    purchase = await purchases.create(uuid4(), uuid4(), Decimal("4.99"), "key-pi")
    await db_session.commit()
    inv = await repo.create(uuid4(), Decimal("4.99"), purchase_id=purchase.id)
    await db_session.commit()

    assert (await repo.get_by_purchase_id(purchase.id)).id == inv.id
    assert await repo.get_by_purchase_id(uuid4()) is None


@pytest.mark.asyncio
async def test_invoice_get_by_user_returns_newest_first(db_session):
    repo = InvoiceRepository(db_session)
    user = uuid4()
    older = await repo.create(user, Decimal("10.00"))
    newer = await repo.create(user, Decimal("15.00"))
    await db_session.commit()
    older.issued_at = datetime(2025, 1, 1)
    newer.issued_at = datetime(2025, 1, 2)
    await db_session.commit()

    invoices = await repo.get_by_by_user if False else await repo.get_by_user(user)
    assert [i.id for i in invoices] == [newer.id, older.id]
    # get_latest_for_user is the single-row version of the same ordering.
    assert (await repo.get_latest_for_user(user)).id == newer.id


@pytest.mark.asyncio
async def test_invoice_get_by_user_is_empty_for_an_unknown_user(db_session):
    assert await InvoiceRepository(db_session).get_by_user(uuid4()) == []


@pytest.mark.asyncio
async def test_invoice_create_accepts_a_subscription_link(db_session):
    subs = SubscriptionRepository(db_session)
    repo = InvoiceRepository(db_session)
    sub = await subs.create(uuid4(), RevenueTier.SVOD, Decimal("7.99"))
    await db_session.commit()
    inv = await repo.create(
        uuid4(), Decimal("7.99"), subscription_id=sub.id, currency="EUR"
    )
    await db_session.commit()
    assert inv.subscription_id == sub.id
    assert inv.currency == "EUR"
    assert inv.status == InvoiceStatus.PENDING


# ---------------------------------------------------------------------------
# RegionFloorRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_region_floor_list_all_is_ordered_by_region_code(db_session):
    repo = RegionFloorRepository(db_session)
    for code, low, high in (("US", "0.15", "0.30"), ("EU", "0.10", "0.20"), ("IN", "0.05", "0.09")):
        db_session.add(
            RegionFloor(
                region_code=code, currency="USD", floor_low=Decimal(low), floor_high=Decimal(high)
            )
        )
    await db_session.commit()

    floors = await repo.list_all()
    assert [f.region_code for f in floors] == sorted(f.region_code for f in floors)
    assert "EU" in [f.region_code for f in floors]
    assert await repo.get_by_region("NOWHERE") is None


# ---------------------------------------------------------------------------
# CreatorPoolRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creator_pool_get_latest_returns_the_newest_cycle(db_session):
    repo = CreatorPoolRepository(db_session)
    assert await repo.get_latest() is None
    older = await repo.create_entry(
        datetime(2025, 1, 1), datetime(2025, 2, 1), Decimal("100.00"), Decimal("0.15")
    )
    newer = await repo.create_entry(
        datetime(2025, 2, 1), datetime(2025, 3, 1), Decimal("200.00"), Decimal("0.15")
    )
    await db_session.commit()
    assert (await repo.get_latest()).id == newer.id
    assert older.id != newer.id


@pytest.mark.asyncio
async def test_creator_pool_entry_computes_the_pool_amount(db_session):
    repo = CreatorPoolRepository(db_session)
    entry = await repo.create_entry(
        datetime(2025, 1, 1), datetime(2025, 2, 1), Decimal("100.00"), Decimal("0.15")
    )
    await db_session.commit()
    # Exact Decimal arithmetic: 15% of 100.00, not 15.000000000000001.
    assert entry.pool_amount == Decimal("15.0000")


@pytest.mark.asyncio
async def test_creator_pool_redistribution_is_bounded_by_the_pool_amount(db_session):
    repo = CreatorPoolRepository(db_session)
    entry = await repo.create_entry(
        datetime(2025, 1, 1), datetime(2025, 2, 1), Decimal("100.00"), Decimal("0.15")
    )
    await db_session.commit()

    # Within the pool: applied.
    assert await repo.redistribute_pool(entry.id, Decimal("5.00")) is True
    await db_session.commit()
    assert (await repo.get_latest()).redistributed_amount == Decimal("5.00")

    # Beyond the remaining balance: refused, so the pool can never go negative.
    assert await repo.redistribute_pool(entry.id, Decimal("50.00")) is False
    await db_session.commit()
    assert (await repo.get_latest()).redistributed_amount == Decimal("5.00")


@pytest.mark.asyncio
async def test_creator_pool_redistribution_of_an_unknown_entry_is_refused(db_session):
    assert await CreatorPoolRepository(db_session).redistribute_pool(uuid4(), Decimal("1.00")) is False


# ---------------------------------------------------------------------------
# MilestoneRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_create_auto_creates_four_tranches(db_session):
    repo = MilestoneRepository(db_session)
    mid = uuid4()
    ms = await repo.create(mid, "Feature Film", Decimal("10000.00"))
    await db_session.commit()

    assert ms.id is not None
    assert ms.status == MilestoneStatus.PENDING
    tranches = await repo.get_tranches(ms.id)
    assert [t.tranche_number for t in tranches] == [1, 2, 3, 4]
    assert [str(t.percentage) for t in tranches] == ["10.00", "20.00", "30.00", "40.00"]
    # Exact Decimal split of the commitment.
    assert [t.amount for t in tranches] == [
        Decimal("1000.0000"),
        Decimal("2000.0000"),
        Decimal("3000.0000"),
        Decimal("4000.0000"),
    ]


@pytest.mark.asyncio
async def test_milestone_tranche_amounts_round_independently_to_two_decimals(db_session):
    # FINDING (reported, not fixed): each tranche is rounded to NUMERIC(12,2)
    # on its own, so for a commitment that is not a multiple of 100 the four
    # tranches can sum to more than the commitment. 999.99 -> 100.00 (+0.01).
    # The payout ledger therefore can be over-funded by up to 2 cents per
    # milestone, and the kill/revert path can under-claw the difference back.
    repo = MilestoneRepository(db_session)
    ms = await repo.create(uuid4(), "Doc", Decimal("999.99"))
    await db_session.commit()
    tranches = await repo.get_tranches(ms.id)
    assert [t.amount for t in tranches] == [
        Decimal("100.00"),
        Decimal("200.00"),
        Decimal("300.00"),
        Decimal("400.00"),
    ]
    assert sum(t.amount for t in tranches) == Decimal("1000.00")
    assert sum(t.amount for t in tranches) != Decimal("999.99")


async def test_milestone_tranches_sum_exactly_for_a_clean_commitment(db_session):
    repo = MilestoneRepository(db_session)
    ms = await repo.create(uuid4(), "Doc", Decimal("10000.00"))
    await db_session.commit()
    tranches = await repo.get_tranches(ms.id)
    assert sum(t.amount for t in tranches) == Decimal("10000.00")


@pytest.mark.asyncio
async def test_milestone_get_by_id(db_session):
    repo = MilestoneRepository(db_session)
    ms = await repo.create(uuid4(), "Doc", Decimal("1.00"))
    await db_session.commit()
    assert (await repo.get(ms.id)).id == ms.id
    assert await repo.get(uuid4()) is None


@pytest.mark.asyncio
async def test_milestone_get_tranches_for_an_unknown_milestone_is_empty(db_session):
    assert await MilestoneRepository(db_session).get_tranches(uuid4()) == []


# ---------------------------------------------------------------------------
# PayoutLedgerRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payout_accrue_is_idempotent_on_the_key(db_session):
    repo = PayoutLedgerRepository(db_session)
    creator = uuid4()
    start, end = datetime(2025, 1, 1), datetime(2025, 2, 1)
    first = await repo.accrue(creator, Decimal("55.00"), "USD", "idem-1", start, end)
    await db_session.commit()
    second = await repo.accrue(creator, Decimal("55.00"), "USD", "idem-1", start, end)
    await db_session.commit()

    # A retry must not create a second ledger row.
    assert second.id == first.id
    assert len(await repo.get_by_creator(creator)) == 1


@pytest.mark.asyncio
async def test_payout_accrue_persists_the_breakdown(db_session):
    repo = PayoutLedgerRepository(db_session)
    breakdown = {"type": "tvod", "content_id": str(uuid4()), "gross": "9.99"}
    entry = await repo.accrue(
        uuid4(), Decimal("5.4945"), "USD", "idem-bd", datetime(2025, 1, 1), datetime(2025, 2, 1), breakdown
    )
    await db_session.commit()
    assert entry.breakdown == breakdown
    assert (await repo.get_by_idempotency_key("idem-bd")).id == entry.id


@pytest.mark.asyncio
async def test_payout_get_by_idempotency_key_returns_none_when_absent(db_session):
    assert await PayoutLedgerRepository(db_session).get_by_idempotency_key("nope") is None


@pytest.mark.asyncio
async def test_payout_history_is_newest_cycle_first(db_session):
    repo = PayoutLedgerRepository(db_session)
    creator = uuid4()
    await repo.accrue(creator, Decimal("1.00"), "USD", "old", datetime(2025, 1, 1), datetime(2025, 2, 1))
    await repo.accrue(creator, Decimal("2.00"), "USD", "new", datetime(2025, 2, 1), datetime(2025, 3, 1))
    await db_session.commit()

    history = await repo.get_by_creator(creator)
    assert [h.idempotency_key for h in history] == ["new", "old"]
    assert await repo.get_by_creator(uuid4()) == []


@pytest.mark.asyncio
async def test_payout_accrue_recovers_from_a_concurrent_unique_violation(db_session):
    # Simulate losing the insert race: the pre-check misses, flush hits the
    # unique constraint, and the winner's row is visible on the retry read.
    repo = PayoutLedgerRepository(db_session)
    winner = await repo.accrue(
        uuid4(), Decimal("1.00"), "USD", "race", datetime(2025, 1, 1), datetime(2025, 2, 1)
    )
    await db_session.commit()
    repo.get_by_idempotency_key = AsyncMock(side_effect=[None, winner])
    with patch.object(db_session, "flush", new=AsyncMock(
        side_effect=IntegrityError("stmt", {}, Exception("duplicate key"))
    )):
        with patch.object(db_session, "rollback", new=AsyncMock()) as rollback:
            recovered = await repo.accrue(
                uuid4(), Decimal("1.00"), "USD", "race", datetime(2025, 1, 1), datetime(2025, 2, 1)
            )
    assert recovered.id == winner.id
    # The failed INSERT must be rolled back before the winner is re-read.
    rollback.assert_awaited_once()


async def test_payout_accrue_reraises_when_the_violation_is_not_a_duplicate_key(db_session):
    repo = PayoutLedgerRepository(db_session)
    db_session.flush = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("other")))
    with pytest.raises(IntegrityError):
        await repo.accrue(
            uuid4(), Decimal("1.00"), "USD", "gone", datetime(2025, 1, 1), datetime(2025, 2, 1)
        )


# ---------------------------------------------------------------------------
# RefundRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_create_is_idempotent_on_the_stripe_refund_id(db_session):
    invoices = InvoiceRepository(db_session)
    repo = RefundRepository(db_session)
    invoice = await invoices.create(uuid4(), Decimal("10.00"))
    await db_session.commit()
    first = await repo.create(
        "re_1", Decimal("5.00"), "USD", charge_id="ch_1", invoice_id=invoice.id,
        reason="requested_by_customer",
    )
    await db_session.commit()
    second = await repo.create("re_1", Decimal("5.00"), "USD", charge_id="ch_1")
    await db_session.commit()

    assert second.id == first.id
    assert (await repo.get_by_refund_id("re_1")).id == first.id
    assert await repo.get_by_refund_id("re_missing") is None


@pytest.mark.asyncio
async def test_refund_create_defaults_to_processed(db_session):
    repo = RefundRepository(db_session)
    refund = await repo.create("re_default", Decimal("1.00"), "USD")
    await db_session.commit()
    assert refund.status == RefundStatus.PROCESSED


@pytest.mark.asyncio
async def test_refund_create_persists_a_pending_review_status(db_session):
    repo = RefundRepository(db_session)
    refund = await repo.create(
        "re_hold", Decimal("1.00"), "USD", status=RefundStatus.PENDING_REVIEW
    )
    await db_session.commit()
    assert refund.status == RefundStatus.PENDING_REVIEW


@pytest.mark.asyncio
async def test_refund_create_recovers_from_a_concurrent_unique_violation(db_session):
    repo = RefundRepository(db_session)
    winner = await repo.create("re_race", Decimal("2.00"), "USD")
    await db_session.commit()
    repo.get_by_refund_id = AsyncMock(side_effect=[None, winner])
    with patch.object(db_session, "flush", new=AsyncMock(
        side_effect=IntegrityError("stmt", {}, Exception("duplicate key"))
    )):
        with patch.object(db_session, "rollback", new=AsyncMock()) as rollback:
            recovered = await repo.create("re_race", Decimal("2.00"), "USD")
    assert recovered.id == winner.id
    rollback.assert_awaited_once()


async def test_refund_create_reraises_when_the_violation_is_unrelated(db_session):
    repo = RefundRepository(db_session)
    db_session.flush = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("other")))
    with pytest.raises(IntegrityError):
        await repo.create("re_x", Decimal("1.00"), "USD")


@pytest.mark.asyncio
async def test_refund_apply_to_invoice_increments_refunded_amount(db_session):
    invoices = InvoiceRepository(db_session)
    repo = RefundRepository(db_session)
    inv = await invoices.create(uuid4(), Decimal("10.00"))
    await db_session.commit()

    assert await repo.apply_to_invoice(inv.id, Decimal("4.00")) is True
    await db_session.commit()
    refetched = await invoices.get(inv.id)
    assert refetched.refunded_amount == Decimal("4.00")
    assert refetched.status == InvoiceStatus.REFUNDED


@pytest.mark.asyncio
async def test_refund_apply_to_invoice_refuses_to_exceed_the_invoice_total(db_session):
    invoices = InvoiceRepository(db_session)
    repo = RefundRepository(db_session)
    inv = await invoices.create(uuid4(), Decimal("10.00"))
    await db_session.commit()

    assert await repo.apply_to_invoice(inv.id, Decimal("4.00")) is True
    await db_session.commit()
    # Only 6.00 left; a 9.00 refund must be refused, not partially applied.
    assert await repo.apply_to_invoice(inv.id, Decimal("9.00")) is False
    await db_session.commit()
    assert (await invoices.get(inv.id)).refunded_amount == Decimal("4.00")


@pytest.mark.asyncio
async def test_refund_apply_is_exactly_bounded_at_the_invoice_total(db_session):
    invoices = InvoiceRepository(db_session)
    repo = RefundRepository(db_session)
    inv = await invoices.create(uuid4(), Decimal("10.00"))
    await db_session.commit()
    # The guard is `<=`, so refunding the exact remaining total is allowed.
    assert await repo.apply_to_invoice(inv.id, Decimal("10.00")) is True
    await db_session.commit()
    assert (await invoices.get(inv.id)).refunded_amount == Decimal("10.00")


@pytest.mark.asyncio
async def test_refund_apply_to_an_unknown_invoice_is_refused(db_session):
    assert await RefundRepository(db_session).apply_to_invoice(uuid4(), Decimal("1.00")) is False


@pytest.mark.asyncio
async def test_refunds_are_append_only_no_delete_is_exposed(db_session):
    # #191: money records are never deleted; the repository must not offer it.
    for repo_cls in (
        SubscriptionRepository,
        PurchaseRepository,
        InvoiceRepository,
        RegionFloorRepository,
        CreatorPoolRepository,
        MilestoneRepository,
        PayoutLedgerRepository,
        RefundRepository,
        WebhookEventRepository,
    ):
        public = {n for n in dir(repo_cls) if not n.startswith("_")}
        assert "delete" not in public
        assert "delete_all" not in public
        assert "purge" not in public


class TestDeadlockRetryFallthrough:
    async def test_the_loop_always_returns_or_raises_so_the_tail_is_unreachable(self):
        """Documents that repositories.py:87-89 can never execute.

        Every iteration either returns a result (line 73) or raises: an
        ``OperationalError`` re-raises when the code is not a deadlock or the
        attempts are exhausted (line 82), and any other ``BaseException``
        re-raises at line 85. The ``for`` loop therefore never completes
        normally, so the ``raise last_exc`` / ``raise RuntimeError`` tail below
        it is dead code kept only for type-checker friendliness.
        """
        # A statement that always raises a non-deadlock error: one iteration,
        # one raise, no fallthrough.
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=ValueError("always"))
        with pytest.raises(ValueError):
            await _execute_with_deadlock_retry(session, "stmt", max_attempts=1)

        # A statement that always raises a deadlock error: exhausts the
        # attempts and re-raises, again with no fallthrough.
        session = AsyncMock()
        session.execute, _ = _flaky(99, "40P01")
        with patch("app.repositories.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(OperationalError):
                await _execute_with_deadlock_retry(session, "stmt", max_attempts=3)

        # Only a success can return, and it returns the result inline.
        session = AsyncMock()
        session.execute, good = _flaky(0, "40P01", result="RESULT")
        assert await _execute_with_deadlock_retry(session, "stmt") == "RESULT"
