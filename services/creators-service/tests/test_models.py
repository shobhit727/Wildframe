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
    CreatorPayout,
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

# ---------- CreatorAccount KYC transition ----------
@pytest.mark.asyncio
async def test_creator_account_kyc_transition(session: AsyncSession):
    """Verify KYC status can transition and timestamps recorded."""
    acct = CreatorAccount(
        user_id=uuid.uuid4(),
        display_name="KYC Creator",
        stripe_connect_account_id="acct_123",
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    assert acct.kyc_status == "pending"  # type: ignore[attr-defined]
    # transition to verified
    acct.kyc_status = "verified"  # type: ignore[attr-defined]
    acct.kyc_verified_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(acct)
    assert acct.kyc_status == "verified"
    assert acct.kyc_verified_at is not None

# ---------- CreatorOnboarding extended fields ----------
@pytest.mark.asyncio
async def test_creator_onboarding_extended(session: AsyncSession):
    """Test contract version, tax form verification, and bank verification flags."""
    uid = uuid.uuid4()
    onboarding = CreatorOnboarding(
        user_id=uid,
        kyc_type="individual",
        tax_form_type="W-9",
        tax_form_verified=True,
        bank_verified=True,
    )
    session.add(onboarding)
    await session.commit()
    await session.refresh(onboarding)
    assert onboarding.contract_version == "1.0.0"
    assert onboarding.tax_form_verified is True
    assert onboarding.bank_verified is True

# ---------- CreatorCommerce defaults and updates ----------
@pytest.mark.asyncio
async def test_creator_commerce_defaults_and_update(session: AsyncSession):
    cid = uuid.uuid4()
    commerce = CreatorCommerce(creator_id=cid)
    session.add(commerce)
    await session.commit()
    await session.refresh(commerce)
    assert commerce.default_currency == "USD"
    # update fields
    commerce.payout_destination_id = "dest_567"
    commerce.stripe_account_id = "acct_789"
    await session.commit()
    await session.refresh(commerce)
    assert commerce.payout_destination_id == "dest_567"
    assert commerce.stripe_account_id == "acct_789"

# ---------- CreatorPayout creation and status flow ----------
@pytest.mark.asyncio
async def test_creator_payout_status_flow(session: AsyncSession):
    cid = uuid.uuid4()
    payout = CreatorPayout(
        creator_id=cid,
        amount_cents=10000,
        schedule="net-45",
    )
    session.add(payout)
    await session.commit()
    await session.refresh(payout)
    assert payout.status == "pending"
    # transition to paid
    payout.status = "paid"
    await session.commit()
    await session.refresh(payout)
    assert payout.status == "paid"

# ---------- PayoutLedger constraints and net calculation ----------
@pytest.mark.asyncio
async def test_payout_ledger_constraints_and_net(session: AsyncSession):
    creator_id = uuid.uuid4()
    ledger = PayoutLedger(
        creator_id=creator_id,
        idempotency_key=str(uuid.uuid4()),
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        floor_cents=500,
        pool_topup_cents=200,
        share_cents=300,
        stripe_fee_cents=50,
        net_cents=950,
    )
    session.add(ledger)
    await session.commit()
    await session.refresh(ledger)
    assert ledger.net_cents == 950
    # negative floor should raise constraint
    bad = PayoutLedger(
        creator_id=creator_id,
        idempotency_key=str(uuid.uuid4()),
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        floor_cents=-1,
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()