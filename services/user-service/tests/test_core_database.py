"""Behavioural tests for `app.core.database.DatabaseManager`.

Two drivers are exercised because `get_engine` branches on them:

* a `sqlite+aiosqlite://` URL -> `NullPool`, no `pool_*` kwargs, a connect
  `timeout` instead of asyncpg `server_settings`;
* a `postgresql+asyncpg://` URL -> pooled with the caps from #64/#427 and the
  server-side statement/lock timeouts from #429/#430.

The engine itself is a real async engine (lazily connected), so `health_check`
can be proven both green and red without a live server.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

from app.core.database import DatabaseManager, get_db, get_db_session

POSTGRES_URL = "postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/users_db"
SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_database_manager():
    """DatabaseManager holds process-wide class state; reset around each test."""
    DatabaseManager._engine = None
    DatabaseManager._session_factory = None
    yield
    DatabaseManager._engine = None
    DatabaseManager._session_factory = None


# ---------------------------------------------------------------------------
# get_engine - postgres branch
# ---------------------------------------------------------------------------


def test_get_engine_is_memoised():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = POSTGRES_URL
        fake_settings.DEBUG = False

        first = DatabaseManager.get_engine()
        second = DatabaseManager.get_engine()

    assert first is second
    # Real engine object: pooled for postgres, no connection is opened here.
    assert isinstance(first.pool, AsyncAdaptedQueuePool)
    assert first.pool.size() == 5


def test_postgres_engine_uses_a_capped_queue_pool_with_server_timeouts():
    captured = _capture_engine_kwargs(POSTGRES_URL)

    assert captured["pool_size"] == 5
    assert captured["max_overflow"] == 5
    assert captured["pool_timeout"] == 30
    assert captured["pool_recycle"] == 3600
    assert captured["pool_pre_ping"] is True
    assert "poolclass" not in captured  # pooled, not NullPool
    assert captured["connect_args"]["command_timeout"] == 30
    assert captured["connect_args"]["server_settings"] == {
        "statement_timeout": "10000",
        "lock_timeout": "5000",
        "idle_in_transaction_session_timeout": "30000",
    }


def test_sqlite_engine_uses_nullpool_and_no_pool_kwargs():
    captured = _capture_engine_kwargs(SQLITE_URL)

    assert captured["poolclass"] is NullPool
    # No pool sizing kwargs - NullPool has no pool to size.
    for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
        assert key not in captured
    assert captured["pool_pre_ping"] is False
    # SQLite has no server to time out; it gets a driver-level timeout instead.
    assert captured["connect_args"] == {"timeout": 10}


def test_engine_echo_follows_the_debug_flag():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = POSTGRES_URL
        fake_settings.DEBUG = True

        engine = DatabaseManager.get_engine()

    assert engine.echo is True


# ---------------------------------------------------------------------------
# get_session_factory
# ---------------------------------------------------------------------------


def test_get_session_factory_is_memoised_and_uses_async_session():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        first = DatabaseManager.get_session_factory()
        second = DatabaseManager.get_session_factory()

    assert first is second
    assert first.kw["expire_on_commit"] is False
    assert first.class_ is AsyncSession


def test_get_session_factory_shares_the_manager_engine():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        factory = DatabaseManager.get_session_factory()
        engine = DatabaseManager.get_engine()

    assert factory.kw["bind"] is engine


# ---------------------------------------------------------------------------
# get_session - commit, rollback on SQLAlchemyError, always close
# ---------------------------------------------------------------------------


async def test_get_session_yields_a_usable_session():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        generator = DatabaseManager.get_session()
        session = await anext(generator)
        try:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        finally:
            with pytest.raises(StopAsyncIteration):
                await anext(generator)


async def test_get_session_rolls_back_and_reraises_sqlalchemy_errors():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        generator = DatabaseManager.get_session()
        session = await anext(generator)
        with patch.object(session, "rollback", new=_noop()) as rollback:
            with pytest.raises(SQLAlchemyError):
                await generator.athrow(SQLAlchemyError("boom"))

    rollback.assert_awaited_once()


async def test_get_session_closes_the_session_on_normal_exhaustion():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        generator = DatabaseManager.get_session()
        session = await anext(generator)
        with patch.object(session, "close", new=_noop()) as close:
            with pytest.raises(StopAsyncIteration):
                await anext(generator)

    assert close.await_count >= 1


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


async def test_health_check_is_true_against_a_working_database():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        assert await DatabaseManager.health_check() is True


async def test_health_check_is_false_and_logs_when_the_engine_cannot_connect():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = "postgresql+asyncpg://nobody:nope@127.0.0.1:1/none"
        fake_settings.DEBUG = False

        with patch("app.core.database.logger") as logger:
            assert await DatabaseManager.health_check() is False

    assert logger.error.called


async def test_health_check_is_false_when_get_engine_raises():
    with patch.object(DatabaseManager, "get_engine", side_effect=RuntimeError("no engine")):
        with patch("app.core.database.logger") as logger:
            assert await DatabaseManager.health_check() is False

    assert logger.error.called


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


async def test_close_disposes_and_clears_the_engine():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False
        DatabaseManager.get_engine()
        DatabaseManager.get_session_factory()
        engine = DatabaseManager._engine

        await DatabaseManager.close()

    assert DatabaseManager._engine is None
    assert DatabaseManager._session_factory is None
    assert engine.sync_engine.pool is not None  # dispose() is safe to have run


async def test_close_is_a_no_op_when_no_engine_exists():
    assert DatabaseManager._engine is None

    await DatabaseManager.close()

    assert DatabaseManager._engine is None


# ---------------------------------------------------------------------------
# get_db_session / get_db aliases
# ---------------------------------------------------------------------------


async def test_get_db_session_reuses_the_manager_session():
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        fake_settings.DEBUG = False

        generator = get_db_session()
        session = await anext(generator)
        with pytest.raises(StopAsyncIteration):
            await anext(generator)

    assert session is not None


def test_get_db_is_an_alias_of_get_db_session():
    assert get_db is get_db_session


def _noop():
    from unittest.mock import AsyncMock

    return AsyncMock()


def _capture_engine_kwargs(database_url: str) -> dict:
    """Return the kwargs `get_engine` passes to `create_async_engine`.

    SQLAlchemy folds ``connect_args`` into the pool creator closure, so the only
    stable way to assert the pool/timeout configuration chosen per driver is to
    intercept the factory call.
    """
    with patch("app.core.database.settings") as fake_settings:
        fake_settings.DATABASE_URL = database_url
        fake_settings.DEBUG = False
        with patch("app.core.database.create_async_engine") as factory:
            DatabaseManager.get_engine()

    return dict(factory.call_args.kwargs)
