"""The registration attempt ceiling must be enforced by the Redis-backed
limiter (#1044).

``app/core/settings.py`` configures ``REGISTRATION_RATE_LIMIT_ATTEMPTS`` (3)
and ``REGISTRATION_RATE_LIMIT_WINDOW`` (3600) and ``docs/API_DOCUMENTATION.md``
documents them, but no code path read those settings to decide whether a
registration attempt was allowed. There was no counter for a registration
attempt in Redis, or in the process, or anywhere else, so the only throttle
on ``POST /auth/register`` was the api-gateway's coarse per-IP edge ceiling
(5 requests per 60s over the whole ``/auth/*`` namespace, which also counts
logins and session checks). That limiter is a different component, in a
different Redis database, with a sixty-second window -- so nothing about it
matched the documented ceiling, and ``rl:token:*`` stayed empty.

These tests pin the property the limiter module's own docstring claims --
Redis is the single source of truth, so limits survive service restarts --
for the registration bucket:

* the attempt counter is written to Redis under the documented key format
  (``rl:token:<40 hex>``), with no PII in the key;
* the ceiling is still in force for a fresh client and a fresh session: a
  new limiter connection to the same Redis, issuing a new request, still
  sees the counter.

The Redis double below models the server and the connection separately, so a
second connection to the same server sees the first one's writes. That is the
whole property under test: if the counter lived in the limiter instead of in
Redis, the second connection would start from zero.
"""

import contextlib
import re
from unittest.mock import patch

import pytest
from app.api.routes import auth as auth_routes
from app.core import rate_limit
from app.core.database import DatabaseManager
from app.core.settings import settings
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

EMAIL = "registration-limit@example.com"
OTHER_EMAIL = "registration-limit-other@example.com"
PASSWORD = "SecurePass123!"

# The key shape the module documents: rl:token:<blake2s-20 hex>.
TOKEN_KEY = re.compile(r"^rl:token:[0-9a-f]{40}$")


# --------------------------------------------------------------------------
# Redis double
# --------------------------------------------------------------------------


class _FakeRedisServer:
    """The server side of Redis: the data a real one would hold."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiry: dict[str, int] = {}
        self.keys_used: list[str] = []


class _FakePipeline:
    """Queues INCR/EXPIRE so ``execute`` returns the INCR result in order."""

    def __init__(self, connection: "_FakeRedis") -> None:
        self._connection = connection
        self._queued: list[tuple[str, str, int | None]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def incr(self, key: str) -> None:
        self._queued.append(("incr", key, None))

    def expire(self, key: str, ttl: int) -> None:
        self._queued.append(("expire", key, ttl))

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, key, arg in self._queued:
            if op == "incr":
                results.append(self._connection._incr(key))
            else:
                self._connection._expire(key, arg)
        self._queued.clear()
        return results


class _FakeRedis:
    """A client connection to a :class:`_FakeRedisServer`.

    ``new_connection()`` models a second process opening its own connection to
    the same server: same data, different object. Two limiter instances over
    two connections therefore share the counter exactly as two replicas behind
    one Redis do.
    """

    def __init__(self, server: _FakeRedisServer) -> None:
        self._server = server

    @property
    def keys_used(self) -> list[str]:
        return self._server.keys_used

    def new_connection(self) -> "_FakeRedis":
        return _FakeRedis(self._server)

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    def _incr(self, key: str) -> int:
        if key not in self._server.keys_used:
            self._server.keys_used.append(key)
        self._server.counts[key] = self._server.counts.get(key, 0) + 1
        return self._server.counts[key]

    def _expire(self, key: str, ttl: int | None) -> None:
        assert ttl is not None  # allow() always passes a window
        self._server.expiry[key] = ttl


@pytest.fixture
def redis_server() -> _FakeRedisServer:
    return _FakeRedisServer()


@pytest.fixture
def redis(redis_server: _FakeRedisServer) -> _FakeRedis:
    return _FakeRedis(redis_server)


# --------------------------------------------------------------------------
# App fixture
# --------------------------------------------------------------------------


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def app(tmp_path):
    """The auth app on a throwaway SQLite database, with the real limiter.

    ``allow`` is deliberately NOT stubbed here: the whole point of these
    tests is which keys the production limiter writes. The suite-wide
    ``conftest`` stub is replaced per test instead.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/registration-rate-limit.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run(_create())

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    original_engine = DatabaseManager.get_engine
    original_factory = DatabaseManager.get_session_factory
    DatabaseManager.get_engine = classmethod(lambda cls: engine)
    DatabaseManager.get_session_factory = classmethod(lambda cls: factory)

    async def _consumer(_f):
        return None

    from app.core import event_consumer

    try:
        with (
            patch.object(event_consumer, "run_user_moderation_consumer", _consumer),
            patch("app.main.setup_logging"),
        ):
            yield create_app()
    finally:
        DatabaseManager.get_engine = original_engine
        DatabaseManager.get_session_factory = original_factory
        _run(engine.dispose())


