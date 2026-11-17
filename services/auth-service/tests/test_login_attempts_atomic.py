"""Regression tests for #1122: failed-login counter must be incremented atomically.

The old implementation loaded the user with an unlocked SELECT and did
``user.login_attempts += 1`` in Python, so two concurrent failed logins could
both read N and both write N + 1, losing an increment and weakening lockout.
"""

import pytest
from app.models import User
from app.repositories import UserRepository
from sqlalchemy import event, select, update


def _capture_statements(engine):
    statements: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    return statements, lambda: event.remove(engine.sync_engine, "before_cursor_execute", _before)


async def test_increment_is_single_sql_side_update(test_engine, test_session, test_user):
    repo = UserRepository(test_session)
    statements, stop = _capture_statements(test_engine)
    try:
        user = await repo.increment_login_attempts(test_user.id)
        await repo.flush()
    finally:
        stop()

    writes = [s for s in statements if s.upper().startswith("UPDATE USERS")]
    assert len(writes) == 1, statements
    assert (
        "login_attempts=(users.login_attempts +" in writes[0]
    ), f"increment must be computed SQL-side, got: {writes[0]}"
    assert not any(
        s.upper().startswith("SELECT") for s in statements
    ), f"increment must not read-modify-write via a separate SELECT: {statements}"
    assert user.login_attempts == 1


async def test_interleaved_increments_do_not_lose_updates(
    test_session_factory, test_user, test_session
):
    """Two sessions read the user, then both increment: the count must be n + 2."""
    await test_session.commit()
    async with test_session_factory() as s1, test_session_factory() as s2:
        # Both callers have already loaded the row and hold it (as
        # AuthService.login does); the strong references keep each session's
        # identity map populated with the value read before either write.
        loaded1 = await s1.get(User, test_user.id)
        loaded2 = await s2.get(User, test_user.id)
        assert loaded1.login_attempts == loaded2.login_attempts == 0

        await UserRepository(s1).increment_login_attempts(test_user.id)
        await s1.commit()
        second = await UserRepository(s2).increment_login_attempts(test_user.id)
        await s2.commit()

    assert second.login_attempts == 2

    async with test_session_factory() as check:
        stored = (
            await check.execute(select(User.login_attempts).where(User.id == test_user.id))
        ).scalar_one()
    assert stored == 2


async def test_returned_identity_mapped_user_reflects_db_count(test_session, test_user):
    """The identity-mapped instance callers compare to the lockout threshold is fresh."""
    await test_session.execute(
        update(User)
        .where(User.id == test_user.id)
        .values(login_attempts=4)
        .execution_options(synchronize_session=False)
    )
    assert test_user.login_attempts == 0  # stale in-memory copy

    user = await UserRepository(test_session).increment_login_attempts(test_user.id)

    assert user is test_user
    assert user.login_attempts == 5


async def test_increment_for_inactive_user_raises(test_session, test_user):
    test_user.is_active = False
    await test_session.flush()
    with pytest.raises(ValueError, match="not found"):
        await UserRepository(test_session).increment_login_attempts(test_user.id)
