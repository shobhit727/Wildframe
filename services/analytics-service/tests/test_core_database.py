"""Tests for ``analytics-service/app/core/database.py``.

``DatabaseManager`` here is a classmethod/classmethod-attribute singleton, and
all 73 statements of the module were unexecuted. These tests cover:

* ``init`` -- the engine is built with the #429/#430 bounded-lock server
  settings, a sized pre-pinged pool, and an ``AsyncSession`` factory using
  ``expire_on_commit=False``.
* ``close`` -- disposes the engine, and is a safe no-op when never initialised.
* ``health_check`` -- lazily initialises, runs a real ``text("SELECT 1")``,
  and returns False (never raises) when the server is unreachable.
* ``get_db`` -- the FastAPI DI generator, including its lazy ``init()``.

No PostgreSQL server is required. Engine *construction* is asserted by
capturing the ``create_async_engine`` kwargs -- the connect args are merged into
a transient mapping at create time and are not retained on the dialect. The
``health_check`` probe is verified against a fake engine whose ``execute``
records the statement, which is how the "must use ``text('SELECT 1')``, never a
lambda" rule from AGENTS.md is actually asserted.
"""

import pytest
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import DatabaseManager, get_db
from app.core.settings import settings

PG_URL = "postgresql+asyncpg://app_user:s3cr3t@127.0.0.1:59998/analytics_db"
SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def reset_manager():
    """Start and finish every test with no engine / no session factory."""
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None
    yield
    DatabaseManager.engine = None
    DatabaseManager.session_factory = None


