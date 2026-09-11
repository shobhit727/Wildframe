import os
from datetime import UTC, datetime
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Use in-memory SQLite for isolated model tests
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app.models import (
    Base,
    CreatorOnboarding,
    CreatorCommerce,
    EffectiveFloor,
    PayoutLedger,
    CreatorAccount,
)

@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()

# ---------- CreatorOnboarding defaults & constraints ----------
@pytest.mark.asyncio
async def test_onboarding_defaults(session: AsyncSession):
    """New onboarding record should have pending KYC and sensible defaults."""
    user_id = uuid.uuid4()
    onboarding = CreatorOnboarding(user_id=user_id, kyc_type="individual")
    session.add(onboarding)
    await session.commit()
    await session.refresh(onboarding)
    assert onboarding.kyc_status == "pending"
    assert onboarding.tax_form_verified is False
    assert onboarding.bank_verified is False
    assert onboarding.living_wage_cents == 0

@pytest.mark.asyncio
async def test_onboarding_user_unique(session: AsyncSession):
    """Only one onboarding record per user_id (unique constraint)."""
    uid = uuid.uuid4()
    o1 = CreatorOnboarding(user_id=uid, kyc_type="individual")
    o2 = CreatorOnboarding(user_id=uid, kyc_type="individual")
    session.add_all([o1, o2])
    with pytest.raises(IntegrityError):
        await session.commit()

# ---------- CreatorCommerce unique creator constraint ----------
@pytest.mark.asyncio
async def test_commerce_creator_unique(session: AsyncSession):
    cid = uuid.uuid4()
    c1 = CreatorCommerce(creator_id=cid)
    c2 = CreatorCommerce(creator_id=cid)
    session.add_all([c1, c2])
    with pytest.raises(IntegrityError):
        await session.commit()

# ---------- EffectiveFloor non‑negative constraint ----------
@pytest.mark.asyncio
async def test_effective_floor_non_negative(session: AsyncSession):
    creator_id = uuid.uuid4()
    floor = EffectiveFloor(creator_id=creator_id, per_minute_amount=-1.0, currency="USD")
    session.add(floor)
    with pytest.raises(IntegrityError):
        await session.commit()

# ---------- PayoutLedger defaults ----------
@pytest.mark.asyncio
async def test_payout_ledger_defaults(session: AsyncSession):
    creator_id = uuid.uuid4()
    ledger = PayoutLedger(
        creator_id=creator_id,
        idempotency_key=str(uuid.uuid4()),
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
    )
    session.add(ledger)
    await session.commit()
    await session.refresh(ledger)
    # status defaults to ACCRUED per model definition
    from app.models import PayoutStatus
    assert ledger.status == PayoutStatus.ACCRUED
    # stripe_fee_cents default 0
    assert ledger.stripe_fee_cents == 0

# ---------- CreatorAccount basic creation ----------
@pytest.mark.asyncio
async def test_creator_account_basic(session: AsyncSession):
    acct = CreatorAccount(
        user_id=uuid.uuid4(),
        display_name="Test Creator",
        stripe_connect_account_id=None,
        currency="USD",
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    assert acct.is_active is True
    assert acct.currency == "USD"
