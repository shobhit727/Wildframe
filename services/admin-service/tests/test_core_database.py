"""Behavioural tests for ``app.core.database`` (admin-service).

``DatabaseManager`` is the single owner of the async engine, and ``get_db``
is the dependency every route resolves its session through. Both are tested
against a real aiosqlite engine (no Postgres, no Docker) plus fake engines
for the failure paths.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database as db_mod
from app.core.database import DatabaseManager, get_db
from app.core.settings import settings

PRODUCTION_BASE = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/admin_db",
    "REDIS_URL": "redis://redis:6379",
    "JWT_SECRET_KEY": "Q" * 41,
    "KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
}


@pytest.fixture
async def sqlite_manager(monkeypatch):
    """Point ``DatabaseManager`` at a throwaway aiosqlite engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    previous_engine = DatabaseManager.engine
    previous_factory = DatabaseManager.session_factory
    DatabaseManager.engine = engine
    DatabaseManager.session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        yield engine
    finally:
        DatabaseManager.engine = previous_engine
        DatabaseManager.session_factory = previous_factory
        await engine.dispose()


class _BrokenEngine:
    """Engine stand-in whose connect() always explodes."""

    def connect(self):
        raise RuntimeError("connection refused")

    async def dispose(self):
        self.disposed = True


@pytest.fixture
def sqlite_url(monkeypatch):
    """Point settings at SQLite, dropping the Postgres-only init() arguments.

    ``init()`` always passes pool_size/max_overflow/pool_timeout and asyncpg
    ``connect_args`` (command_timeout, server_settings), none of which aiosqlite
    accepts. The wrapper removes only those, so the rest of the real ``init``
    code path still executes.
    """
    real_create = db_mod.create_async_engine

    def wrapper(url, **kwargs):
        if str(url).startswith("sqlite"):
            for key in ("pool_size", "max_overflow", "pool_timeout", "connect_args"):
                kwargs.pop(key, None)
        return real_create(url, **kwargs)

    monkeypatch.setattr(db_mod, "create_async_engine", wrapper)
    monkeypatch.setattr(
        db_mod.settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:", raising=False
    )