@pytest.fixture
def engine_spy(monkeypatch):
    """Record the kwargs ``init`` hands to ``create_async_engine``."""
    import app.core.database as database_module

    calls: list[dict] = []
    real = database_module.create_async_engine

    def _spy(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return real(url, **kwargs)

    monkeypatch.setattr(database_module, "create_async_engine", _spy)
    return calls


class FakeConnection:
    """Records the statements ``health_check`` executes."""

    def __init__(self, sink: list):
        self._sink = sink

    async def execute(self, statement, *args, **kwargs):
        self._sink.append(statement)
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeEngine:
    """Stands in for AsyncEngine.connect() -> async context manager."""

    def __init__(self, sink: list, fail: bool = False):
        self._sink = sink
        self._fail = fail

    def connect(self):
        if self._fail:
            raise OSError("connection refused")
        return FakeConnection(self._sink)


# ================================== init ===================================


@pytest.mark.unit
async def test_init_builds_a_pooled_pre_pinged_engine(monkeypatch, engine_spy):
    """database.py:25-42 -- the postgres engine is sized and pre-pinged."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)

    await DatabaseManager.init()

    assert len(engine_spy) == 1
    kwargs = engine_spy[0]
    assert kwargs["url"] == PG_URL
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 5
    assert kwargs["pool_timeout"] == 30
    assert kwargs["pool_recycle"] == 3600
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["echo"] is False
    assert kwargs["future"] is True
    # The built engine really uses the async-adapted queue pool.
    assert type(DatabaseManager.engine.pool).__name__ == "AsyncAdaptedQueuePool"
    assert DatabaseManager.engine.pool.size() == 5


@pytest.mark.unit
async def test_init_sets_the_bounded_lock_server_settings(monkeypatch, engine_spy):
    """#429/#430: statement, lock and idle-in-transaction timeouts."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)

    await DatabaseManager.init()

    connect_args = engine_spy[0]["connect_args"]
    assert connect_args["command_timeout"] == 30
    assert connect_args["server_settings"] == {
        "statement_timeout": "10000",
        "lock_timeout": "5000",
        "idle_in_transaction_session_timeout": "30000",
    }


@pytest.mark.unit
async def test_init_creates_an_async_session_factory(monkeypatch, engine_spy):
    """The factory must yield AsyncSession with expire_on_commit disabled."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)

    await DatabaseManager.init()

    factory = DatabaseManager.session_factory
    assert isinstance(factory, async_sessionmaker)
    assert factory.class_ is AsyncSession
    assert factory.kw["expire_on_commit"] is False
    # The factory is bound to the engine init just built.
    assert factory.kw["bind"] is DatabaseManager.engine


@pytest.mark.unit
async def test_init_overwrites_a_previous_engine(monkeypatch):
    """A second init replaces both attributes rather than skipping."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    await DatabaseManager.init()
    first_engine = DatabaseManager.engine
    first_factory = DatabaseManager.session_factory

    await DatabaseManager.init()

    assert DatabaseManager.engine is not first_engine
    assert DatabaseManager.session_factory is not first_factory


@pytest.mark.unit
async def test_init_rejects_a_sqlite_url(monkeypatch, engine_spy):
    """KNOWN LIMITATION -- database.py:25-34.

    ``init`` unconditionally passes ``pool_size``/``max_overflow``/
    ``pool_timeout``/``pool_recycle`` and asyncpg-only ``connect_args``. The
    sibling ``streaming-service`` manager special-cases sqlite (NullPool, no
    pool sizing), so it can be pointed at SQLite; this one cannot.

    Consequence: the analytics DI/health paths cannot be exercised against
    SQLite at all. Pinned here so the asymmetry is explicit -- if a sqlite
    branch is ever added to ``init``, this test fails.
    """
    monkeypatch.setattr(settings, "DATABASE_URL", SQLITE_URL)
    with pytest.raises(TypeError, match="pool_size"):
        await DatabaseManager.init()


# ================================== close ==================================


@pytest.mark.unit
async def test_close_disposes_the_engine():
    """database.py:50-51 -- ``engine.dispose()`` is awaited on shutdown."""
    disposed: list[int] = []

    class _Engine:
        async def dispose(self):
            disposed.append(1)

    DatabaseManager.engine = _Engine()

    await DatabaseManager.close()

    assert disposed == [1]


@pytest.mark.unit
async def test_close_is_a_noop_without_an_engine():
    """database.py:50 -- ``if cls.engine`` short-circuits; no AttributeError."""
    DatabaseManager.engine = None
    await DatabaseManager.close()
    assert DatabaseManager.engine is None


@pytest.mark.unit
async def test_close_does_not_null_the_engine_attribute():
    """Documents an asymmetry with the streaming service.

    ``streaming-service``'s ``DatabaseManager.close()`` resets ``_engine`` to
    ``None``; this one leaves the (now-disposed) engine in place. Harmless
    here because ``health_check`` only rebuilds when the attribute is ``None``,
    but pinned so the difference is intentional rather than accidental.
    """
    disposed: list[int] = []

    class _Engine:
        async def dispose(self):
            disposed.append(1)

    engine = _Engine()
    DatabaseManager.engine = engine

    await DatabaseManager.close()

    assert DatabaseManager.engine is engine


# ============================== health_check ================================


@pytest.mark.unit
async def test_health_check_initialises_lazily(monkeypatch, engine_spy):
    """database.py:57-58 -- no engine means init first."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    assert DatabaseManager.engine is None

    await DatabaseManager.health_check()

    assert DatabaseManager.engine is not None
    assert len(engine_spy) == 1


@pytest.mark.unit
async def test_health_check_executes_a_real_select_one():
    """database.py:60-62 -- the probe must be ``text("SELECT 1")``.

    AGENTS.md forbids a Python lambda here, so the statement object itself is
    asserted, not just the boolean verdict.
    """
    executed: list = []
    DatabaseManager.engine = FakeEngine(executed)

    assert await DatabaseManager.health_check() is True

    assert len(executed) == 1
    statement = executed[0]
    # ``text("SELECT 1")`` builds a TextClause -- a real SQLAlchemy statement,
    # not a callable.
    assert isinstance(statement, TextClause)
    assert statement.text == "SELECT 1"


@pytest.mark.unit
async def test_health_check_reuses_an_existing_engine(monkeypatch, engine_spy):
    """An already-built engine must not be rebuilt on each probe."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    await DatabaseManager.init()
    engine = DatabaseManager.engine
    # Swap in a fake so the probe succeeds without a live server; the point is
    # that init() is *not* called again.
    DatabaseManager.engine = FakeEngine([])

    assert await DatabaseManager.health_check() is True

    assert len(engine_spy) == 1, "health_check must not re-run init()"
    assert DatabaseManager.engine is not engine


@pytest.mark.unit
async def test_health_check_returns_false_when_connect_raises():
    """database.py:63-64 -- an unreachable server is False, never an exception."""
    DatabaseManager.engine = FakeEngine([], fail=True)
    assert await DatabaseManager.health_check() is False


@pytest.mark.unit
async def test_health_check_returns_false_when_execute_raises():
    """A mid-probe failure (bad credentials, dropped connection) is still False."""

    class _BadConnection:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("connection reset mid-probe")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    class _Engine:
        def connect(self):
            return _BadConnection()

    DatabaseManager.engine = _Engine()
    assert await DatabaseManager.health_check() is False


@pytest.mark.unit
async def test_health_check_returns_false_when_init_itself_fails(monkeypatch):
    """A bad driver/URL must be swallowed by the same ``except Exception``."""
    monkeypatch.setattr(settings, "DATABASE_URL", "not-a-real-scheme://nope")
    assert await DatabaseManager.health_check() is False


# ================================== get_db ==================================


@pytest.mark.unit
async def test_get_db_initialises_lazily(monkeypatch, engine_spy):
    """database.py:69-70 -- the DI generator builds the factory on demand."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    assert DatabaseManager.session_factory is None

    gen = get_db()
    session = await gen.asend(None)

    assert isinstance(session, AsyncSession)
    assert DatabaseManager.session_factory is not None
    assert len(engine_spy) == 1
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)


@pytest.mark.unit
async def test_get_db_yields_a_queryable_session():
    """The yielded session must be usable, not a placeholder.

    The factory is injected over a real in-memory SQLite engine because
    ``init`` cannot build one (see ``test_init_rejects_a_sqlite_url``).
    """
    engine = create_async_engine(SQLITE_URL, future=True)
    try:
        DatabaseManager.session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        gen = get_db()
        session = await gen.asend(None)
        result = await session.execute(text("SELECT 1"))

        assert isinstance(session, AsyncSession)
        assert result.scalar_one() == 1
        with pytest.raises(StopAsyncIteration):
            await gen.asend(None)
    finally:
        await engine.dispose()


@pytest.mark.unit
async def test_get_db_reuses_an_existing_factory(monkeypatch, engine_spy):
    """A pre-built factory must be reused rather than re-initialised."""
    monkeypatch.setattr(settings, "DATABASE_URL", PG_URL)
    await DatabaseManager.init()
    factory = DatabaseManager.session_factory

    gen = get_db()
    await gen.asend(None)

    assert DatabaseManager.session_factory is factory
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)


@pytest.mark.unit
async def test_get_db_closes_the_session_on_exit():
    """database.py:72-73 -- the ``async with`` must close the session.

    ``AsyncSession.close()`` releases the connection but leaves the session
    reusable, so a post-close ``execute`` would not fail. The close is observed
    with a tracking factory instead.
    """

    class _TrackingSession:
        def __init__(self, log):
            self.closed = 0
            self._log = log

        async def close(self):
            self.closed += 1
            self._log.append("closed")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            await self.close()
            return False

    log: list[str] = []

    class _TrackingFactory:
        def __call__(self):
            session = _TrackingSession(log)
            self.last = session
            return session

    factory = _TrackingFactory()
    DatabaseManager.session_factory = factory

    gen = get_db()
    session = await gen.asend(None)
    assert session.closed == 0
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)

    assert session.closed == 1
    assert log == ["closed"]
