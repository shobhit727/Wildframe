"""Tests for ``streaming-service/app/core/database.py``.

Focus is the ``DatabaseManager`` singleton and its engine factory, which builds
materially different engines depending on the driver:

* SQLite URLs get ``NullPool`` and no pool sizing (asyncpg rejects the sync
  pool class), plus no ``command_timeout``/``server_settings`` connect args.
* PostgreSQL URLs get ``pool_size``/``max_overflow``/``pool_timeout``/
  ``pool_recycle``, ``pool_pre_ping``, and the bounded-lock server settings
  from #429/#430.

No PostgreSQL server is contacted: engine *construction* is inspected, and the
only connect is against a real in-memory SQLite database.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import DatabaseManager, db_manager, get_db, get_db_session
from app.core.settings import settings

PG_URL = "postgresql+asyncpg://app_user:s3cr3t@127.0.0.1:59999/streaming_db"
SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def reset_singleton():
    """Each test starts from a clean, engine-less singleton."""
    db_manager._engine = None
    db_manager._session_factory = None
    yield
    db_manager._engine = None
    db_manager._session_factory = None


# ------------------------------------------------------------- singleton ----


@pytest.mark.unit
def test_database_manager_is_a_singleton():
    """Constructing the manager twice must return the same object."""
    assert DatabaseManager() is db_manager
    assert DatabaseManager() is DatabaseManager()


# ----------------------------------------------------------- get_engine -----


@pytest.fixture
def engine_spy(monkeypatch):
    """Record the kwargs ``get_engine`` hands to ``create_async_engine``.

    The connect args are merged into a transient ``cparams`` mapping at
    ``create_engine`` time and never stored on the dialect, so the factory's
    contract is only observable at the call site.
    """
    import app.core.database as database_module

    calls: list[dict] = []
    real = database_module.create_async_engine

    def _spy(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return real(url, **kwargs)

    monkeypatch.setattr(database_module, "create_async_engine", _spy)
    return calls


@pytest.mark.unit
def test_get_engine_builds_postgres_pool_configuration(monkeypatch, engine_spy):
    """A postgres URL must get the sized, pre-pinged queue pool."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    monkeypatch.setattr(settings, "DEBUG", False)
    db_manager._engine = None

    engine = db_manager.get_engine()

    assert len(engine_spy) == 1
    kwargs = engine_spy[0]
    assert kwargs["url"] == PG_URL
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 5
    assert kwargs["pool_timeout"] == 30
    assert kwargs["pool_recycle"] == 3600
    assert kwargs["pool_pre_ping"] is True
    assert "poolclass" not in kwargs, "async engines must not take the sync pool class"
    assert kwargs["future"] is True
    assert kwargs["echo"] is False
    # The built engine really is the async-adapted queue pool.
    assert type(engine.pool).__name__ == "AsyncAdaptedQueuePool"
    assert engine.pool.size() == 5


@pytest.mark.unit
def test_get_engine_is_memoized(monkeypatch, engine_spy):
    """The second call must reuse the engine, not rebuild the pool."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    db_manager._engine = None
    first = db_manager.get_engine()
    second = db_manager.get_engine()
    assert first is second
    assert len(engine_spy) == 1


@pytest.mark.unit
def test_get_engine_uses_nullpool_for_sqlite(monkeypatch, engine_spy):
    """SQLite must use NullPool and no pool sizing kwargs."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None

    engine = db_manager.get_engine()

    kwargs = engine_spy[0]
    assert kwargs["poolclass"] is NullPool
    assert kwargs["pool_pre_ping"] is False
    for absent in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
        assert absent not in kwargs, f"{absent} is invalid for an async sqlite engine"
    assert isinstance(engine.pool, NullPool)


@pytest.mark.unit
def test_get_engine_passes_server_settings_lock_bounds(monkeypatch, engine_spy):
    """#429/#430: statement, lock and idle-in-transaction timeouts are set."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    db_manager._engine = None
    db_manager.get_engine()

    connect_args = engine_spy[0]["connect_args"]
    assert connect_args["command_timeout"] == 30
    assert connect_args["server_settings"] == {
        "statement_timeout": "10000",
        "lock_timeout": "5000",
        "idle_in_transaction_session_timeout": "30000",
    }


@pytest.mark.unit
def test_get_engine_omits_server_settings_for_sqlite(monkeypatch, engine_spy):
    """aiosqlite rejects the asyncpg-only connect args, so they must be absent."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None
    db_manager.get_engine()

    assert engine_spy[0]["connect_args"] == {}


