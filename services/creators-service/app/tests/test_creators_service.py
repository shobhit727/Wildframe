"""Tests for the Creators service.

Run with: python -m pytest (asyncio-mode=auto) from services/creators-service.

Uses in-memory async SQLite with the SAME models as production. SQLite does not
enforce the PostgreSQL-only column types (UUID, ENUM) the way pg would, but it
exercises the repository/service logic faithfully for the invariants we care
about: idempotency, floor >= 0, kill rolls back only unreleased tranches, and
onboarding defaults KYC to pending.
"""
import os
import pytest
import pytest_asyncio
from uuid import uuid4
from datetime import datetime, timezone

# Use in-memory SQLite for tests BEFORE importing app code that reads settings.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.models import Base, KYCStatus, MilestoneStatus, TrancheStatus, PayoutStatus
from app.repositories import (
    CreatorAccountRepository, EffectiveFloorRepository,
    CreatorPoolBalanceRepository, MilestoneRepository,
    PayoutLedgerRepository,
)
from app.services import CreatorService


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


@pytest_asyncio.fixture
async def service(session):
    return CreatorService(
        CreatorAccountRepository(session),
        EffectiveFloorRepository(session),
        CreatorPoolBalanceRepository(session),
        MilestoneRepository(session),
        PayoutLedgerRepository(session),
    )


def _svc(session):
    return CreatorService(
        CreatorAccountRepository(session),
        EffectiveFloorRepository(session),
        CreatorPoolBalanceRepository(session),
        MilestoneRepository(session),
        PayoutLedgerRepository(session),
    )


# ------------------------------------------------------------- idempotent accrual
@pytest.mark.asyncio
async def test_accrual_is_idempotent(session):
    """Invariant: the same (creator, period) accrues EXACTLY one ledger row.

    Protects: double-pay from a retried payout / retried Stripe webhook
    (PRODUCT_VISION §4). The idempotency_key is unique; a second call must
    return the existing row, not insert a new one.
    """
    from sqlalchemy import select
    from app.models import PayoutLedger

    user_id = uuid4()
    acct = await CreatorAccountRepository(session).create(user_id=user_id,
                                                           display_name="Ida")
    await session.flush()
    svc = _svc(session)

    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 1, 31, tzinfo=timezone.utc)

    row1 = await svc.accrue_payout(
        creator_id=acct.id, period_start=period_start, period_end=period_end,
        view_minutes=100, earned_cents=500, stripe_fee_cents=10,
    )
    row2 = await svc.accrue_payout(
        creator_id=acct.id, period_start=period_start, period_end=period_end,
        view_minutes=100, earned_cents=500, stripe_fee_cents=10,
    )
    await session.flush()

    assert row1.id == row2.id, "second accrual must return the same row"

    stmt = select(PayoutLedger).where(PayoutLedger.creator_id == acct.id)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    assert len(rows) == 1, "exactly one ledger row per (creator, period)"


# ------------------------------------------------------------ floor invariant >=0
@pytest.mark.asyncio
async def test_floor_must_be_non_negative(session):
    """Invariant: effective floor is a minimum guarantee, never negative.

    Protects: a negative floor would imply the platform owes the creator for
    NOT publishing, which breaks the pool math. Rejected at the service layer.
    """
    user_id = uuid4()
    acct = await CreatorAccountRepository(session).create(user_id=user_id,
                                                           display_name="Flo")
    await session.flush()
    svc = _svc(session)

    with pytest.raises(AssertionError):
        await svc.set_floor(acct.id, per_minute_amount=-1.0)

    # A zero floor is valid (no guarantee, pool-only).
    zero = await svc.set_floor(acct.id, per_minute_amount=0.0)
    assert zero.per_minute_amount == 0.0


# -------------------------------------- kill rolls back only unreleased tranches
@pytest.mark.asyncio
async def test_kill_rolls_back_only_unreleased_tranches(session):
    """Invariant: killing a milestone flips every non-released tranche to
    rolled_back in ONE transaction; released tranches stay released.

    Protects: capital protection (PRODUCT_VISION §2.3). Funds already released
    to the creator for completed milestones are NOT clawed back — only the
    remaining locked tranches revert to the pool.
    """
    user_id = uuid4()
    acct = await CreatorAccountRepository(session).create(user_id=user_id,
                                                           display_name="Kil")
    await session.flush()
    svc = _svc(session)

    ms = await svc.create_milestone("Series A", acct.id, total_cents=100_00)
    await svc.add_tranche(ms.id, threshold=10, amount_cents=10_00,
                          release_condition="script")
    await svc.add_tranche(ms.id, threshold=30, amount_cents=30_00,
                          release_condition="animatic")
    await svc.add_tranche(ms.id, threshold=60, amount_cents=40_00,
                          release_condition="first cut")
    await svc.add_tranche(ms.id, threshold=100, amount_cents=20_00,
                          release_condition="final")
    await session.flush()

    # Release the first two tranches (script + animatic done).
    await svc.release_tranche(ms.id, threshold=10)
    await svc.release_tranche(ms.id, threshold=30)
    await session.flush()

    # Kill the milestone (missed first cut).
    killed = await svc.kill_milestone(ms.id, reason="missed first cut deadline")
    await session.flush()

    assert killed.status == MilestoneStatus.KILLED

    # Inspect tranches.
    from sqlalchemy import select
    from app.models import MilestoneTranche
    stmt = select(MilestoneTranche).where(MilestoneTranche.milestone_id == ms.id)
    result = await session.execute(stmt)
    tranches = sorted(result.scalars().all(), key=lambda t: t.threshold)

    by_threshold = {t.threshold: t for t in tranches}
    assert by_threshold[10].status == TrancheStatus.RELEASED, "released stays released"
    assert by_threshold[30].status == TrancheStatus.RELEASED, "released stays released"
    assert by_threshold[60].status == TrancheStatus.ROLLED_BACK, "unreleased rolled back"
    assert by_threshold[100].status == TrancheStatus.ROLLED_BACK, "unreleased rolled back"


# ----------------------------------------------- onboarding default kyc pending
@pytest.mark.asyncio
async def test_onboarding_defaults_kyc_pending(session):
    """Invariant: a newly onboarded creator starts at KYC pending.

    Protects: a creator must NOT receive payouts before identity review
    (PRODUCT_VISION §4). The default is the safe state; verified is only set
    after an explicit admin/verification step.
    """
    user_id = uuid4()
    acct = await CreatorAccountRepository(session).create(user_id=user_id,
                                                           display_name="New")
    await session.flush()
    assert acct.kyc_status == KYCStatus.PENDING
    assert acct.kyc_verified_at is None
    assert acct.is_active is True
