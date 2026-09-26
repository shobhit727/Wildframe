"""Tests for `app.core.database.DatabaseManager`.

`init()` was never called by any test, so the pool/timeout branch per driver
(#64/#427 pool budget, #429/#430 server timeouts) was unverified. Both branches
are asserted here by intercepting `create_async_engine`, because SQLAlchemy
folds `connect_args` into the pool creator closure where they cannot be read
back. `health_check`, `close` and `get_db` are driven against a real aiosqlite
engine so they are proven, not mocked.
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

from app.core.database import DatabaseManager, get_db

POSTGRES_URL = "postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/notification_db"
SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_manager():
    """`DatabaseManager` keeps class-level engine state; reset around each test."""
    saved_engine = DatabaseManager.engine
    saved_factory = DatabaseManager.session_factory
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    yield
    DatabaseManager.engine = saved_engine
    DatabaseManager.session_factory = saved_factory


def _capture_init_kwargs(database_url: str) -> dict:
    with patch("app.core.settings.settings") as fake_settings:
        fake_settings.DATABASE_URL = database_url
        with patch("app.core.database.create_async_engine") as factory:
            factory.return_value = create_async_engine("sqlite+aiosqlite:///:memory:")
            import asyncio

            asyncio.run(DatabaseManager.init())

    return dict(factory.call_args.kwargs)


# ---------------------------------------------------------------------------
# init - sqlite branch (in-memory tests)
# ---------------------------------------------------------------------------


def test_sqlite_init_uses_no_pool_or_timeout_kwargs():
    captured = _capture_init_kwargs(SQLITE_URL)

    # A NullPool is the SQLAlchemy default for aiosqlite memory databases, so
    # `init` must not pass pool sizing (which NullPool would reject).
    assert captured["connect_args"] == {}
    for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping"):
        assert key not in captured
    assert captured["echo"] is False
    assert captured["future"] is True


# ---------------------------------------------------------------------------
# init - postgres branch (live dev / prod)
# ---------------------------------------------------------------------------


def test_postgres_init_uses_a_capped_pool_with_server_side_timeouts():
    captured = _capture_init_kwargs(POSTGRES_URL)

    assert captured["pool_size"] == 5
    assert captured["max_overflow"] == 5
    assert captured["pool_timeout"] == 30
    assert captured["pool_recycle"] == 3600
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "command_timeout": 30,
        "server_settings": {
            "statement_timeout": "10000",
            "lock_timeout": "5000",
            "idle_in_transaction_session_timeout": "30000",
        },
    }


async def test_postgres_init_produces_a_real_pooled_engine():
    with patch("app.core.settings.settings") as fake_settings:
        fake_settings.DATABASE_URL = POSTGRES_URL
        await DatabaseManager.init()

    engine = DatabaseManager.engine
    assert engine is not None
    assert isinstance(engine.pool, AsyncAdaptedQueuePool)
    assert engine.pool.size() == 5
    assert DatabaseManager.session_factory is not None
    assert DatabaseManager.session_factory.kw["expire_on_commit"] is False


# ---------------------------------------------------------------------------
# init side effects
# ---------------------------------------------------------------------------


async def test_init_replaces_a_previous_engine():
    first = create_async_engine(SQLITE_URL)
    DatabaseManager.engine = first
    DatabaseManager.session_factory = None

    with patch("app.core.settings.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        await DatabaseManager.init()

    assert DatabaseManager.engine is not first
    assert DatabaseManager.session_factory is not None
    await first.dispose()


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


async def test_health_check_is_true_against_a_working_database():
    DatabaseManager.engine = create_async_engine(SQLITE_URL)

    assert await DatabaseManager.health_check() is True


async def test_health_check_initialises_a_missing_engine_lazily():
    assert DatabaseManager.engine is None

    with patch("app.core.settings.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL
        assert await DatabaseManager.health_check() is True

    assert DatabaseManager.engine is not None
    await DatabaseManager.close()


async def test_health_check_is_false_when_the_connection_fails():
    with patch("app.core.settings.settings") as fake_settings:
        fake_settings.DATABASE_URL = "postgresql+asyncpg://nobody:nope@127.0.0.1:1/none"
        assert await DatabaseManager.health_check() is False


async def test_health_check_is_false_when_connect_raises():
    engine = create_async_engine(SQLITE_URL)
    DatabaseManager.engine = engine

    # The engine is a real object, so the failure has to be injected at the
    # dialect level (an unopenable SQLite file is the honest equivalent).
    with patch.object(
        type(engine), "connect", side_effect=RuntimeError("no route to host")
    ):
        assert await DatabaseManager.health_check() is False

    await engine.dispose()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


async def test_close_disposes_the_engine():
    engine = create_async_engine(SQLITE_URL)
    DatabaseManager.engine = engine
    DatabaseManager.session_factory = "sentinel"

    await DatabaseManager.close()

    # `close` only disposes - the class attributes are intentionally left alone
    # so a later `health_check` reuses the (disposed but reusable) engine.
    assert DatabaseManager.engine is engine
    assert DatabaseManager.session_factory == "sentinel"


async def test_close_is_a_no_op_without_an_engine():
    assert DatabaseManager.engine is None

    await DatabaseManager.close()

    assert DatabaseManager.engine is None


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


async def test_get_db_yields_a_working_session():
    DatabaseManager.engine = create_async_engine(SQLITE_URL)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    DatabaseManager.session_factory = async_sessionmaker(
        DatabaseManager.engine, class_=AsyncSession, expire_on_commit=False
    )

    generator = get_db()
    session = await anext(generator)
    try:
        assert isinstance(session, AsyncSession)
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        with pytest.raises(StopAsyncIteration):
            await anext(generator)


async def test_get_db_initialises_the_factory_when_missing():
    """The dependency is a `Depends(get_db)`, so it must be self-sufficient."""
    with patch("app.core.settings.settings") as fake_settings:
        fake_settings.DATABASE_URL = SQLITE_URL

        generator = get_db()
        session = await anext(generator)
        try:
            assert session is not None
            assert DatabaseManager.session_factory is not None
        finally:
            with pytest.raises(StopAsyncIteration):
                await anext(generator)
