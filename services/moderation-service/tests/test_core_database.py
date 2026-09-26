"""Behavioural tests for ``app/core/database.py`` (moderation-service).

``DatabaseManager.health_check`` differs from the admin-service one: it lazily
calls ``init()`` when no engine exists and swallows every failure, so the
interesting paths are "boots a missing engine itself" and "returns False
rather than propagating". ``get_db`` is the dependency every route resolves
its session through.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database as db_mod
from app.core.database import DatabaseManager, get_db
from app.core.settings import settings


@pytest.fixture
async def manager(monkeypatch):
    """A DatabaseManager bound to a throwaway aiosqlite engine."""
    real_create = db_mod.create_async_engine

    def wrapper(url, **kwargs):
        if str(url).startswith("sqlite"):
            # init() hardcodes asyncpg pool + connect_args; drop them.
            for key in ("pool_size", "max_overflow", "pool_timeout", "connect_args"):
                kwargs.pop(key, None)
        return real_create(url, **kwargs)

    monkeypatch.setattr(db_mod, "create_async_engine", wrapper)
    monkeypatch.setattr(
        db_mod.settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:", raising=False
    )
    saved = (DatabaseManager.engine, DatabaseManager.session_factory)
    try:
        yield
    finally:
        engine = DatabaseManager.engine
        if engine is not None and hasattr(engine, "dispose"):
            await engine.dispose()
        DatabaseManager.engine, DatabaseManager.session_factory = saved


class _ExplodingEngine:
    def connect(self):
        raise RuntimeError("connection refused")


class _CloseTrackingSession(AsyncSession):
    closed = False

    async def close(self) -> None:
        type(self).closed = True
        await super().close()


class TestInit:
    async def test_init_builds_engine_and_factory(self, manager):
        await DatabaseManager.init()
        assert DatabaseManager.engine is not None
        assert DatabaseManager.session_factory is not None
        assert await DatabaseManager.health_check() is True

    async def test_init_applies_the_pool_and_timeout_hardening(self, manager, monkeypatch):
        captured = {}

        def fake_create_async_engine(url, **kwargs):
            captured.update(kwargs)
            captured["url"] = url
            return object()

        monkeypatch.setattr(db_mod, "create_async_engine", fake_create_async_engine)
        await DatabaseManager.init()
        assert captured["url"] == settings.DATABASE_URL
        assert captured["echo"] is False
        assert captured["pool_pre_ping"] is True
        assert captured["pool_size"] == 5
        assert captured["max_overflow"] == 5
        assert captured["pool_timeout"] == 30
        assert captured["pool_recycle"] == 3600
        server_settings = captured["connect_args"]["server_settings"]
        assert server_settings == {
            "statement_timeout": "10000",
            "lock_timeout": "5000",
            "idle_in_transaction_session_timeout": "30000",
        }
        assert captured["connect_args"]["command_timeout"] == 30

    async def test_factory_does_not_expire_committed_objects(self, manager):
        await DatabaseManager.init()
        assert DatabaseManager.session_factory.kw["expire_on_commit"] is False


class TestHealthCheck:
    async def test_initialises_a_missing_engine_on_first_probe(self, manager):
        DatabaseManager.engine = None
        DatabaseManager.session_factory = None
        assert await DatabaseManager.health_check() is True
        assert DatabaseManager.engine is not None

    async def test_returns_false_when_the_engine_cannot_connect(self):
        saved = DatabaseManager.engine
        DatabaseManager.engine = _ExplodingEngine()
        try:
            assert await DatabaseManager.health_check() is False
        finally:
            DatabaseManager.engine = saved

    async def test_returns_false_when_initialisation_itself_fails(self, monkeypatch):
        def boom(url, **kwargs):
            raise RuntimeError("cannot create engine")

        monkeypatch.setattr(db_mod, "create_async_engine", boom)
        saved = (DatabaseManager.engine, DatabaseManager.session_factory)
        DatabaseManager.engine = None
        DatabaseManager.session_factory = None
        try:
            assert await DatabaseManager.health_check() is False
        finally:
            DatabaseManager.engine, DatabaseManager.session_factory = saved

    async def test_runs_a_real_select_one(self, manager):
        await DatabaseManager.init()
        async with DatabaseManager.engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1


class _DisposeSpy:
    """Engine stand-in that records whether ``dispose()`` was awaited."""

    def __init__(self):
        self.disposed = False

    async def dispose(self):
        self.disposed = True


class TestClose:
    async def test_close_disposes_the_engine(self, manager):
        await DatabaseManager.init()
        spy = _DisposeSpy()
        DatabaseManager.engine = spy
        await DatabaseManager.close()
        assert spy.disposed is True

    async def test_close_leaves_the_factory_in_place(self, manager):
        await DatabaseManager.init()
        factory = DatabaseManager.session_factory
        await DatabaseManager.close()
        assert DatabaseManager.session_factory is factory

    async def test_close_is_a_noop_without_an_engine(self):
        saved = DatabaseManager.engine
        DatabaseManager.engine = None
        try:
            assert await DatabaseManager.close() is None
        finally:
            DatabaseManager.engine = saved


class TestGetDb:
    async def test_yields_a_usable_session(self, manager):
        await DatabaseManager.init()
        generator = get_db()
        session = await generator.__anext__()
        try:
            assert isinstance(session, AsyncSession)
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
        finally:
            with pytest.raises(StopAsyncIteration):
                await generator.__anext__()

    async def test_initialises_the_factory_when_absent(self, manager):
        DatabaseManager.engine = None
        DatabaseManager.session_factory = None
        generator = get_db()
        session = await generator.__anext__()
        try:
            assert isinstance(session, AsyncSession)
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
        finally:
            with pytest.raises(StopAsyncIteration):
                await generator.__anext__()

    async def test_closes_the_session_on_generator_exit(self, manager):
        await DatabaseManager.init()
        _CloseTrackingSession.closed = False
        DatabaseManager.session_factory = lambda: _CloseTrackingSession(
            DatabaseManager.engine, expire_on_commit=False
        )
        generator = get_db()
        await generator.__anext__()
        assert _CloseTrackingSession.closed is False
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()
        assert _CloseTrackingSession.closed is True

    async def test_writes_and_reads_through_one_session(self, manager):
        await DatabaseManager.init()
        generator = get_db()
        session = await generator.__anext__()
        try:
            await session.execute(text("CREATE TABLE probe (v INTEGER)"))
            await session.execute(text("INSERT INTO probe VALUES (5)"))
            await session.commit()
            assert (await session.execute(text("SELECT v FROM probe"))).scalar() == 5
        finally:
            with pytest.raises(StopAsyncIteration):
                await generator.__anext__()
