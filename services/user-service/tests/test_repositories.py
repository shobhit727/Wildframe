import pytest
import pytest_asyncio
from uuid import uuid4
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models import Base, UserProfile, UserDevice, UserPreference, UserSubscriptionProfile, DSARRequest, ChildAccount
from app.repositories import (
    UserProfileRepository,
    UserDeviceRepository,
    UserPreferenceRepository,
    UserSubscriptionProfileRepository,
    DSARRepository,
)

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture
async def db_session(tmp_path):
    """Async SQLite DB session fixture isolated per test file."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db", echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()

@pytest.mark.asyncio
async def test_user_profile_crud(db_session: AsyncSession):
    repo = UserProfileRepository(db_session)
    uid = uuid4()
    profile = await repo.create(uid)
    await db_session.commit()
    fetched = await repo.get_by_user_id(uid)
    assert fetched is not None
    assert fetched.user_id == uid
    # update
    updated = await repo.update(uid, completed_onboarding=True, profile_completeness=100)
    await db_session.commit()
    assert updated.completed_onboarding is True
    assert updated.profile_completeness == 100

@pytest.mark.asyncio
async def test_user_device_repository(db_session: AsyncSession):
    repo = UserDeviceRepository(db_session)
    uid = uuid4()
    device = await repo.create(uid, "dev-1", "Phone", "android")
    await db_session.commit()
    fetched = await repo.get_by_device_id("dev-1")
    assert fetched.device_name == "Phone"
    # unique constraint
    with pytest.raises(Exception):
        await repo.create(uid, "dev-1", "Duplicate", "android")
        await db_session.commit()
    # list devices
    devices = await repo.get_user_devices(uid)
    assert len(devices) == 1
    # deactivate
    deactivated = await repo.mark_device_inactive(device.id)
    await db_session.commit()
    assert deactivated.is_active is False

@pytest.mark.asyncio
async def test_user_preference_repository(db_session: AsyncSession):
    repo = UserPreferenceRepository(db_session)
    uid = uuid4()
    pref = await repo.create_default(uid)
    await db_session.commit()
    fetched = await repo.get_by_user_id(uid)
    assert fetched is not None
    # update
    updated = await repo.update(uid, theme="light", allow_explicit_content=False)
    await db_session.commit()
    assert updated.theme == "light"
    assert updated.allow_explicit_content is False

@pytest.mark.asyncio
async def test_subscription_repository(db_session: AsyncSession):
    repo = UserSubscriptionProfileRepository(db_session)
    uid = uuid4()
    sub = await repo.create_default(uid)
    await db_session.commit()
    fetched = await repo.get_by_user_id(uid)
    assert fetched.subscription_tier == "free"
    # tier upgrade
    upgraded = await repo.update_tier(uid, "premium")
    await db_session.commit()
    assert upgraded.subscription_tier == "premium"
    assert upgraded.can_use_4k is True

@pytest.mark.asyncio
async def test_dsar_repository(db_session: AsyncSession):
    repo = DSARRepository(db_session)
    uid = uuid4()
    dsar = await repo.create(uid, "access", ["profile"], reason="testing")
    await db_session.commit()
    fetched = await repo.get_by_id(dsar.id)
    assert fetched is not None
    assert fetched.request_type == "access"
    # SLA approx 30 days
    delta = fetched.sla_deadline - datetime.now(UTC)
    assert 29 <= delta.days <= 31

@pytest.mark.asyncio
async def test_child_account_crud(db_session: AsyncSession):
    # create child account
    child = ChildAccount(
        child_user_id=uuid4(),
        parent_user_id=uuid4(),
        relationship="parent",
    )
    db_session.add(child)
    await db_session.commit()
    await db_session.refresh(child)
    # read back
    fetched = await db_session.get(ChildAccount, child.id)
    assert fetched is not None
    assert fetched.relationship == "parent"
    # update verification method
    fetched.verification_method = "email_otp"
    await db_session.commit()
    await db_session.refresh(fetched)
    assert fetched.verification_method == "email_otp"
    # delete
    await db_session.delete(fetched)
    await db_session.commit()
    deleted = await db_session.get(ChildAccount, child.id)
    assert deleted is None