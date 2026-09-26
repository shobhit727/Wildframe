"""DatabaseManager: engine construction, health probing, session lifecycle.

``create_async_engine`` is patched so the asyncpg-only pool/connect_args
contract can be asserted without a live PostgreSQL server.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import TextClause
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database as database_mod
from app.core.database import DatabaseManager, get_db
from app.core.settings import settings


@pytest.fixture
def reset_manager():
    saved_engine = DatabaseManager.engine
    saved_factory = DatabaseManager.session_factory
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    yield DatabaseManager
    DatabaseManager.engine = saved_engine
    DatabaseManager.session_factory = saved_factory


def _engine_mock() -> MagicMock:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    engine.connect = MagicMock()
    return engine


class _ConnectContext:
    def __init__(self, conn: MagicMock):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _connectable(conn: MagicMock) -> MagicMock:
    engine = _engine_mock()
    engine.connect.return_value = _ConnectContext(conn)
    return engine


def _connection() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock()
    return conn


class TestInit:
    @pytest.mark.asyncio
    async def test_builds_engine_with_bounded_pool_and_statement_timeouts(
        self, reset_manager, monkeypatch
    ):
        engine = _engine_mock()
        factory = MagicMock(return_value="session-factory")
        create = MagicMock(return_value=engine)
        sessionmaker = MagicMock(return_value=factory)
        monkeypatch.setattr(database_mod, "create_async_engine", create)
        monkeypatch.setattr(database_mod, "async_sessionmaker", sessionmaker)

        await DatabaseManager.init()

        create.assert_called_once()
        kwargs = create.call_args.kwargs
        assert create.call_args.args == (settings.DATABASE_URL,)
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 5
        assert kwargs["pool_timeout"] == 30
        assert kwargs["pool_recycle"] == 3600
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["echo"] is False
        assert kwargs["connect_args"]["command_timeout"] == 30
        server_settings = kwargs["connect_args"]["server_settings"]
        assert server_settings["statement_timeout"] == "10000"
        assert server_settings["lock_timeout"] == "5000"
        assert server_settings["idle_in_transaction_session_timeout"] == "30000"

        sessionmaker.assert_called_once_with(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        assert DatabaseManager.engine is engine
        assert DatabaseManager.session_factory is factory

    @pytest.mark.asyncio
    async def test_a_second_init_replaces_the_engine_without_disposing_it(
        self, reset_manager, monkeypatch
    ):
        """Documented behaviour: ``init`` always builds a fresh engine.

        The previous engine is overwritten and never disposed, so a repeated
        init leaks the old pool. Reported, not fixed.
        """
        first_engine = _engine_mock()
        second_engine = _engine_mock()
        create = MagicMock(side_effect=[first_engine, second_engine])
        monkeypatch.setattr(database_mod, "create_async_engine", create)
        monkeypatch.setattr(database_mod, "async_sessionmaker", MagicMock())

        await DatabaseManager.init()
        await DatabaseManager.init()

        assert create.call_count == 2
        assert DatabaseManager.engine is second_engine
        first_engine.dispose.assert_not_awaited()


class TestClose:
    @pytest.mark.asyncio
    async def test_disposes_the_engine(self, reset_manager):
        engine = _engine_mock()
        DatabaseManager.engine = engine

        await DatabaseManager.close()

        engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_a_noop_without_an_engine(self, reset_manager):
        assert DatabaseManager.engine is None

        await DatabaseManager.close()  # must not raise


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_when_select_1_succeeds(self, reset_manager):
        conn = _connection()
        DatabaseManager.engine = _connectable(conn)

        assert await DatabaseManager.health_check() is True

        # A real SQLAlchemy statement, not a Python lambda (health contract).
        statement = conn.execute.await_args.args[0]
        assert isinstance(statement, TextClause)
        assert str(statement) == "SELECT 1"

    @pytest.mark.asyncio
    async def test_initialises_the_engine_when_absent(self, reset_manager, monkeypatch):
        conn = _connection()
        create = MagicMock(return_value=_connectable(conn))
        monkeypatch.setattr(database_mod, "create_async_engine", create)
        monkeypatch.setattr(database_mod, "async_sessionmaker", MagicMock())

        assert await DatabaseManager.health_check() is True

        create.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_connect_explodes(self, reset_manager):
        engine = _engine_mock()
        engine.connect.side_effect = RuntimeError("no route to host")
        DatabaseManager.engine = engine

        assert await DatabaseManager.health_check() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_the_probe_query_fails(self, reset_manager):
        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("server closed the connection"))
        DatabaseManager.engine = _connectable(conn)

        assert await DatabaseManager.health_check() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_engine_creation_fails(self, reset_manager, monkeypatch):
        monkeypatch.setattr(
            database_mod,
            "create_async_engine",
            MagicMock(side_effect=RuntimeError("bad dsn")),
        )

        assert await DatabaseManager.health_check() is False


class _SessionFactory:
    """Stand-in for ``async_sessionmaker`` used as a plain session scope."""

    def __init__(self):
        self.sessions: list[MagicMock] = []

    def __call__(self):
        session = MagicMock(spec=AsyncSession)
        self.sessions.append(session)
        return _SessionContext(session)


class _SessionContext:
    def __init__(self, session: MagicMock):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class TestGetDb:
    @pytest.mark.asyncio
    async def test_yields_a_session(self, reset_manager):
        factory = _SessionFactory()
        DatabaseManager.session_factory = factory

        generator = get_db()
        yielded = await generator.__anext__()

        assert yielded is factory.sessions[0]

        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

    @pytest.mark.asyncio
    async def test_initialises_the_manager_when_no_factory_exists(
        self, reset_manager, monkeypatch
    ):
        factory = _SessionFactory()
        monkeypatch.setattr(database_mod, "async_sessionmaker", MagicMock(return_value=factory))
        monkeypatch.setattr(
            database_mod, "create_async_engine", MagicMock(return_value=_engine_mock())
        )

        generator = get_db()
        yielded = await generator.__anext__()

        assert yielded is factory.sessions[0]
        assert DatabaseManager.session_factory is factory
