"""#1143: ``client_event_id`` idempotency must follow the *commit*, not precede it.

The ingestion endpoints used to claim the dedup key in Redis (``SET NX``)
before the row existed. The row is only durable once ``get_db`` commits, and
that commit ran after the response had been sent. When it failed:

* the key stayed behind, so the client's retry was swallowed as a duplicate;
* the response had already said ``recorded``, so the client never retried at
  all. The event was lost for the key's 24 hour lifetime.

These tests drive the real app (real ``get_db``, real routes, real service and
repositories) against a real PostgreSQL, so the unique-key behaviour and the
commit ordering are the database's and FastAPI's, not a mock's. Nothing here
sleeps: the commit failure is injected, and the concurrency test relies on a
unique index being correct under *every* interleaving rather than on timing.

A disposable PostgreSQL is required. Point ``TEST_DATABASE_URL`` at one (CI's
``backend-test`` job already does); the tables are created in a throwaway
schema and dropped afterwards, so the target database is otherwise untouched.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine

import app.main as main_module
from app.api.analytics_routes import get_current_user_claims
from app.core.database import DatabaseManager
from app.main import app
from app.models import Base


class FakeRedis:
    """Minimal redis-compatible store: just ``set(..., nx=True, ex=...)``.

    Installed as ``app.state.redis_client``, where the pre-#1143 app found its
    dedup store, so these tests also run (and fail) against that code. The
    fixed app does not read it; it only ever receives the old pre-write claim.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


@dataclass(frozen=True)
class Endpoint:
    path: str
    recorded: str  # the body status for a persisted event
    table: str
    owner_column: str
    body: Callable[[uuid.UUID, str | None], dict[str, Any]]


def _view_body(owner: uuid.UUID, client_event_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "content_id": str(uuid.uuid4()),
        "viewer_id": str(owner),
        "watch_duration_seconds": 10,
        "content_duration_seconds": 100,
        "completion_pct": 10.0,
    }
    if client_event_id is not None:
        body["client_event_id"] = client_event_id
    return body


def _event_body(owner: uuid.UUID, client_event_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"user_id": str(owner), "event_type": "playback_started"}
    if client_event_id is not None:
        body["client_event_id"] = client_event_id
    return body


VIEW = Endpoint(
    "/api/v1/analytics/view-events",
    "recorded",
    "content_view_events",
    "viewer_id",
    _view_body,
)
EVENT = Endpoint("/api/v1/analytics/events", "logged", "events", "user_id", _event_body)
ENDPOINTS = pytest.mark.parametrize("endpoint", [VIEW, EVENT], ids=["view-events", "events"])


@dataclass
class Harness:
    engine: AsyncEngine
    client: httpx.AsyncClient
    redis: FakeRedis
    user: dict[str, uuid.UUID]

    async def post(self, endpoint: Endpoint, client_event_id: str | None) -> httpx.Response:
        return await self.client.post(
            endpoint.path, json=endpoint.body(self.user["id"], client_event_id)
        )

    async def count(self, endpoint: Endpoint, owner: uuid.UUID | None = None) -> int:
        owner = owner or self.user["id"]
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text(f"SELECT count(*) FROM {endpoint.table} WHERE {endpoint.owner_column} = :o"),
                {"o": owner},
            )
            return int(result.scalar_one())


def _database_url() -> URL:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. These tests need a disposable PostgreSQL, e.g. "
            "TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5432/test_db"
        )
    # A URL object, not str(): str() masks the password as "***".
    return make_url(url).set(drivername="postgresql+asyncpg")


