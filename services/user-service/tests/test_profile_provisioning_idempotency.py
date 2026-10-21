"""Regression tests for idempotent profile provisioning (issue #1043).

``POST /api/v1/profiles`` is an *ensure* path, not a strict create. The
Account page posts when it cannot find a profile, and the Kafka consumer
provisions profiles from at-least-once ``user.registered`` events (see
``app/core/event_consumer.py``), so the same user can legitimately arrive
with some of the rows already in place. The state reproduced here is the one
from the bug report: a user that has a preferences row but no profile row.

Before the fix, the default-preferences insert hit the unique constraint,
``UserPreferenceRepository.create_default`` rolled the *whole session* back
-- discarding the profile row the same call had just written -- and the
request failed with a 500 that the gateway surfaced as a 502. The Account
page then looped GET 404 -> POST 502 -> GET 404 forever.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import get_current_user_id
from app.core.database import get_db_session
from app.main import app
from app.models import Base, UserPreference, UserProfile, UserSubscriptionProfile
from app.repositories import UserPreferenceRepository, UserProfileRepository


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """A session factory over a real (SQLite) schema, isolated per test."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/provisioning.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def user_id():
    return uuid4()


@pytest_asyncio.fixture
async def app_session(session_factory):
    """The session the application under test writes through."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(app_session, user_id):
    """An HTTP client whose requests hit a real database session.

    The auth dependency is overridden because these tests are about the
    provisioning behaviour, not about token verification.
    """
    app.dependency_overrides[get_db_session] = lambda: app_session
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _count(session_factory, model, user_id):
    """Count the rows of ``model`` that belong to ``user_id``."""
    async with session_factory() as session:
        result = await session.execute(select(model).where(model.user_id == user_id))
        return len(result.all())


# ---------------------------------------------------------------------------
# The reported defect
# ---------------------------------------------------------------------------


async def test_post_profiles_provisions_the_profile_when_preferences_already_exist(
    client, app_session, session_factory, user_id
):
    """A user with preferences but no profile must still get a profile.

    This is the exact state from issue #1043. Before the fix the request
    answered 500 (502 through the gateway) and no profile row was left
    behind, so the Account page looped forever.
    """
    # The half-created user: preferences exist, the profile does not.
    app_session.add(UserPreference(user_id=user_id))
    await app_session.commit()

    response = await client.post("/api/v1/profiles")

    assert response.status_code == 201, (
        "POST /api/v1/profiles must succeed for a user that already has "
        f"preferences; got {response.status_code}: {response.text}"
    )

    # The profile row must exist *and* be committed, so read it back through
    # a different session rather than trusting the one under test.
    assert await _count(session_factory, UserProfile, user_id) == 1


async def test_post_profiles_is_idempotent(client, session_factory, user_id):
    """Posting twice must not fail and must not duplicate any row."""
    first = await client.post("/api/v1/profiles")
    second = await client.post("/api/v1/profiles")

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]

    assert await _count(session_factory, UserProfile, user_id) == 1
    assert await _count(session_factory, UserPreference, user_id) == 1
    assert await _count(session_factory, UserSubscriptionProfile, user_id) == 1


async def test_post_profiles_completes_a_half_created_user(
    client, app_session, session_factory, user_id
):
    """Preferences *and* subscription present, profile missing: still a profile."""
    app_session.add(UserPreference(user_id=user_id))
    app_session.add(UserSubscriptionProfile(user_id=user_id, subscription_tier="free"))
    await app_session.commit()

    response = await client.post("/api/v1/profiles")

    assert response.status_code == 201, response.text
    assert await _count(session_factory, UserProfile, user_id) == 1
    assert await _count(session_factory, UserPreference, user_id) == 1
    assert await _count(session_factory, UserSubscriptionProfile, user_id) == 1


# ---------------------------------------------------------------------------
# The failure must stay scoped to the write that failed
# ---------------------------------------------------------------------------


async def test_losing_the_insert_race_does_not_discard_earlier_writes(session_factory, user_id):
    """A failed INSERT must cost that INSERT only, not the whole unit of work.

    The duplicate is produced by the database: the competing row is
    committed by another connection. Only the SELECT is faked, to open the
    window between "the row is not there" and "the row is inserted" that a
    real race opens. The IntegrityError, the rollback and the recovery are
    all real.
    """
    async with session_factory() as session:
        profile_repo = UserProfileRepository(session)
        preference_repo = UserPreferenceRepository(session)

        # The competing preferences row, committed by another connection
        # before this session writes anything (SQLite allows a single
        # writer, so the order matters).
        async with session_factory() as other:
            other.add(UserPreference(user_id=user_id))
            await other.commit()

        # A write made earlier in the same unit of work, still uncommitted.
        await profile_repo.create(user_id=user_id)

        real_get = preference_repo.get_by_user_id
        calls: list[object] = []

        async def get_by_user_id(uid):
            calls.append(uid)
            if len(calls) == 1:
                # The race window: the row is not visible to this SELECT yet.
                return None
            return await real_get(uid)

        with patch.object(preference_repo, "get_by_user_id", side_effect=get_by_user_id):
            preferences = await preference_repo.get_or_create_default(user_id)

        assert preferences.user_id == user_id
        assert preferences.id is not None

        # The earlier write survived the failed INSERT and is still committable.
        await session.commit()
        assert await _count(session_factory, UserProfile, user_id) == 1
        assert await _count(session_factory, UserPreference, user_id) == 1
