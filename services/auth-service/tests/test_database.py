"""Regression tests for the Auth Service database layer.

Covers the fix for ``DatabaseManager.health_check``: the previous
implementation passed a ``lambda`` to ``conn.execute`` (``conn.execute(lambda:
"SELECT 1")``) which is not a valid SQLAlchemy statement and would always
raise, making the health check fail and preventing the app from starting.
"""

import pytest
from app.core.database import DatabaseManager
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def tmp_engine(tmp_path):
    """Create a throwaway async SQLite engine for the duration of a test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    yield engine
    await engine.dispose()


@pytest.mark.unit
async def test_health_check_returns_true_against_working_engine():
    """``health_check`` must succeed when the database is reachable.

    The old buggy body was ``await conn.execute(lambda: "SELECT 1")`` which
    raises at execution time, so the check returned ``False`` even against a
    perfectly healthy database. After the fix it issues a real ``text()``
    statement over a transaction and returns ``True``.
    """
    # Point the singleton engine at a fresh in-memory database.
    DatabaseManager._engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    DatabaseManager._session_factory = None

    try:
        assert await DatabaseManager.health_check() is True
    finally:
        await DatabaseManager.close()


@pytest.mark.unit
def test_health_check_uses_text_statement_not_lambda():
    """Guard against the lambda regression at the source-text level.

    Read the source of ``health_check`` and assert it executes a compiled SQL
    text statement rather than calling a Python lambda. This catches a
    re-introduction of the bug without needing a live database connection.
    """
    import inspect

    source = inspect.getsource(DatabaseManager.health_check)
    assert "conn.execute(text(" in source, (
        "health_check must execute a sqlalchemy.text() statement; the lambda "
        "form ``conn.execute(lambda: ...)`` is invalid and always raises."
    )
    assert "lambda:" not in source, "health_check must not pass a lambda to conn.execute."


# ==========================================================================
# DatabaseManager engine/session lifecycle
# ==========================================================================

from unittest.mock import AsyncMock, MagicMock, patch

from app.core import database as database_module
from app.core.database import DatabaseManager, get_db, get_db_session
from app.core.settings import settings
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool


@pytest.fixture
def clean_manager():
    """Restore the DatabaseManager singletons around a test."""
    saved_engine = DatabaseManager._engine
    saved_factory = DatabaseManager._session_factory
    DatabaseManager._engine = None
    DatabaseManager._session_factory = None
    yield
    DatabaseManager._engine = saved_engine
    DatabaseManager._session_factory = saved_factory


def test_get_engine_builds_a_sqlite_engine_with_nullpool(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./local.db")

    engine = DatabaseManager.get_engine()

    assert isinstance(engine.pool, NullPool)
    assert engine.url.get_backend_name() == "sqlite"
    # The engine is cached, not rebuilt.
    assert DatabaseManager.get_engine() is engine


def test_get_engine_caches_the_instance(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./cache.db")

    assert DatabaseManager.get_engine() is DatabaseManager.get_engine()


def test_get_engine_uses_a_capped_queue_pool_for_postgres(clean_manager, monkeypatch):
    """Postgres must be pooled too — NullPool is only for SQLite test engines."""
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+asyncpg://user:pw@localhost:5432/auth_db",
    )
    monkeypatch.setattr(settings, "DEBUG", False)

    engine = DatabaseManager.get_engine()

    assert not isinstance(engine.pool, NullPool)
    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 5


def test_get_engine_applies_statement_timeouts_for_postgres(clean_manager, monkeypatch):
    """The 10s statement / 5s lock caps ride in ``connect_args.server_settings``."""
    captured = {}

    def _fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock(name="engine")

    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+asyncpg://user:pw@localhost:5432/auth_db",
    )
    monkeypatch.setattr(database_module, "create_async_engine", _fake_create_engine)

    DatabaseManager.get_engine()

    server_settings = captured["kwargs"]["connect_args"]["server_settings"]
    assert server_settings["statement_timeout"] == "10000"
    assert server_settings["lock_timeout"] == "5000"
    assert server_settings["idle_in_transaction_session_timeout"] == "30000"
    assert captured["kwargs"]["connect_args"]["command_timeout"] == 30
    assert captured["kwargs"]["pool_pre_ping"] is True


def test_get_engine_uses_a_sqlite_timeout_connect_arg(clean_manager, monkeypatch):
    captured = {}

    def _fake_create_engine(url, **kwargs):
        captured["kwargs"] = kwargs
        return MagicMock(name="engine")

    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    monkeypatch.setattr(database_module, "create_async_engine", _fake_create_engine)

    DatabaseManager.get_engine()

    assert captured["kwargs"]["connect_args"] == {"timeout": 10}
    assert captured["kwargs"]["pool_pre_ping"] is False


def test_get_engine_asserts_when_database_url_is_missing(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", None)

    with pytest.raises(AssertionError, match="DATABASE_URL is not configured"):
        DatabaseManager.get_engine()


def test_get_session_factory_is_cached_and_configured(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./factory.db")

    factory = DatabaseManager.get_session_factory()

    assert DatabaseManager.get_session_factory() is factory
    assert factory.kw["expire_on_commit"] is False
    assert factory.kw["autoflush"] is False
    assert factory.kw["autocommit"] is False


async def test_get_session_yields_a_session_and_closes_it(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./session.db")
    factory = MagicMock()
    session = MagicMock()
    session.close = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(DatabaseManager, "get_session_factory", classmethod(lambda cls: factory))

    yielded = []
    async for value in DatabaseManager.get_session():
        yielded.append(value)

    assert yielded == [session]
    session.close.assert_awaited_once()


async def test_get_session_rolls_back_on_sqlalchemy_error(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./rollback.db")
    session = MagicMock()
    session.close = AsyncMock()
    session.rollback = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    monkeypatch.setattr(DatabaseManager, "get_session_factory", classmethod(lambda cls: factory))

    generator = DatabaseManager.get_session()
    await generator.__anext__()
    with pytest.raises(SQLAlchemyError, match="statement failed"):
        await generator.athrow(SQLAlchemyError("statement failed"))

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_health_check_returns_false_when_the_engine_explodes(clean_manager):
    engine = MagicMock()
    engine.begin = MagicMock(side_effect=RuntimeError("no route to host"))
    DatabaseManager._engine = engine

    assert await DatabaseManager.health_check() is False


async def test_health_check_returns_false_on_a_sqlalchemy_error(clean_manager):
    engine = MagicMock()
    engine.begin = MagicMock(side_effect=SQLAlchemyError("server closed the connection"))
    DatabaseManager._engine = engine

    assert await DatabaseManager.health_check() is False


async def test_health_check_runs_select_one_over_a_transaction(clean_manager, tmp_path):
    """The check must execute a real compiled statement, not a Python lambda."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hc.db")
    DatabaseManager._engine = engine
    executed = []
    original_connect = type(engine).connect

    def _spy(self, *args, **kwargs):
        executed.append("connect")
        return original_connect(self, *args, **kwargs)

    try:
        with patch.object(type(engine), "connect", _spy):
            assert await DatabaseManager.health_check() is True
    finally:
        await engine.dispose()

    assert executed == ["connect"]
    assert str(text("SELECT 1")) == "SELECT 1"


async def test_close_disposes_and_clears_both_caches(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./close.db")
    engine = DatabaseManager.get_engine()
    DatabaseManager.get_session_factory()
    dispose = AsyncMock()
    with patch.object(type(engine), "dispose", dispose):
        await DatabaseManager.close()

    dispose.assert_awaited_once()
    assert DatabaseManager._engine is None
    assert DatabaseManager._session_factory is None


async def test_close_is_a_noop_without_an_engine(clean_manager):
    await DatabaseManager.close()

    assert DatabaseManager._engine is None


async def test_get_db_session_alias_delegates_to_get_session(clean_manager, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./alias.db")
    seen = []

    async def _fake_get_session():
        seen.append("called")
        session = MagicMock()
        session.close = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        yield session

    monkeypatch.setattr(DatabaseManager, "get_session", classmethod(lambda cls: _fake_get_session()))

    async for _ in get_db_session():
        pass

    assert seen == ["called"]


def test_get_db_is_the_same_callable_as_get_db_session():
    """API routes declare ``Depends(get_db)``; the alias must point at the
    same dependency-injection generator."""
    assert get_db is get_db_session
    assert get_db is database_module.get_db