@pytest_asyncio.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    schema = f"wf_analytics_1143_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(_database_url())
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        _database_url(), connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        monkeypatch.setattr(
            DatabaseManager,
            "session_factory",
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
        )
        user = {"id": uuid.uuid4()}
        fake_redis = FakeRedis()
        app.dependency_overrides.clear()
        app.dependency_overrides[get_current_user_claims] = lambda: {
            "user_id": user["id"],
            "role": "user",
        }
        # The lifespan does not run under ASGITransport; set what it would set.
        main_module._in_flight_lock = asyncio.Lock()
        main_module._shutdown_event = asyncio.Event()
        app.state.shutting_down = False
        app.state.redis_client = fake_redis
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield Harness(engine, client, fake_redis, user)
    finally:
        app.dependency_overrides.clear()
        app.state.redis_client = None
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
def fail_next_commits(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Make the next ``n`` ``AsyncSession.commit`` calls raise, then behave."""
    real_commit = AsyncSession.commit
    remaining = {"n": 0}

    async def flaky_commit(self: AsyncSession) -> None:
        if remaining["n"] > 0:
            remaining["n"] -= 1
            raise OperationalError("COMMIT", {}, ConnectionError("connection lost at commit"))
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)

    def arm(n: int) -> None:
        remaining["n"] = n

    return arm


@ENDPOINTS
async def test_failed_commit_is_not_reported_as_recorded(
    harness: Harness, fail_next_commits: Callable[[int], None], endpoint: Endpoint
) -> None:
    """The reply may only claim success once the row is durable."""
    fail_next_commits(1)

    response = await harness.post(endpoint, "evt-1")

    assert response.status_code >= 500, response.text
    assert endpoint.recorded not in response.text
    assert await harness.count(endpoint) == 0


@ENDPOINTS
async def test_retry_after_failed_commit_is_persisted(
    harness: Harness, fail_next_commits: Callable[[int], None], endpoint: Endpoint
) -> None:
    """The client's retry of a failed write must write, not be swallowed."""
    fail_next_commits(1)
    await harness.post(endpoint, "evt-1")  # fails at commit; its outcome is not the point

    retry = await harness.post(endpoint, "evt-1")

    assert retry.status_code == 200, retry.text
    assert retry.json() == {"status": endpoint.recorded}
    assert await harness.count(endpoint) == 1


@ENDPOINTS
async def test_failed_write_leaves_no_dedup_key_behind(
    harness: Harness, fail_next_commits: Callable[[int], None], endpoint: Endpoint
) -> None:
    fail_next_commits(1)

    await harness.post(endpoint, "evt-1")

    assert harness.redis.data == {}


@ENDPOINTS
async def test_true_duplicate_is_reported_and_inserts_no_second_row(
    harness: Harness, endpoint: Endpoint
) -> None:
    first = await harness.post(endpoint, "evt-1")
    second = await harness.post(endpoint, "evt-1")

    assert first.status_code == 200 and first.json() == {"status": endpoint.recorded}
    assert second.status_code == 200 and second.json() == {"status": "duplicate"}
    assert await harness.count(endpoint) == 1


@ENDPOINTS
async def test_dedup_does_not_depend_on_redis(harness: Harness, endpoint: Endpoint) -> None:
    """Redis being absent, flushed or evicted must not reopen the duplicate."""
    app.state.redis_client = None

    first = await harness.post(endpoint, "evt-1")
    second = await harness.post(endpoint, "evt-1")

    assert first.json() == {"status": endpoint.recorded}
    assert second.json() == {"status": "duplicate"}
    assert await harness.count(endpoint) == 1


@ENDPOINTS
async def test_concurrent_requests_with_one_client_event_id_persist_once(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, endpoint: Endpoint
) -> None:
    """Two in-flight deliveries of one event: the unique key admits exactly one.

    A latch holds every commit until both requests have sent their INSERT, so
    the second INSERT always runs while the first transaction is still open
    (it waits on that transaction, then conflicts once it commits). Without
    the latch the two requests could simply run one after the other.
    """
    app.state.redis_client = None
    inserts_sent = 0
    both_inserts_sent = asyncio.Event()

    def count_inserts(conn, cursor, statement, parameters, context, executemany) -> None:
        nonlocal inserts_sent
        if statement.lstrip().upper().startswith(f"INSERT INTO {endpoint.table.upper()}"):
            inserts_sent += 1
            if inserts_sent == 2:
                both_inserts_sent.set()

    real_commit = AsyncSession.commit

    async def latched_commit(self: AsyncSession) -> None:
        await asyncio.wait_for(both_inserts_sent.wait(), timeout=10)
        await real_commit(self)

    event.listen(harness.engine.sync_engine, "before_cursor_execute", count_inserts)
    monkeypatch.setattr(AsyncSession, "commit", latched_commit)
    try:
        responses = await asyncio.gather(
            harness.post(endpoint, "evt-1"), harness.post(endpoint, "evt-1")
        )
    finally:
        event.remove(harness.engine.sync_engine, "before_cursor_execute", count_inserts)

    assert inserts_sent == 2
    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.json()["status"] for r in responses) == sorted(["duplicate", endpoint.recorded])
    assert await harness.count(endpoint) == 1


@ENDPOINTS
async def test_events_without_client_event_id_are_all_recorded(
    harness: Harness, endpoint: Endpoint
) -> None:
    """The key is optional: NULLs never collide, and nothing is deduplicated."""
    first = await harness.post(endpoint, None)
    second = await harness.post(endpoint, None)

    assert first.json() == second.json() == {"status": endpoint.recorded}
    assert await harness.count(endpoint) == 2


@ENDPOINTS
async def test_same_client_event_id_from_different_users_is_independent(
    harness: Harness, endpoint: Endpoint
) -> None:
    """One user's id must never suppress, or reveal the use of, another user's."""
    other = uuid.uuid4()
    first = await harness.post(endpoint, "shared-id")
    harness.user["id"] = other
    second = await harness.post(endpoint, "shared-id")

    assert first.json() == second.json() == {"status": endpoint.recorded}
    assert await harness.count(endpoint, other) == 1


@ENDPOINTS
async def test_client_event_key_is_unique_optional_and_not_the_raw_id(
    harness: Harness, endpoint: Endpoint
) -> None:
    """The invariant lives in the schema: one unique, nullable, owner-scoped key.

    The column is ``unique=True`` on the column itself, not a table-level
    ``Index``, so the schema bootstrap refuses to add it bare to an existing
    table instead of silently leaving the constraint off. It stores a digest
    of (owner, client_event_id) rather than the client-supplied text, which
    is arbitrary and is only ever needed for equality.
    """
    table = endpoint.table

    def _inspect(sync_conn: Any) -> tuple[dict[str, Any], set[tuple[str, ...]]]:
        inspector = sa_inspect(sync_conn)
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        unique = {tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
        unique |= {
            tuple(i["column_names"]) for i in inspector.get_indexes(table) if i.get("unique")
        }
        return columns, unique

    async with harness.engine.connect() as conn:
        columns, unique = await conn.run_sync(_inspect)

    assert "client_event_key" in columns
    assert columns["client_event_key"]["nullable"] is True
    assert ("client_event_key",) in unique

    await harness.post(endpoint, "evt-1")
    async with harness.engine.connect() as conn:
        stored = (await conn.execute(text(f"SELECT client_event_key FROM {table}"))).scalar_one()
    assert stored != "evt-1"
    assert len(stored) == 64 and int(stored, 16) >= 0
