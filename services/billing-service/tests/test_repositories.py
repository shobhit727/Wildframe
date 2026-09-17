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
