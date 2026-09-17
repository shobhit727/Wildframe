# tests for repository layer in creators-service
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

import uuid
import pytest_asyncio
from datetime import datetime, timezone
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app.models import CreatorOnboarding
from app.repositories import (
    CreatorAccountRepository,
    PayoutLedgerRepository,
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(os.getenv("DATABASE_URL"), echo=False, future=True)
    async with engine.begin() as conn:
        # create tables
        from app.models import Base

        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_creator_account_and_onboarding_flow(session: AsyncSession):
    acct_repo = CreatorAccountRepository(session)
    user_id = uuid.uuid4()
    acct = await acct_repo.create(user_id=user_id, display_name="Test", region_code="US")
    assert acct.user_id == user_id
    # onboarding record directly (no dedicated repo)
    _ = CreatorOnboarding(user_id=user_id, kyc_type="individual")
    # setup dates for ledger
    period_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    period_end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
    ledger_repo = PayoutLedgerRepository(session)
    key = "test-key"
    row1 = await ledger_repo.accrued(
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        amount_usd=100.0,
        idempotency_key=key,
    )
    assert row1.idempotency_key == key
    assert row1.status == "accrued"

    row2 = await ledger_repo.accrued(
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        amount_usd=150.0,
        idempotency_key=key,
    )
    assert row2.id == row1.id


@pytest.mark.asyncio
async def test_payout_ledger_suspended_creator(session: AsyncSession):
    acct_repo = CreatorAccountRepository(session)
    user_id = uuid.uuid4()
    await acct_repo.create(user_id=user_id, display_name="Test", region_code="US")
    await acct_repo.set_status(user_id, "suspended")

    ledger_repo = PayoutLedgerRepository(session)
    period_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    period_end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    from app.models import CreatorSuspendedError

    try:
        await ledger_repo.accrued(
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            amount_usd=100.0,
            idempotency_key="test-key-suspended",
        )
        assert False, "Expected CreatorSuspendedError"
    except CreatorSuspendedError:
        pass
