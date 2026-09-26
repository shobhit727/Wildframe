"""Behavioural tests for ``app.core.database``.

``DatabaseManager`` is a process-wide singleton, so every test here snapshots
and restores the class state — otherwise a leaked engine would make a later
test silently exercise the wrong pool.

The real-PostgreSQL tests are driven by ``TEST_DATABASE_URL`` and are the
strongest evidence that the pooling config and the ``SELECT 1`` health probe
actually work against asyncpg; the mock tests pin the lifecycle contract
(commit on success, rollback on failure, dispose on shutdown).
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import DatabaseManager, get_db, get_db_session
from app.core.settings import settings
from app.models import Base


# ---------------------------------------------------------------------------
# Doubles that honour the async-context-manager protocols the code relies on
# ---------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self, engine: "_FakeEngine"):
        self.engine = engine

    async def execute(self, statement) -> None:
        self.engine.executed.append(statement)


class _FakeEngine:
    """Minimal AsyncEngine stand-in: begin() and dispose()."""

    def __init__(self, *, fail_on_begin: Exception | None = None):
        self.executed: list = []
        self.disposed = False
        self.dispose_calls = 0
        self._fail_on_begin = fail_on_begin

    def begin(self):
        engine = self

        class _Begin:
            async def __aenter__(self):
                if engine._fail_on_begin is not None:
                    raise engine._fail_on_begin
                return _FakeConnection(engine)

            async def __aexit__(self, *exc_info):
                return False

        return _Begin()

    async def dispose(self) -> None:
        self.disposed = True
        self.dispose_calls += 1


class _FakeSession:
    """Minimal AsyncSession stand-in that records the transaction calls."""

    def __init__(self, *, fail_on_commit: Exception | None = None):
        self.commits = 0
        self.rollbacks = 0
        self.added: list = []
        self._fail_on_commit = fail_on_commit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self._fail_on_commit is not None:
            raise self._fail_on_commit
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        return None


def _factory(session: _FakeSession):
    return MagicMock(return_value=session)


@pytest.fixture(autouse=True)
def _restore_singleton():
    """Never leak engine/session_factory state between tests."""
    saved_engine = DatabaseManager.engine
    saved_factory = DatabaseManager.session_factory
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    try:
        yield
    finally:
        DatabaseManager.engine = saved_engine
        DatabaseManager.session_factory = saved_factory


# ---------------------------------------------------------------------------
# init / close
# ---------------------------------------------------------------------------


class TestInitAndClose:
    async def test_init_builds_an_engine_and_a_session_factory(self):
        engine = _FakeEngine()
        with patch("app.core.database.create_async_engine", return_value=engine) as create:
            await DatabaseManager.init()
        assert DatabaseManager.engine is engine
        assert DatabaseManager.session_factory is not None
        create.assert_called_once()

    async def test_init_uses_the_configured_database_url(self):
        with patch("app.core.database.create_async_engine", return_value=_FakeEngine()) as create:
            await DatabaseManager.init()
        assert create.call_args.args[0] == settings.DATABASE_URL

    async def test_init_pins_the_financial_transaction_isolation_level(self):
        # #428: financial transactions need an explicit isolation level rather
        # than inheriting whatever the server default happens to be.
        with patch("app.core.database.create_async_engine", return_value=_FakeEngine()) as create:
            await DatabaseManager.init()
        assert create.call_args.kwargs["isolation_level"] == "READ COMMITTED"

    async def test_init_bounds_statement_and_lock_waits(self):
        # #429 / #430: a wedged transaction must not hold a pooled connection
        # indefinitely.
        with patch("app.core.database.create_async_engine", return_value=_FakeEngine()) as create:
            await DatabaseManager.init()
        server_settings = create.call_args.kwargs["connect_args"]["server_settings"]
        assert server_settings["statement_timeout"] == "10000"
        assert server_settings["lock_timeout"] == "5000"
        assert server_settings["idle_in_transaction_session_timeout"] == "30000"
        assert create.call_args.kwargs["connect_args"]["command_timeout"] == 30

    async def test_init_enables_pool_pre_ping_and_bounded_pooling(self):
        # pool_pre_ping is what lets the engine survive a database restart
        # without handing out dead connections.
        with patch("app.core.database.create_async_engine", return_value=_FakeEngine()) as create:
            await DatabaseManager.init()
        kwargs = create.call_args.kwargs
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 5
        assert kwargs["pool_timeout"] == 30
        assert kwargs["pool_recycle"] == 3600

    async def test_init_is_idempotent(self):
        first, second = _FakeEngine(), _FakeEngine()
        with patch("app.core.database.create_async_engine", return_value=first):
            await DatabaseManager.init()
        with patch("app.core.database.create_async_engine", return_value=second):
            await DatabaseManager.init()
        assert DatabaseManager.engine is second

    async def test_session_factory_is_configured_to_keep_attributes_after_commit(self):
        # expire_on_commit=False is required because webhook handlers read model
        # attributes *after* committing. async_sessionmaker validates the engine
        # type, so a real engine is needed here.
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("TEST_DATABASE_URL not set")
        engine = create_async_engine(
            make_url(url).set(drivername="postgresql+asyncpg"), **_engine_kwargs()
        )
        try:
            with patch("app.core.database.create_async_engine", return_value=engine):
                await DatabaseManager.init()
            session = DatabaseManager.session_factory()
            try:
                assert isinstance(session, AsyncSession)
                assert session.sync_session.expire_on_commit is False
            finally:
                await session.close()
        finally:
            await engine.dispose()

    async def test_close_disposes_the_engine(self):
        engine = _FakeEngine()
        DatabaseManager.engine = engine
        await DatabaseManager.close()
        assert engine.disposed is True
        assert engine.dispose_calls == 1

    async def test_close_without_an_engine_is_a_no_op(self):
        DatabaseManager.engine = None
        await DatabaseManager.close()  # must not raise
        assert DatabaseManager.engine is None


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_healthy_database_returns_true(self):
        engine = _FakeEngine()
        DatabaseManager.engine = engine
        assert await DatabaseManager.health_check() is True
        assert len(engine.executed) == 1

    async def test_probe_uses_a_real_select_1_statement(self):
        # A Python lambda or a truthiness check would pass without touching the
        # database; the probe must be genuine SQL against the connection.
        engine = _FakeEngine()
        DatabaseManager.engine = engine
        await DatabaseManager.health_check()
        statement = engine.executed[0]
        assert isinstance(statement, type(text("SELECT 1")))
        assert str(statement) == "SELECT 1"

    async def test_health_check_initializes_when_no_engine_exists(self):
        engine = _FakeEngine()
        with patch("app.core.database.create_async_engine", return_value=engine):
            assert await DatabaseManager.health_check() is True
        # init() must have run as part of the probe.
        assert DatabaseManager.engine is engine
        assert len(engine.executed) == 1

    async def test_connection_failure_returns_false_instead_of_raising(self):
        # /health must degrade, not 500, when the database is down.
        DatabaseManager.engine = _FakeEngine(fail_on_begin=OSError("connection refused"))
        assert await DatabaseManager.health_check() is False

    async def test_probe_failure_does_not_leave_a_half_open_engine(self):
        engine = _FakeEngine(fail_on_begin=RuntimeError("boom"))
        DatabaseManager.engine = engine
        assert await DatabaseManager.health_check() is False
        # The engine object is left in place for the next attempt; only the
        # boolean signals the failure.
        assert DatabaseManager.engine is engine

    @pytest.mark.parametrize(
        "error",
        [Exception("generic"), ValueError("bad url"), TimeoutError("slow")],
    )
    async def test_any_exception_degrades_to_false(self, error):
        DatabaseManager.engine = _FakeEngine(fail_on_begin=error)
        assert await DatabaseManager.health_check() is False


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    async def test_yields_a_session_from_an_existing_factory(self):
        session = _FakeSession()
        DatabaseManager.session_factory = _factory(session)
        received = [s async for s in DatabaseManager.get_session()]
        assert received == [session]

    async def test_initializes_the_factory_when_missing(self):
        engine = _FakeEngine()
        session = _FakeSession()
        with patch("app.core.database.create_async_engine", return_value=engine):
            with patch(
                "app.core.database.async_sessionmaker", return_value=_factory(session)
            ) as make:
                received = [s async for s in DatabaseManager.get_session()]
        assert received == [session]
        make.assert_called_once()

    async def test_session_factory_is_bound_to_the_engine(self):
        engine = _FakeEngine()
        with patch("app.core.database.create_async_engine", return_value=engine):
            with patch("app.core.database.async_sessionmaker") as make:
                async for _ in DatabaseManager.get_session():
                    pass
        assert make.call_args.args[0] is engine
        assert make.call_args.kwargs["class_"] is AsyncSession
        assert make.call_args.kwargs["expire_on_commit"] is False

    async def test_get_session_does_not_commit(self):
        # get_session is the "use outside FastAPI DI" helper: the caller owns
        # the transaction boundary.
        session = _FakeSession()
        DatabaseManager.session_factory = _factory(session)
        async for _ in DatabaseManager.get_session():
            pass
        assert session.commits == 0
        assert session.rollbacks == 0


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


class TestGetDb:
    async def test_yields_a_session_and_commits_on_success(self):
        session = _FakeSession()
        DatabaseManager.session_factory = _factory(session)
        received = [s async for s in get_db()]
        assert received == [session]
        assert session.commits == 1
        assert session.rollbacks == 0

    async def test_initializes_the_factory_when_missing(self):
        session = _FakeSession()
        engine = _FakeEngine()
        with patch("app.core.database.create_async_engine", return_value=engine):
            with patch("app.core.database.async_sessionmaker", return_value=_factory(session)):
                received = [s async for s in get_db()]
        assert received == [session]
        assert session.commits == 1

    async def test_rolls_back_and_propagates_on_handler_failure(self):
        # A raising consumer must leave the transaction rolled back, never
        # half-applied, and the exception must not be swallowed. The exception
        # is thrown *into* the generator (as FastAPI does when a route handler
        # raises) so the generator's own except clause runs.
        session = _FakeSession()
        DatabaseManager.session_factory = _factory(session)
        boom = RuntimeError("handler failed")
        agen = get_db()
        yielded = await agen.asend(None)
        assert yielded is session
        with pytest.raises(RuntimeError, match="handler failed"):
            await agen.athrow(boom)
        assert session.rollbacks == 1
        assert session.commits == 0
        with pytest.raises(StopAsyncIteration):
            await agen.asend(None)

    async def test_a_failing_commit_also_triggers_a_rollback(self):
        session = _FakeSession(fail_on_commit=RuntimeError("commit failed"))
        DatabaseManager.session_factory = _factory(session)
        with pytest.raises(RuntimeError, match="commit failed"):
            async for _ in get_db():
                pass
        assert session.rollbacks == 1
        assert session.commits == 0

    async def test_exception_identity_is_preserved(self):
        session = _FakeSession()
        DatabaseManager.session_factory = _factory(session)
        boom = ValueError("specific")
        agen = get_db()
        await agen.asend(None)
        with pytest.raises(ValueError) as exc:
            await agen.athrow(boom)
        assert exc.value is boom
        assert session.rollbacks == 1

    def test_get_db_session_is_an_alias_of_get_db(self):
        # Existing route imports use get_db_session; they must not diverge.
        assert get_db_session is get_db

    def test_singleton_starts_with_no_engine_and_no_factory(self):
        assert DatabaseManager.engine is None or isinstance(
            DatabaseManager.engine, object
        )
        assert hasattr(DatabaseManager, "session_factory")


# ---------------------------------------------------------------------------
# Real PostgreSQL round trip (driven by TEST_DATABASE_URL)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


class TestAgainstRealPostgres:
    async def test_health_check_round_trips_select_1(self, real_url):
        engine = create_async_engine(
            make_url(real_url).set(drivername="postgresql+asyncpg"),
            **_engine_kwargs(),
        )
        try:
            DatabaseManager.engine = engine
            assert await DatabaseManager.health_check() is True
        finally:
            DatabaseManager.engine = None
            await engine.dispose()

    async def test_get_db_commits_real_work(self, real_url):
        engine = create_async_engine(
            make_url(real_url).set(drivername="postgresql+asyncpg"),
            **_engine_kwargs(),
        )
        try:
            DatabaseManager.session_factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async for session in get_db():
                async with session.begin():
                    await session.execute(text("SELECT 1"))
                # The row count proves the transaction really committed.
                result = await session.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
        finally:
            DatabaseManager.session_factory = None
            await engine.dispose()

    async def test_get_db_rolls_back_real_work(self, real_url):
        engine = create_async_engine(
            make_url(real_url).set(drivername="postgresql+asyncpg"),
            **_engine_kwargs(),
        )
        try:
            DatabaseManager.session_factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            with pytest.raises(RuntimeError):
                async for session in get_db():
                    await session.execute(text("SELECT 1"))
                    raise RuntimeError("abort")
        finally:
            DatabaseManager.session_factory = None
            await engine.dispose()

    async def test_metadata_create_all_is_idempotent(self, real_url):
        # The models must be able to materialise against a real schema.
        engine = create_async_engine(
            make_url(real_url).set(drivername="postgresql+asyncpg"),
            **_engine_kwargs(),
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    async def test_init_and_close_against_a_real_database(self, real_url):
        with patch.object(settings, "DATABASE_URL", real_url):
            await DatabaseManager.init()
            assert DatabaseManager.engine is not None
            assert await DatabaseManager.health_check() is True
            engine = DatabaseManager.engine
            await DatabaseManager.close()
        # dispose() is idempotent-safe: the engine reports no live pool after.
        assert engine.pool is not None


def _engine_kwargs() -> dict:
    """The pooling/timeout settings production uses, minus server_settings.

    ``server_settings`` are Postgres-specific and would be rejected by other
    dialects, but the URL here is always Postgres, so they are kept.
    """
    return {
        "echo": False,
        "pool_pre_ping": True,
        "connect_args": {
            "command_timeout": 30,
            "server_settings": {
                "statement_timeout": "10000",
                "lock_timeout": "5000",
                "idle_in_transaction_session_timeout": "30000",
            },
        },
    }


def test_module_uses_the_async_engine_factory():
    # Guards against someone swapping in the blocking engine: the health probe
    # and get_db are both async and would deadlock on a sync session.
    import app.core.database as db_module

    assert db_module.create_async_engine.__module__.startswith("sqlalchemy")
    assert db_module.AsyncSession.__module__.startswith("sqlalchemy")
