"""Error-path and edge-case tests for `app.repositories` (the main package).

`tests/test_repositories.py` covers the happy paths; the 47 lines it left are
the failure handling: the `IntegrityError` rollback-and-reraise branches, the
"row belongs to someone else / does not exist" early returns, the broad
`except Exception` rollbacks, and `BaseRepository.commit`.

These are the branches that decide whether a duplicate registration 500s or
degrades gracefully, so they are asserted on real aiosqlite rather than mocks
wherever the database can produce the failure itself.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, UserDevice, UserPreference, UserProfile, UserSubscriptionProfile
from app.repositories import (
    UserDeviceRepository,
    UserPreferenceRepository,
    UserProfileRepository,
    UserSubscriptionProfileRepository,
)


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/repo_errors.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


async def _seed_profile(session: AsyncSession, user_id):
    profile = UserProfile(user_id=user_id)
    session.add(profile)
    await session.commit()
    return profile


# ---------------------------------------------------------------------------
# BaseRepository.commit
# ---------------------------------------------------------------------------


async def test_base_repository_commit_delegates_to_the_session(session: AsyncSession):
    repo = UserProfileRepository(session)
    user_id = uuid4()
    repo.session.add(UserProfile(user_id=user_id))

    await repo.commit()

    assert (await repo.get_by_user_id(user_id)) is not None


# ---------------------------------------------------------------------------
# UserProfileRepository
# ---------------------------------------------------------------------------


async def test_profile_create_rolls_back_and_reraises_on_a_duplicate_user(session: AsyncSession):
    user_id = uuid4()
    await _seed_profile(session, user_id)
    repo = UserProfileRepository(session)

    with pytest.raises(IntegrityError):
        await repo.create(user_id)

    # The rollback left the session usable (no PendingRollbackError).
    assert (await repo.get_by_user_id(user_id)) is not None


async def test_profile_get_by_id_returns_the_row_and_none(session: AsyncSession):
    repo = UserProfileRepository(session)
    profile = await _seed_profile(session, uuid4())

    found = await repo.get_by_id(profile.id)

    assert found is not None
    assert found.id == profile.id
    assert await repo.get_by_id(uuid4()) is None


async def test_profile_update_returns_none_for_an_unknown_user(session: AsyncSession):
    repo = UserProfileRepository(session)

    assert await repo.update(uuid4(), bio="nope") is None


async def test_profile_update_ignores_unknown_attribute_names(session: AsyncSession):
    repo = UserProfileRepository(session)
    user_id = uuid4()
    await _seed_profile(session, user_id)

    updated = await repo.update(user_id, not_a_real_column="ignored", bio="hello")

    assert updated is not None
    assert updated.bio == "hello"
    assert not hasattr(updated, "not_a_real_column")


async def test_profile_update_rolls_back_and_reraises_on_a_flush_failure(
    session: AsyncSession,
):
    repo = UserProfileRepository(session)
    user_id = uuid4()
    await _seed_profile(session, user_id)
    repo.flush = AsyncMock(side_effect=RuntimeError("flush blew up"))

    with pytest.raises(RuntimeError, match="flush blew up"):
        await repo.update(user_id, bio="hello")


async def test_mark_onboarding_complete_sets_the_completeness_to_100(session: AsyncSession):
    repo = UserProfileRepository(session)
    user_id = uuid4()
    await _seed_profile(session, user_id)

    profile = await repo.mark_onboarding_complete(user_id)

    assert profile is not None
    assert profile.completed_onboarding is True
    assert profile.profile_completeness == 100


async def test_mark_onboarding_complete_returns_none_for_an_unknown_user(session: AsyncSession):
    repo = UserProfileRepository(session)

    assert await repo.mark_onboarding_complete(uuid4()) is None


# ---------------------------------------------------------------------------
# UserDeviceRepository
# ---------------------------------------------------------------------------


async def test_device_get_by_id_returns_the_row_and_none(session: AsyncSession):
    repo = UserDeviceRepository(session)
    device = await repo.create(uuid4(), "dev-by-id", "Phone", "android")
    await session.commit()

    found = await repo.get_by_id(device.id)

    assert found is not None
    assert found.device_id == "dev-by-id"
    assert await repo.get_by_id(uuid4()) is None


async def test_device_update_returns_none_when_the_device_does_not_exist(
    session: AsyncSession,
):
    repo = UserDeviceRepository(session)

    assert await repo.update(uuid4(), uuid4(), device_name="x") is None


async def test_device_update_returns_none_for_another_users_device(session: AsyncSession):
    """The ownership check is the anti-impersonation guard."""
    repo = UserDeviceRepository(session)
    owner, attacker = uuid4(), uuid4()
    device = await repo.create(owner, "owned-device", "Phone", "android")
    await session.commit()

    assert await repo.update(device.id, attacker, device_name="hijacked") is None


async def test_device_update_skips_none_values(session: AsyncSession):
    repo = UserDeviceRepository(session)
    user_id = uuid4()
    device = await repo.create(user_id, "dev-none", "Phone", "android")
    await session.commit()

    updated = await repo.update(device.id, user_id, device_name=None, device_type="ios")

    assert updated is not None
    # `None` values are ignored (see the `value is not None` guard).
    assert updated.device_name == "Phone"
    assert updated.device_type == "ios"


async def test_device_update_bumps_last_active_when_is_active_changes(
    session: AsyncSession,
):
    repo = UserDeviceRepository(session)
    user_id = uuid4()
    device = await repo.create(user_id, "dev-active", "Phone", "android")
    await session.commit()
    # `create` does not stamp last_active_at; the service layer does.
    assert device.last_active_at is None

    updated = await repo.update(device.id, user_id, is_active=True)

    assert updated is not None
    assert updated.last_active_at is not None


async def test_device_update_does_not_bump_last_active_for_other_fields(
    session: AsyncSession,
):
    repo = UserDeviceRepository(session)
    user_id = uuid4()
    device = await repo.create(user_id, "dev-quiet", "Phone", "android")
    await session.commit()
    device.last_active_at = None
    await session.commit()

    updated = await repo.update(device.id, user_id, device_name="Renamed")

    assert updated is not None
    assert updated.last_active_at is None


async def test_device_update_rolls_back_and_reraises(session: AsyncSession):
    repo = UserDeviceRepository(session)
    user_id = uuid4()
    device = await repo.create(user_id, "dev-fail", "Phone", "android")
    await session.commit()
    repo.flush = AsyncMock(side_effect=RuntimeError("nope"))

    with pytest.raises(RuntimeError, match="nope"):
        await repo.update(device.id, user_id, device_name="x")


async def test_device_delete_removes_an_owned_device(session: AsyncSession):
    repo = UserDeviceRepository(session)
    user_id = uuid4()
    device = await repo.create(user_id, "dev-delete", "Phone", "android")
    await session.commit()

    assert await repo.delete(device.id, user_id) is True
    await session.commit()
    assert await repo.get_by_id(device.id) is None


async def test_device_delete_returns_false_for_an_unknown_device(session: AsyncSession):
    repo = UserDeviceRepository(session)

    assert await repo.delete(uuid4(), uuid4()) is False


async def test_device_delete_returns_false_for_another_users_device(session: AsyncSession):
    repo = UserDeviceRepository(session)
    owner, attacker = uuid4(), uuid4()
    device = await repo.create(owner, "dev-protected", "Phone", "android")
    await session.commit()

    assert await repo.delete(device.id, attacker) is False
    assert await repo.get_by_id(device.id) is not None


async def test_device_delete_rolls_back_and_reraises(session: AsyncSession):
    repo = UserDeviceRepository(session)
    user_id = uuid4()
    device = await repo.create(user_id, "dev-delete-fail", "Phone", "android")
    await session.commit()
    repo.flush = AsyncMock(side_effect=RuntimeError("cannot flush"))

    with pytest.raises(RuntimeError, match="cannot flush"):
        await repo.delete(device.id, user_id)


# ---------------------------------------------------------------------------
# UserPreferenceRepository
# ---------------------------------------------------------------------------


async def test_preference_create_default_rolls_back_on_a_duplicate_user(
    session: AsyncSession,
):
    user_id = uuid4()
    repo = UserPreferenceRepository(session)
    await repo.create_default(user_id)
    await session.commit()

    with pytest.raises(IntegrityError):
        await repo.create_default(user_id)


async def test_preference_update_returns_none_for_an_unknown_user(session: AsyncSession):
    repo = UserPreferenceRepository(session)

    assert await repo.update(uuid4(), theme="dark") is None


async def test_preference_update_ignores_none_and_unknown_fields(session: AsyncSession):
    repo = UserPreferenceRepository(session)
    user_id = uuid4()
    await repo.create_default(user_id)
    await session.commit()

    updated = await repo.update(
        user_id, theme=None, not_a_field=1, autoplay_next_episode=False
    )

    assert updated is not None
    assert updated.theme == "dark"  # unchanged: `None` values are ignored
    assert updated.autoplay_next_episode is False
    assert not hasattr(updated, "not_a_field")


async def test_preference_update_rolls_back_and_reraises(session: AsyncSession):
    repo = UserPreferenceRepository(session)
    user_id = uuid4()
    await repo.create_default(user_id)
    await session.commit()
    repo.flush = AsyncMock(side_effect=RuntimeError("prefs flush"))

    with pytest.raises(RuntimeError, match="prefs flush"):
        await repo.update(user_id, theme="dark")


# ---------------------------------------------------------------------------
# UserSubscriptionProfileRepository
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,streams,download,uhd,ad_free",
    [
        ("free", 1, False, False, False),
        ("basic", 2, True, False, True),
        ("premium", 4, True, True, True),
    ],
)
async def test_subscription_create_default_sets_tier_entitlements(
    session: AsyncSession, tier, streams, download, uhd, ad_free
):
    repo = UserSubscriptionProfileRepository(session)

    subscription = await repo.create_default(uuid4(), tier=tier)

    assert subscription.subscription_tier == tier
    assert subscription.max_concurrent_streams == streams
    assert subscription.can_download is download
    assert subscription.can_use_4k is uhd
    assert subscription.ad_free is ad_free


async def test_subscription_create_default_rolls_back_on_a_duplicate_user(
    session: AsyncSession,
):
    user_id = uuid4()
    repo = UserSubscriptionProfileRepository(session)
    await repo.create_default(user_id)
    await session.commit()

    with pytest.raises(IntegrityError):
        await repo.create_default(user_id)


async def test_subscription_update_tier_returns_none_for_an_unknown_user(
    session: AsyncSession,
):
    repo = UserSubscriptionProfileRepository(session)

    assert await repo.update_tier(uuid4(), "premium") is None


@pytest.mark.parametrize(
    "tier,streams,download,uhd,ad_free",
    [
        ("premium", 4, True, True, True),
        ("basic", 2, True, False, True),
        ("free", 1, False, False, False),
    ],
)
async def test_subscription_update_tier_recalculates_entitlements(
    session: AsyncSession, tier, streams, download, uhd, ad_free
):
    repo = UserSubscriptionProfileRepository(session)
    user_id = uuid4()
    await repo.create_default(user_id)
    await session.commit()

    subscription = await repo.update_tier(user_id, tier)

    assert subscription is not None
    assert subscription.subscription_tier == tier
    assert subscription.max_concurrent_streams == streams
    assert subscription.can_download is download
    assert subscription.can_use_4k is uhd
    assert subscription.ad_free is ad_free


async def test_subscription_update_tier_rolls_back_and_reraises(session: AsyncSession):
    repo = UserSubscriptionProfileRepository(session)
    user_id = uuid4()
    await repo.create_default(user_id)
    await session.commit()
    repo.flush = AsyncMock(side_effect=RuntimeError("tier flush"))

    with pytest.raises(RuntimeError, match="tier flush"):
        await repo.update_tier(user_id, "premium")
