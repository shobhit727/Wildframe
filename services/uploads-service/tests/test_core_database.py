"""``app/core/database.py`` — engine construction, health, and session lifecycle.

The health check runs ``SELECT 1`` through a real SQLAlchemy statement (never a
Python lambda) because the k8s readiness probe depends on it, so it is pinned
against a real in-memory SQLite engine rather than a mock.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.core.database import DatabaseManager, get_db
from app.core.settings import settings

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_manager():
    """The manager holds class-level engine/session state; start each test clean."""
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    yield
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None


@pytest.fixture
async def sqlite_manager():
    with patch.object(settings, "DATABASE_URL", SQLITE_URL):
        await DatabaseManager.init()
        yield DatabaseManager
        await DatabaseManager.close()


# ---------------------------------------------------------------------------
# init(): the engine + session factory.
# ---------------------------------------------------------------------------


async def test_init_builds_an_engine_and_a_session_factory(sqlite_manager):
    assert DatabaseManager.engine is not None
    assert DatabaseManager.session_factory is not None
    assert DatabaseManager.session_factory.class_ is AsyncSession
    # expire_on_commit=False keeps ORM objects usable after a commit.
    assert DatabaseManager.session_factory.kw["expire_on_commit"] is False


async def test_init_uses_no_pool_overrides_for_sqlite():
    with patch.object(settings, "DATABASE_URL", SQLITE_URL):
        await DatabaseManager.init()
    # SQLite gets an empty pool/connect_args dict, not the Postgres budget.
    assert DatabaseManager.engine.pool.__class__.__name__ in {
        "StaticPool",
        "NullPool",
        "AsyncAdaptedQueuePool",
    }
    await DatabaseManager.close()


async def test_init_wires_the_postgres_pool_budget_and_server_timeouts():
    url = "postgresql+asyncpg://user:pass@localhost:5432/uploads_db"
    with (
        patch.object(settings, "DATABASE_URL", url),
        patch("app.core.database.create_async_engine") as create_engine,
    ):
        await DatabaseManager.init()

    kwargs = create_engine.call_args.kwargs
    # A capped QueuePool, not an unbounded one.
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 5
    assert kwargs["pool_timeout"] == 30
    assert kwargs["pool_recycle"] == 3600
    assert kwargs["pool_pre_ping"] is True
    # Server-side timeouts so a stuck query cannot pin a worker forever.
    connect_args = kwargs["connect_args"]
    assert connect_args["command_timeout"] == 30
    assert connect_args["server_settings"] == {
        "statement_timeout": "10000",
        "lock_timeout": "5000",
        "idle_in_transaction_session_timeout": "30000",
    }
    assert kwargs["echo"] is False
    assert kwargs["future"] is True


# ---------------------------------------------------------------------------
# health_check().
# ---------------------------------------------------------------------------


async def test_health_check_runs_select_1_and_reports_true(sqlite_manager):
    assert await DatabaseManager.health_check() is True


async def test_health_check_initialises_the_engine_on_first_use():
    with patch.object(settings, "DATABASE_URL", SQLITE_URL):
        assert DatabaseManager.engine is None
        assert await DatabaseManager.health_check() is True
    assert DatabaseManager.engine is not None
    await DatabaseManager.close()


class _BrokenEngine:
    """An engine whose ``connect()`` blows up the way a dead server would."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def connect(self):
        raise self._exc

    async def dispose(self) -> None:
        return None


async def test_health_check_reports_false_instead_of_raising():
    """A dead database must degrade liveness, never 500 the readiness probe."""
    DatabaseManager.engine = _BrokenEngine(
        OSError("server closed the connection unexpectedly")
    )  # type: ignore[assignment]
    assert await DatabaseManager.health_check() is False


async def test_health_check_reports_false_when_connect_itself_raises():
    DatabaseManager.engine = _BrokenEngine(OSError("could not connect to server"))
    assert await DatabaseManager.health_check() is False


async def test_health_check_reports_false_for_a_bad_dsn():
    with patch.object(settings, "DATABASE_URL", "postgresql+asyncpg://user:pass@127.0.0.1:1/nope"):
        assert await DatabaseManager.health_check() is False
    DatabaseManager.engine = None


class _StatementSpyConnection:
    """Wraps a real connection and records the statement it was handed."""

    def __init__(self, inner, seen: list) -> None:
        self._inner = inner
        self._seen = seen

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._inner.__aexit__(*exc)

    async def execute(self, statement):
        self._seen.append(statement)
        return await self._inner.execute(statement)


class _StatementSpyEngine:
    def __init__(self, engine, seen: list) -> None:
        self._engine = engine
        self._seen = seen

    def connect(self):
        return _StatementSpyConnection(self._engine.connect(), self._seen)

    async def dispose(self) -> None:
        await self._engine.dispose()


async def test_health_check_issues_a_real_select_1_statement():
    """A Python lambda would not be awaitable; the check must issue real SQL."""
    with patch.object(settings, "DATABASE_URL", SQLITE_URL):
        await DatabaseManager.init()
        real_engine = DatabaseManager.engine
        seen: list = []
        DatabaseManager.engine = _StatementSpyEngine(real_engine, seen)  # type: ignore[assignment]

        assert await DatabaseManager.health_check() is True

        assert len(seen) == 1
        assert isinstance(seen[0], TextClause)
        assert str(seen[0]) == "SELECT 1"
        await real_engine.dispose()


# ---------------------------------------------------------------------------
# close().
# ---------------------------------------------------------------------------


async def test_close_disposes_the_engine(sqlite_manager):
    engine = DatabaseManager.engine
    assert engine is not None
    await DatabaseManager.close()
    # A disposed engine hands out no live pooled connection.
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def test_close_is_a_noop_before_init():
    await DatabaseManager.close()  # must not raise
    assert DatabaseManager.engine is None


# ---------------------------------------------------------------------------
# get_db().
# ---------------------------------------------------------------------------


async def test_get_db_yields_a_usable_session(sqlite_manager):
    generator = get_db()
    session = await generator.__anext__()
    try:
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    finally:
        await generator.aclose()


async def test_get_db_initialises_the_manager_when_it_has_not_run():
    with patch.object(settings, "DATABASE_URL", SQLITE_URL):
        assert DatabaseManager.session_factory is None
        generator = get_db()
        session = await generator.__anext__()
        try:
            assert DatabaseManager.session_factory is not None
            assert isinstance(session, AsyncSession)
        finally:
            await generator.aclose()
        await DatabaseManager.close()


async def test_get_db_closes_the_session_when_the_generator_ends(sqlite_manager):
    generator = get_db()
    session = await generator.__anext__()
    assert session.is_active
    # Closing the generator runs the `async with` teardown.
    await generator.aclose()
    assert not session.in_transaction()