@pytest.fixture(autouse=True)
def _no_global_client():
    """Never fall through to the process-wide client (a real Redis may be up)."""
    saved = rate_limit._client
    rate_limit._client = None
    yield
    rate_limit._client = saved


@contextlib.contextmanager
def _real_limiter(connection: _FakeRedis):
    """Run the production limiter against ``connection``.

    Restores the real ``allow`` on the route module (the suite-wide
    ``conftest`` stub would otherwise answer every call with True) and points
    the module's client factory at the given connection.
    """
    with (
        patch.object(auth_routes, "allow", rate_limit.allow),
        patch("app.core.rate_limit._get_client", return_value=connection),
    ):
        yield


def _register(client: TestClient, email: str = EMAIL):
    return client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Rate",
            "last_name": "Limit",
        },
    )


# --------------------------------------------------------------------------
# The counter must live in Redis
# --------------------------------------------------------------------------


def test_the_registration_attempt_is_counted_in_redis_under_the_documented_key(
    app, redis: _FakeRedis
):
    """A registration attempt writes ``rl:token:<40 hex>`` to Redis.

    The key format is the one the limiter module documents, and the address
    itself must not appear in it: the key is a digest of the bucket, and the
    address is PII.
    """
    with TestClient(app) as client, _real_limiter(redis):
        response = _register(client)

    assert response.status_code == 201, response.text
    assert len(redis.keys_used) == 1, redis.keys_used
    key = redis.keys_used[0]
    assert TOKEN_KEY.match(key), key
    assert EMAIL not in key
    assert "example.com" not in key


def test_the_registration_counter_is_shared_by_every_replica(app, redis: _FakeRedis):
    """A second limiter over a second connection sees the first one's count.

    This is the property the module's docstring claims and the one the issue
    reports as missing: the counter lives in Redis, so it is shared by every
    replica rather than being private to one process.
    """
    attempts = settings.REGISTRATION_RATE_LIMIT_ATTEMPTS

    with TestClient(app) as client, _real_limiter(redis):
        for _ in range(attempts):
            _register(client)

    # A second replica: a new process, so a new connection to the same Redis.
    replica = redis.new_connection()
    with TestClient(app) as replica_client, _real_limiter(replica):
        response = _register(replica_client)

    assert response.status_code == 429, (
        "the ceiling was not enforced for a second replica: " f"{response.status_code}"
    )
    assert response.json()["detail"] == "Too many registration attempts. Try again later."


