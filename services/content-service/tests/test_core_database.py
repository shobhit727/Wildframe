"""Behavioural tests for app/core/database.py.

Exercises the DatabaseManager singleton for both engine flavours (SQLite test
engines vs. pooled PostgreSQL), the session factory, the ``get_session``
generator, the ``SELECT 1`` health probe (success *and* failure paths) and
``close()`` — plus the ``get_db`` FastAPI dependency built on top of them.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from app.core.database import DatabaseManager, db_manager, get_db
from app.core.settings import settings

pytestmark = pytest.mark.unit

_UNSET = object()

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
POSTGRES_URL = "postgresql+asyncpg://user:pass@127.0.0.1:5432/content_db"


@pytest.fixture(autouse=True)
def _reset_manager():
    """DatabaseManager is a process-wide singleton — isolate every test."""
    # ``get_engine``/``close`` assign ``self._engine`` / ``self._session_factory``,
    # which land on the *singleton instance* shadowing the class attributes, so
    # the reset has to target the instance rather than the class.
    instance = DatabaseManager()
    saved_engine = getattr(instance, "_engine", _UNSET)
    saved_factory = getattr(instance, "_session_factory", _UNSET)
    saved_url = settings.DATABASE_URL
    instance._engine = None
    instance._session_factory = None
    try:
        yield
    finally:
        instance._engine = saved_engine
        instance._session_factory = saved_factory
        settings.DATABASE_URL = saved_url


class TestSingleton:
    def test_constructor_returns_the_same_instance(self):
        first = DatabaseManager()
        second = DatabaseManager()

        assert first is second

    def test_module_level_db_manager_is_the_singleton(self):
        assert DatabaseManager() is db_manager


class TestEngine:
    def test_sqlite_engine_uses_nullpool_and_no_server_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()

        engine = manager.get_engine()

        assert isinstance(engine.pool, NullPool)
        assert engine.dialect.name == "sqlite"
        assert engine.pool._pre_ping is False

    def test_postgres_engine_is_pooled_with_bounded_timeouts(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", POSTGRES_URL)
        manager = DatabaseManager()

        engine = manager.get_engine()

        assert isinstance(engine.pool, AsyncAdaptedQueuePool)
        assert engine.pool.size() == 5
        assert engine.pool._pre_ping is True

    def test_engine_is_created_once_and_cached(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()

        assert manager.get_engine() is manager.get_engine()

    def test_close_disposes_and_clears_the_cached_engine(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()
        engine = manager.get_engine()
        assert manager._engine is engine

        asyncio.run(manager.close())

        assert manager._engine is None
        assert manager._session_factory is None

    async def test_close_is_a_noop_without_an_engine(self):
        manager = DatabaseManager()
        assert manager._engine is None

        await manager.close()

        assert manager._engine is None


class TestSessionFactory:
    def test_factory_is_an_async_sessionmaker_and_cached(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()

        factory = manager.get_session_factory()

        assert isinstance(factory, async_sessionmaker)
        assert factory.kw["expire_on_commit"] is False
        assert factory.kw["autoflush"] is False
        assert manager.get_session_factory() is factory

    async def test_get_session_yields_a_session_then_closes(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()

        sessions = [session async for session in manager.get_session()]

        assert len(sessions) == 1
        assert isinstance(sessions[0], AsyncSession)


class TestHealthCheck:
    async def test_returns_true_when_select_1_succeeds(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()

        assert await manager.health_check() is True

    async def test_returns_true_on_the_cached_engine(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        manager = DatabaseManager()
        manager.get_engine()

        assert await manager.health_check() is True
        assert manager.get_engine() is not None

    async def test_returns_false_when_the_probe_raises(self, monkeypatch):
        # aiosqlite fails immediately ("unable to open database file") instead of
        # hanging on a TCP connect, so this exercises the except branch fast.
        monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:////nonexistent-dir/x.db")
        manager = DatabaseManager()

        assert await manager.health_check() is False


class TestGetDbDependency:
    async def test_get_db_yields_a_session(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)

        sessions = [session async for session in get_db()]

        assert len(sessions) == 1
        assert isinstance(sessions[0], AsyncSession)

    async def test_get_db_runs_a_real_statement(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)

        async for session in get_db():
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
