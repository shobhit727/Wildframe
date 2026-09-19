import pytest
from uuid import uuid4
from decimal import Decimal

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models import (
    Base,
    Subscription,
    Purchase,
    Invoice,
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
# Fixtures – isolated SQLite DB per test (mirrors auth-service conftest)
# ---------------------------------------------------------------------------
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                Subscription.__table__,
                Purchase.__table__,
                Invoice.__table__,
                RegionFloor.__table__,
            ],
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as session:
        yield session
        await session.rollback()


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
    # First invoice
    await repo.create(user_id, Decimal("10.00"))
    await db_session.commit()
    # Slight delay to ensure different timestamps
    await db_session.flush()
    # Second (newer) invoice
    inv2 = await repo.create(user_id, Decimal("15.00"))
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
