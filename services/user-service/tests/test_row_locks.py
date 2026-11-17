"""Row-lock regression tests for the read-modify-write repository paths (#1123).

Each update path reads a row and then writes fields back onto it. Without
``SELECT ... FOR UPDATE`` two concurrent writers both read the same base row
and the second flush silently overwrites the first (a lost update).

SQLite ignores ``FOR UPDATE``, so these tests do not race two connections.
They record every statement the repository sends through
``AsyncSession.execute`` and compile it with the PostgreSQL dialect (the
production database), which renders the lock clause when it was requested.
Pure read paths are checked the same way and must NOT lock.
"""

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.repositories import (
    UserDeviceRepository,
    UserPreferenceRepository,
    UserProfileRepository,
    UserSubscriptionProfileRepository,
)


@pytest.fixture
def recorded_selects(db_session, monkeypatch):
    """Record the PostgreSQL rendering of every SELECT sent via session.execute."""
    selects: list[str] = []
    real_execute = db_session.execute

    async def spy(statement, *args, **kwargs):
        if isinstance(statement, Select):
            selects.append(str(statement.compile(dialect=postgresql.dialect())))
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", spy)
    return selects


def _locked(selects: list[str], table: str) -> list[str]:
    return [s for s in selects if f"FROM {table}" in s and "FOR UPDATE" in s]


def _reads_of(selects: list[str], table: str) -> list[str]:
    return [s for s in selects if f"FROM {table}" in s]


async def test_profile_update_reads_its_row_with_for_update(db_session, recorded_selects):
    user_id = uuid4()
    repo = UserProfileRepository(db_session)
    await repo.create(user_id, bio="before")
    recorded_selects.clear()

    updated = await repo.update(user_id, bio="after")

    assert updated is not None and updated.bio == "after"
    assert _locked(recorded_selects, "user_profiles"), (
        "UserProfileRepository.update() read its row without FOR UPDATE: "
        f"concurrent updates can lose writes (#1123). SELECTs: {recorded_selects}"
    )


async def test_device_update_reads_its_row_with_for_update(db_session, recorded_selects):
    user_id = uuid4()
    repo = UserDeviceRepository(db_session)
    device = await repo.create(user_id, "dev-1", "Phone", "mobile")
    recorded_selects.clear()

    updated = await repo.update(device.id, user_id, device_name="Renamed")

    assert updated is not None and updated.device_name == "Renamed"
    assert _locked(recorded_selects, "user_devices"), (
        "UserDeviceRepository.update() read its row without FOR UPDATE: "
        f"concurrent updates can lose writes (#1123). SELECTs: {recorded_selects}"
    )


async def test_device_update_does_not_touch_another_users_device(db_session, recorded_selects):
    owner = uuid4()
    repo = UserDeviceRepository(db_session)
    device = await repo.create(owner, "dev-2", "Tablet", "tablet")

    assert await repo.update(device.id, uuid4(), device_name="Hijack") is None
    assert device.device_name == "Tablet"


async def test_preference_update_reads_its_row_with_for_update(db_session, recorded_selects):
    user_id = uuid4()
    repo = UserPreferenceRepository(db_session)
    await repo.create_default(user_id)
    recorded_selects.clear()

    updated = await repo.update(user_id, language="fr")

    assert updated is not None and updated.language == "fr"
    assert _locked(recorded_selects, "user_preferences"), (
        "UserPreferenceRepository.update() read its row without FOR UPDATE: "
        f"concurrent updates can lose writes (#1123). SELECTs: {recorded_selects}"
    )


async def test_subscription_update_tier_reads_its_row_with_for_update(db_session, recorded_selects):
    user_id = uuid4()
    repo = UserSubscriptionProfileRepository(db_session)
    await repo.create_default(user_id)
    recorded_selects.clear()

    updated = await repo.update_tier(user_id, "premium")

    assert updated is not None and updated.subscription_tier == "premium"
    assert updated.can_use_4k is True and updated.max_concurrent_streams == 4
    assert _locked(recorded_selects, "user_subscription_profiles"), (
        "UserSubscriptionProfileRepository.update_tier() read its row without "
        f"FOR UPDATE: concurrent tier changes can tear (#1123). SELECTs: {recorded_selects}"
    )


async def test_pure_reads_take_no_row_lock(db_session, recorded_selects):
    user_id = uuid4()
    profiles = UserProfileRepository(db_session)
    preferences = UserPreferenceRepository(db_session)
    subscriptions = UserSubscriptionProfileRepository(db_session)
    devices = UserDeviceRepository(db_session)
    await profiles.create(user_id)
    await preferences.create_default(user_id)
    await subscriptions.create_default(user_id)
    await devices.create(user_id, "dev-3", "TV", "tv")
    recorded_selects.clear()

    assert await profiles.get_by_user_id(user_id) is not None
    assert await preferences.get_by_user_id(user_id) is not None
    assert await subscriptions.get_by_user_id(user_id) is not None
    assert await preferences.get_or_create_default(user_id) is not None
    assert await subscriptions.get_or_create_default(user_id) is not None
    assert await devices.get_by_device_id("dev-3") is not None
    assert await devices.get_user_devices(user_id)

    for table in (
        "user_profiles",
        "user_preferences",
        "user_subscription_profiles",
        "user_devices",
    ):
        assert _reads_of(recorded_selects, table), f"no read of {table} was recorded"
    locked = [s for s in recorded_selects if "FOR UPDATE" in s]
    assert not locked, f"pure read paths must not take row locks (#1123): {locked}"
