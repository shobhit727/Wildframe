# tests for repository layer in creators-service
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

import uuid
import pytest_asyncio
from datetime import datetime

from app.models import Base, CreatorSuspendedError, PayoutStatus
from app.repositories import (
    CreatorAccountRepository,
    PayoutLedgerRepository,
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_creator_account_and_idempotent_accrual(session: AsyncSession):
    acct_repo = CreatorAccountRepository(session)
    user_id = uuid.uuid4()
    acct = await acct_repo.create(user_id=user_id, display_name="Test", region_code="US")
    assert (await acct_repo.get_by_user(user_id)).id == acct.id
    ledger_repo = PayoutLedgerRepository(session)
    values = dict(
        creator_id=acct.id,
        period_start=datetime(2024, 1, 1),
        period_end=datetime(2024, 1, 31),
        view_minutes=100,
        floor_cents=1000,
        pool_topup_cents=200,
        share_cents=300,
        stripe_fee_cents=50,
        net_cents=1450,
        idempotency_key="test-key",
    )
    row = await ledger_repo.accrued(**values)
    duplicate = await ledger_repo.accrued(**{**values, "net_cents": 9999})
    assert duplicate.id == row.id
    await session.refresh(duplicate)
    assert duplicate.net_cents == 1450
    assert duplicate.status == PayoutStatus.ACCRUED
    # PayoutLedgerRepository.accrued derives the stored key from
    # (creator_id, period_start, period_end) and ignores the caller's
    # ``idempotency_key`` argument, so look the row up by the derived key.
    derived_key = (
        f"{acct.id}:{values['period_start'].isoformat()}:{values['period_end'].isoformat()}"
    )
    assert row.idempotency_key == derived_key
    assert (await ledger_repo.get_by_idempotency_key(derived_key)).id == row.id
    assert await ledger_repo.get_by_idempotency_key("test-key") is None


@pytest.mark.asyncio
async def test_payout_ledger_suspended_creator(session: AsyncSession):
    acct_repo = CreatorAccountRepository(session)
    acct = await acct_repo.create(user_id=uuid.uuid4(), display_name="Test", region_code="US")
    await acct_repo.update(acct, is_active=False)
    ledger_repo = PayoutLedgerRepository(session)
    with pytest.raises(CreatorSuspendedError):
        await ledger_repo.accrued(
            creator_id=acct.id,
            period_start=datetime(2024, 1, 1),
            period_end=datetime(2024, 1, 31),
            view_minutes=100,
            floor_cents=1000,
            pool_topup_cents=0,
            share_cents=0,
            stripe_fee_cents=0,
            net_cents=1000,
            idempotency_key="suspended",
        )
    rolled_back_key = f"{acct.id}:2024-01-01T00:00:00:2024-01-31T00:00:00"
    assert await ledger_repo.get_by_idempotency_key(rolled_back_key) is None
