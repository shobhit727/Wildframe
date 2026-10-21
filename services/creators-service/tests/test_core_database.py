"""Behavioural tests for app/core/database.py (creators-service).

``DatabaseManager`` is a class-level singleton around a lazily created async
engine: SQLite URLs get a ``NullPool`` and no server settings, PostgreSQL gets a
bounded queue pool plus statement/lock/idle-in-transaction timeouts. The
``SELECT 1`` health probe is exercised on both the success and failure path, and
``get_db`` is verified to hand out a usable session.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from app.core.database import DatabaseManager, get_db
from app.core.settings import settings

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
POSTGRES_URL = "postgresql+asyncpg://user:pass@127.0.0.1:5432/creators_db"
UNWRITABLE_SQLITE_URL = "sqlite+aiosqlite:////nonexistent-dir/creators.db"


@pytest.fixture(autouse=True)
def _reset_manager():
    saved_engine = DatabaseManager.engine
    saved_factory = DatabaseManager.session_factory
    saved_url = settings.DATABASE_URL
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    try:
        yield
    finally:
        DatabaseManager.engine = saved_engine
        DatabaseManager.session_factory = saved_factory
        settings.DATABASE_URL = saved_url


class TestInit:
    async def test_sqlite_engine_uses_nullpool_without_server_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)

        await DatabaseManager.init()

        engine = DatabaseManager.engine
        assert engine is not None
        assert engine.dialect.name == "sqlite"
        assert isinstance(engine.pool, NullPool)
        assert engine.pool._pre_ping is False

    async def test_sqlite_session_factory_is_created(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)

        await DatabaseManager.init()

        assert DatabaseManager.session_factory is not None
        assert DatabaseManager.session_factory.kw["expire_on_commit"] is False

    async def test_postgres_engine_is_pooled_with_bounded_timeouts(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", POSTGRES_URL)

        await DatabaseManager.init()

        engine = DatabaseManager.engine
        assert engine.dialect.name == "postgresql"
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 5
        assert engine.pool._timeout == 30
        assert engine.pool._recycle == 3600
        assert engine.pool._pre_ping is True

    async def test_init_can_be_called_again_to_rebuild_the_engine(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        await DatabaseManager.init()
        first = DatabaseManager.engine

        await DatabaseManager.init()

        assert DatabaseManager.engine is not first


class TestClose:
    async def test_close_disposes_the_engine(self):
        disposed = []

        class FakeEngine:
            async def dispose(self):
                disposed.append(True)

        DatabaseManager.engine = FakeEngine()

        await DatabaseManager.close()

        assert disposed == [True]

    async def test_close_without_an_engine_is_a_noop(self):
        DatabaseManager.engine = None

        await DatabaseManager.close()

        assert DatabaseManager.engine is None

    async def test_close_of_a_real_sqlite_engine(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        await DatabaseManager.init()
        engine = DatabaseManager.engine

        await DatabaseManager.close()

        # The pooled connection is gone, so a fresh connect must open anew.
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1


class TestHealthCheck:
    async def test_returns_true_and_initialises_on_demand(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        assert DatabaseManager.engine is None

        assert await DatabaseManager.health_check() is True
        assert DatabaseManager.engine is not None

    async def test_reuses_an_existing_engine(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        await DatabaseManager.init()
        engine = DatabaseManager.engine

        assert await DatabaseManager.health_check() is True
        assert DatabaseManager.engine is engine

    async def test_returns_false_when_the_probe_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", UNWRITABLE_SQLITE_URL)

        assert await DatabaseManager.health_check() is False

    async def test_returns_false_when_connect_explodes(self, monkeypatch):
        class ExplodingEngine:
            def connect(self):
                raise RuntimeError("no connection")

        DatabaseManager.engine = ExplodingEngine()

        assert await DatabaseManager.health_check() is False


class TestGetDb:
    async def test_get_db_initialises_the_manager_when_needed(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        assert DatabaseManager.session_factory is None

        sessions = [session async for session in get_db()]

        assert len(sessions) == 1
        assert isinstance(sessions[0], AsyncSession)
        assert DatabaseManager.session_factory is not None

    async def test_get_db_yields_a_usable_session(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)

        async for session in get_db():
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1

    async def test_get_db_reuses_an_initialised_factory(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
        await DatabaseManager.init()
        factory = DatabaseManager.session_factory

        async for session in get_db():
            assert isinstance(session, AsyncSession)

        assert DatabaseManager.session_factory is factory
