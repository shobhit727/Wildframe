"""The rate limiter's Lua, executed by a real Lua engine (mutation G21).

Every other rate-limit test in this suite runs against `FakeRedis`, which
**re-implements** `_ACQUIRE_LEASE_SCRIPT` in Python -- including its own
``if len(leases) >= int(limit)``. That is AGENTS.md §19.1 exactly: the test
mocks the layer where the defect lives. Flipping the production script's
``>=`` to ``>`` -- which admits ``limit + 1`` concurrent requests per key --
survived the whole suite, because the assertion was made against a *copy* of
the logic.

So this file drives the real production script through `RateLimiter` against a
real Redis, which is the only thing that actually runs the Lua. Redis is
resolved in this order:

1. ``WILDFRAME_TEST_REDIS_URL`` -- point this at any reachable Redis.
2. ``redis://127.0.0.1:6379/15`` -- a local dev server, or a
   ``docker run -d -p 6379:6379 redis:7-alpine`` one.

When neither answers, the live tests **skip** with that message rather than
passing against a double. That is a real gap, not a formality: CI defines a
`postgres` service and no `redis` one (see .github/workflows/ci-cd.yml), so on
CI only the textual proxy at the bottom of this file runs. The proxy is
labelled as the weaker thing it is -- it catches a mutation to the operator in
the script text and nothing else; the boundary behaviour itself is proven only
by the live tests. Adding a `redis` service to the gateway CI job is what would
close that gap, and it needs a change to .github/workflows/ci-cd.yml.

Run against a live Redis with:

    docker run -d -p 6379:6379 redis:7-alpine
    pytest tests/test_rate_limiter_lua.py
"""

import asyncio
import os
import uuid

import pytest
import redis.asyncio as redis

from app.middleware import RateLimiter

# Candidate Redis URLs, most explicit first.
_CANDIDATE_URLS = (
    os.environ.get("WILDFRAME_TEST_REDIS_URL"),
    "redis://127.0.0.1:6379/15",
)
_SKIP_REASON = (
    "no reachable Redis for the live Lua tests; set WILDFRAME_TEST_REDIS_URL "
    "(e.g. `docker run -d -p 6379:6379 redis:7-alpine`) -- these are the only "
    "tests that execute the production Lua script"
)


async def _live_client() -> redis.Redis | None:
    for url in _CANDIDATE_URLS:
        if not url:
            continue
        client = redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=2)
        try:
            await client.ping()
        except Exception:  # noqa: BLE001 -- any connection failure means "try the next"
            await client.aclose()
            continue
        return client
    return None


@pytest.fixture
async def live_redis():
    client = await _live_client()
    if client is None:
        pytest.skip(_SKIP_REASON)
    try:
        yield client
    finally:
        await client.aclose()


def _limiter(client, concurrency: int) -> RateLimiter:
    """A limiter whose only live dimension is concurrency.

    A unique IP string gives the test its own key namespace, so running this
    against a developer's real Redis cannot disturb -- or be disturbed by --
    anything else. The fixed-window and burst limits are raised out of the way
    so a failure can only mean the concurrency script said so.
    """
    limiter = RateLimiter(client)
    limiter.limits["uploads"] = 10_000
    limiter.burst_limits["uploads"] = 10_000
    limiter.concurrency_limits["uploads"] = concurrency
    return limiter


def _ip() -> str:
    return f"203.0.113.{uuid.uuid4().int % 250}"


def _inflight_key(ip: str, service: str = "uploads") -> str:
    return f"rate_limit:inflight:ip:{ip}:{service}"


# ---------------------------------------------------------------------------
# Live: the production script, run by Redis's own Lua interpreter
# ---------------------------------------------------------------------------


async def test_the_lease_boundary_is_the_configured_limit(live_redis):
    """`limit` admits exactly `limit` concurrent leases, and refuses the next.

    The mutant this kills is ``if redis.call('ZCARD', key) >= limit`` becoming
    ``> limit``, which would admit ``limit + 1`` concurrent requests.
    """
    limiter = _limiter(live_redis, concurrency=2)
    ip = _ip()

    first = await limiter.acquire_rate_limits(ip, "uploads")
    second = await limiter.acquire_rate_limits(ip, "uploads")
    third = await limiter.acquire_rate_limits(ip, "uploads")

    assert first[0] is True and first[1], "the first lease was refused"
    assert second[0] is True and second[1], "the second lease was refused"
    assert third == (False, None), "the third lease was admitted past the limit"