@pytest.mark.unit
def test_get_engine_honours_debug_echo_flag(monkeypatch, engine_spy):
    """``echo`` is threaded from settings.DEBUG, not hardcoded."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    monkeypatch.setattr(settings, "DEBUG", False)
    db_manager._engine = None
    assert db_manager.get_engine().echo is False
    assert engine_spy[-1]["echo"] is False

    db_manager._engine = None
    monkeypatch.setattr(settings, "DEBUG", True)
    assert db_manager.get_engine().echo is True
    assert engine_spy[-1]["echo"] is True


# ------------------------------------------------- get_session_factory ------


@pytest.mark.unit
def test_get_session_factory_is_memoized_and_typed(monkeypatch):
    """The factory is built once, over AsyncSession, and cached."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None
    db_manager._session_factory = None

    factory = db_manager.get_session_factory()

    assert isinstance(factory, async_sessionmaker)
    assert factory.class_ is AsyncSession
    assert factory.kw["expire_on_commit"] is False
    assert factory.kw["autoflush"] is False
    assert db_manager.get_session_factory() is factory


# ----------------------------------------------------- health_check ---------


@pytest.mark.unit
async def test_health_check_runs_select_one(monkeypatch):
    """database.py:99-101: a real ``text("SELECT 1")`` must execute."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None
    assert await db_manager.health_check() is True


@pytest.mark.unit
async def test_health_check_returns_false_and_logs_on_failure(monkeypatch, caplog):
    """A dead server must be reported as False, not raised."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    db_manager._engine = None
    with caplog.at_level("ERROR", logger="app.core.database"):
        assert await db_manager.health_check() is False
    assert any("Database health check failed" in r.message for r in caplog.records)


# ---------------------------------------------------------- close -----------


@pytest.mark.unit
async def test_close_disposes_and_clears_state(monkeypatch):
    """Teardown must dispose the engine AND null the cached factory."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None
    db_manager.get_session_factory()
    assert db_manager._engine is not None
    assert db_manager._session_factory is not None

    await db_manager.close()

    assert db_manager._engine is None
    assert db_manager._session_factory is None


@pytest.mark.unit
async def test_close_is_a_noop_without_an_engine():
    """Closing a never-initialised manager must not raise."""
    db_manager._engine = None
    await db_manager.close()
    assert db_manager._engine is None


# ------------------------------------------------- get_db_session / get_db --


@pytest.mark.unit
async def test_get_db_session_yields_a_usable_session(monkeypatch):
    """The DI generator must hand out a working AsyncSession."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None
    db_manager.get_session_factory()

    gen = get_db_session()
    session = await gen.asend(None)
    assert isinstance(session, AsyncSession)
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)


@pytest.mark.unit
async def test_get_db_session_rolls_back_and_reraises(monkeypatch):
    """database.py:127-129: a body failure rolls back, then re-raises."""
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None
    db_manager.get_session_factory()

    gen = get_db_session()
    session = await gen.asend(None)
    rolled_back = False
    real_rollback = session.rollback

    async def _tracking_rollback():
        nonlocal rolled_back
        rolled_back = True
        await real_rollback()

    session.rollback = _tracking_rollback

    boom = RuntimeError("handler blew up")
    with pytest.raises(RuntimeError) as exc:
        await gen.athrow(boom)

    assert exc.value is boom
    assert rolled_back is True


@pytest.mark.unit
async def test_get_db_is_an_alias_of_get_db_session():
    """Routes declare ``Depends(get_db)``; it must be the same generator."""
    assert get_db is get_db_session


@pytest.mark.unit
async def test_manager_get_session_yields_a_session(monkeypatch):
    """``DatabaseManager.get_session`` is the older DI entry point.

    Routes may depend on either name, so both generators must work.
    """
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None

    gen = db_manager.get_session()
    session = await gen.asend(None)
    assert isinstance(session, AsyncSession)
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)


@pytest.mark.unit
async def test_manager_get_session_does_not_roll_back_on_error(monkeypatch):
    """``get_session`` is a bare ``async with`` -- no rollback-on-error guard.

    Documented here because ``get_db_session`` *does* roll back; the two DI
    entry points differ, which is worth pinning so the difference is visible
    if either is ever refactored.
    """
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    db_manager._engine = None

    gen = db_manager.get_session()
    session = await gen.asend(None)
    rolled_back = False
    real_rollback = session.rollback

    async def _tracking_rollback():
        nonlocal rolled_back
        rolled_back = True
        await real_rollback()

    session.rollback = _tracking_rollback

    with pytest.raises(ValueError):
        await gen.athrow(ValueError("no rollback path here"))

    assert rolled_back is False