def test_the_ceiling_survives_a_restart_and_a_fresh_session(app, redis: _FakeRedis):
    """Restarting the service must not reset the ceiling.

    The module's docstring says Redis is the single source of truth "so limits
    survive service restarts". A restart drops the process's cached client
    and its memory; it cannot drop a key in Redis. So a request issued after
    the restart, from a new session, is still refused.
    """
    attempts = settings.REGISTRATION_RATE_LIMIT_ATTEMPTS

    with TestClient(app) as client, _real_limiter(redis):
        for _ in range(attempts):
            _register(client)

    # The restart: the cached client is gone, and the next connection is a
    # new one to the same Redis.
    rate_limit._client = None
    after_restart = redis.new_connection()

    with TestClient(app) as fresh_session, _real_limiter(after_restart):
        response = _register(fresh_session)

    assert response.status_code == 429, (
        "the ceiling did not survive a restart: " f"{response.status_code}"
    )


def test_the_ceiling_counts_attempts_and_not_only_the_first_one(app, redis: _FakeRedis):
    """Every attempt is counted, so the ceiling is reached.

    The second and third attempts for an address that already exists are
    rejected by the database, not by the limiter -- but they are still
    attempts, and the limiter has to see them. The fourth attempt is the one
    the ceiling refuses, and it is refused *before* the duplicate-address
    check, which is why it is a 429 and not a 409.
    """
    attempts = settings.REGISTRATION_RATE_LIMIT_ATTEMPTS

    with TestClient(app) as client, _real_limiter(redis):
        statuses = [_register(client).status_code for _ in range(attempts + 1)]

    assert statuses[0] == 201, statuses
    assert (
        statuses[-1] == 429
    ), f"the {attempts + 1}th attempt was not refused by the ceiling: {statuses}"
    assert statuses.count(429) == 1, statuses


def test_the_ceiling_is_per_address(app, redis: _FakeRedis):
    """One address exhausting its ceiling does not lock out another.

    The bucket is keyed by the address, so a client that trips its own limit
    cannot use that to deny service to an unrelated account.
    """
    attempts = settings.REGISTRATION_RATE_LIMIT_ATTEMPTS

    with TestClient(app) as client, _real_limiter(redis):
        for _ in range(attempts):
            _register(client)
        blocked = _register(client)
        other = _register(client, OTHER_EMAIL)

    assert blocked.status_code == 429
    assert other.status_code == 201, other.text
    assert len(redis.keys_used) == 2, redis.keys_used


def test_the_route_asks_the_limiter_for_the_documented_ceiling(app):
    """The route uses the configured ceiling rather than a literal.

    ``REGISTRATION_RATE_LIMIT_ATTEMPTS`` / ``_WINDOW`` in ``app/core/settings.py``
    are the documented numbers, so the route must pass those and not a
    hard-coded copy that can drift away from them.
    """

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def __call__(self, key, *, max_requests, window_seconds, cooldown_seconds=0):
            self.calls.append(
                {"key": key, "max_requests": max_requests, "window_seconds": window_seconds}
            )
            return True

    recorder = _Recorder()
    with TestClient(app) as client, patch.object(auth_routes, "allow", recorder):
        response = _register(client)

    assert response.status_code == 201, response.text
    assert len(recorder.calls) == 1, recorder.calls
    call = recorder.calls[0]
    assert call["key"] == f"registration:email:{EMAIL}"
    assert call["max_requests"] == settings.REGISTRATION_RATE_LIMIT_ATTEMPTS
    assert call["window_seconds"] == settings.REGISTRATION_RATE_LIMIT_WINDOW
    # The documented numbers, so a silent change to the settings is visible.
    assert call["max_requests"] == 3
    assert call["window_seconds"] == 3600


def test_equivalent_address_spellings_share_one_counter(app, redis: _FakeRedis):
    """``Alice@Example.com`` and ``alice@example.com`` are one account.

    The request model normalises the address, so the bucket sees one spelling
    and a client cannot multiply its own ceiling by changing case.
    """
    attempts = settings.REGISTRATION_RATE_LIMIT_ATTEMPTS

    with TestClient(app) as client, _real_limiter(redis):
        _register(client)
        for _ in range(attempts):
            response = _register(client, "Registration-Limit@Example.COM")

    assert response.status_code == 429, response.text
    assert len(redis.keys_used) == 1, redis.keys_used