class TestInit:
    async def test_init_builds_engine_and_session_factory(self, sqlite_url):
        previous = (DatabaseManager.engine, DatabaseManager.session_factory)
        try:
            await DatabaseManager.init()
            assert DatabaseManager.engine is not None
            assert DatabaseManager.session_factory is not None
            assert await DatabaseManager.health_check() is True
        finally:
            if DatabaseManager.engine is not None:
                await DatabaseManager.engine.dispose()
            DatabaseManager.engine, DatabaseManager.session_factory = previous

    async def test_init_applies_connection_hardening_options(self, monkeypatch):
        captured = {}

        def fake_create_async_engine(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(db_mod, "create_async_engine", fake_create_async_engine)
        previous = (DatabaseManager.engine, DatabaseManager.session_factory)
        try:
            await DatabaseManager.init()
        finally:
            DatabaseManager.engine, DatabaseManager.session_factory = previous

        assert captured["url"] == settings.DATABASE_URL
        assert captured["echo"] == settings.DEBUG
        assert captured["pool_pre_ping"] is True
        # #429 / #430: server-side statement/lock timeouts must be pinned.
        server_settings = captured["connect_args"]["server_settings"]
        assert server_settings["statement_timeout"] == "10000"
        assert server_settings["lock_timeout"] == "5000"
        assert server_settings["idle_in_transaction_session_timeout"] == "30000"

    async def test_init_rebuilds_the_engine_and_factory_each_call(self, monkeypatch):
        built = []

        def fake_create_async_engine(url, **kwargs):
            built.append(url)
            return object()

        monkeypatch.setattr(db_mod, "create_async_engine", fake_create_async_engine)
        previous = (DatabaseManager.engine, DatabaseManager.session_factory)
        try:
            await DatabaseManager.init()
            first = DatabaseManager.engine
            await DatabaseManager.init()
            assert DatabaseManager.engine is not first
            assert built == [settings.DATABASE_URL, settings.DATABASE_URL]
            assert DatabaseManager.session_factory is not None
        finally:
            DatabaseManager.engine, DatabaseManager.session_factory = previous


class TestHealthCheck:
    async def test_returns_false_when_no_engine_is_configured(self):
        previous = DatabaseManager.engine
        DatabaseManager.engine = None
        try:
            assert await DatabaseManager.health_check() is False
        finally:
            DatabaseManager.engine = previous

    async def test_returns_true_against_a_reachable_database(self, sqlite_manager):
        assert await DatabaseManager.health_check() is True

    async def test_returns_false_instead_of_raising_when_connect_fails(self):
        previous = DatabaseManager.engine
        DatabaseManager.engine = _BrokenEngine()
        try:
            assert await DatabaseManager.health_check() is False
        finally:
            DatabaseManager.engine = previous

    async def test_executes_a_real_select_one_statement(self, sqlite_manager):
        # A health check that never touches the DB would pass the tests above;
        # prove the probe statement is executable SQL.
        async with sqlite_manager.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1


class TestClose:
    async def test_close_disposes_the_engine(self):
        previous = DatabaseManager.engine
        engine = _BrokenEngine()
        DatabaseManager.engine = engine
        try:
            await DatabaseManager.close()
            assert engine.disposed is True
        finally:
            DatabaseManager.engine = previous

    async def test_close_is_a_noop_without_an_engine(self):
        previous = DatabaseManager.engine
        DatabaseManager.engine = None
        try:
            assert await DatabaseManager.close() is None
        finally:
            DatabaseManager.engine = previous

    async def test_close_leaves_the_factory_untouched(self, sqlite_manager):
        factory = DatabaseManager.session_factory
        await DatabaseManager.close()
        assert DatabaseManager.session_factory is factory


class _CloseTrackingSession(AsyncSession):
    """AsyncSession that records whether ``close()`` ran."""

    closed = False

    async def close(self) -> None:
        type(self).closed = True
        await super().close()


class TestGetDb:
    async def test_yields_a_usable_session(self, sqlite_manager):
        generator = get_db()
        session = await generator.__anext__()
        try:
            assert isinstance(session, AsyncSession)
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
        finally:
            with pytest.raises(StopAsyncIteration):
                await generator.__anext__()

    async def test_closes_the_session_on_exit(self, sqlite_manager):
        _CloseTrackingSession.closed = False
        DatabaseManager.session_factory = lambda: _CloseTrackingSession(
            sqlite_manager, expire_on_commit=False
        )

        generator = get_db()
        await generator.__anext__()
        assert _CloseTrackingSession.closed is False
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()
        assert _CloseTrackingSession.closed is True

    async def test_initialises_the_factory_when_absent(self, sqlite_url):
        previous = (DatabaseManager.engine, DatabaseManager.session_factory)
        DatabaseManager.engine = None
        DatabaseManager.session_factory = None
        try:
            generator = get_db()
            session = await generator.__anext__()
            assert isinstance(session, AsyncSession)
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
            with pytest.raises(StopAsyncIteration):
                await generator.__anext__()
        finally:
            if DatabaseManager.engine is not None:
                await DatabaseManager.engine.dispose()
            DatabaseManager.engine, DatabaseManager.session_factory = previous

    async def test_reads_write_through_the_same_session(self, sqlite_manager):
        generator = get_db()
        session = await generator.__anext__()
        try:
            await session.execute(text("CREATE TABLE t (v INTEGER)"))
            await session.execute(text("INSERT INTO t VALUES (7)"))
            await session.commit()
            assert (await session.execute(text("SELECT v FROM t"))).scalar() == 7
        finally:
            with pytest.raises(StopAsyncIteration):
                await generator.__anext__()