async def test_a_single_concurrency_limit_still_refuses_the_second_lease(live_redis):
    """The degenerate case of the same boundary: limit 1, one lease, no more."""
    limiter = _limiter(live_redis, concurrency=1)
    ip = _ip()

    assert (await limiter.acquire_rate_limits(ip, "uploads"))[0] is True
    assert await limiter.acquire_rate_limits(ip, "uploads") == (False, None)


async def test_the_lua_actually_mutated_redis_state(live_redis):
    """Guards the test above against silently testing a double.

    If the script had not run inside Redis, the sorted set behind the key
    would not exist server-side and its TTL would not have been set by the
    script's own PEXPIRE.
    """
    limiter = _limiter(live_redis, concurrency=5)
    ip = _ip()

    allowed, lease_id = await limiter.acquire_rate_limits(ip, "uploads")
    assert allowed is True and lease_id

    key = _inflight_key(ip)
    assert await live_redis.zcard(key) == 1, "the Lua script did not ZADD inside Redis"
    assert 0 < await live_redis.pttl(key) <= limiter._lease_ttl_seconds * 1000


async def test_an_expired_lease_is_reclaimed(live_redis):
    """ZREMRANGEBYSCORE is real too -- deleting it strands every caller forever."""
    limiter = _limiter(live_redis, concurrency=1)
    limiter._lease_ttl_seconds = 1
    ip = _ip()

    first = await limiter.acquire_rate_limits(ip, "uploads")
    assert first[0] is True
    assert await limiter.acquire_rate_limits(ip, "uploads") == (False, None)

    await asyncio.sleep(1.2)

    second = await limiter.acquire_rate_limits(ip, "uploads")
    assert second[0] is True, "an expired lease was never reclaimed"


async def test_a_released_lease_frees_the_slot_immediately(live_redis):
    """The release script runs in the same real Lua engine as the acquire."""
    limiter = _limiter(live_redis, concurrency=1)
    ip = _ip()

    allowed, lease_id = await limiter.acquire_rate_limits(ip, "uploads")
    assert allowed is True and lease_id
    assert await limiter.acquire_rate_limits(ip, "uploads") == (False, None)

    await limiter.release_rate_limits(lease_id, ip, "uploads")

    assert (await limiter.acquire_rate_limits(ip, "uploads"))[0] is True


async def test_a_partially_taken_lease_is_rolled_back(live_redis):
    """Two keys, the second over capacity: the first must be given back.

    The ip key is taken first and the account key second, so a refusal on the
    account key leaves the ip key holding two leases unless the rollback runs.
    """
    limiter = _limiter(live_redis, concurrency=1)
    ip = _ip()

    first = await limiter.acquire_rate_limits(ip, "uploads", account_id="acct")
    assert first[0] is True

    second = await limiter.acquire_rate_limits(ip, "uploads", account_id="acct")
    assert second == (False, None)

    # One live lease, not two: the refused acquire gave its ip key back.
    assert await live_redis.zcard(_inflight_key(ip)) == 1


# ---------------------------------------------------------------------------
# Textual proxy -- runs everywhere, proves much less
# ---------------------------------------------------------------------------


def test_production_script_text_carries_the_boundary_the_live_tests_check():
    """WEAKER PROXY. Runs with no Redis, so this is what CI executes.

    It pins the operator in the shipped script text, which kills a ``>=`` ->
    ``>`` mutation of `_ACQUIRE_LEASE_SCRIPT` on a machine with no Redis. It
    cannot catch a semantic change that preserves those characters, and it
    executes nothing. The live tests above are the evidence; this is the floor,
    here so the mutant does not survive CI *silently*.
    """
    script = RateLimiter._ACQUIRE_LEASE_SCRIPT
    assert "if redis.call('ZCARD', key) >= limit then return 0 end" in script
    # The guard has to be evaluated after expired leases are swept and before
    # the ZADD, or the boundary it enforces is meaningless.
    assert script.index("ZREMRANGEBYSCORE") < script.index("ZCARD") < script.index("ZADD")
    assert "PEXPIRE" in script
