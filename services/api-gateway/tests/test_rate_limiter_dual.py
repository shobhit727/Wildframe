import pytest
from unittest.mock import AsyncMock, MagicMock


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expires = {}
        self.leases = {}

    def __await__(self):
        """Mirror ``redis.asyncio.Redis.__await__``: awaiting the client returns it.

        The real client implements ``__await__`` as
        ``return self.initialize().__await__()``, so ``await redis.from_url(...)``
        both initialises and yields the client.
        """

        async def _identity():
            return self

        return _identity().__await__()

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):
        self.expires[key] = window
        return True

    async def eval(self, script, numkeys, key, *args):
        assert numkeys == 1
        leases = self.leases.setdefault(key, {})
        if "ZREMRANGEBYSCORE" in script:
            lease_id, now, expiry, limit = args
            leases = {member: exp for member, exp in leases.items() if exp > float(now)}
            self.leases[key] = leases
            if len(leases) >= int(limit):
                return 0
            leases[lease_id] = float(expiry)
            return 1
        lease_id = args[0]
        return int(leases.pop(lease_id, None) is not None)


@pytest.mark.asyncio
async def test_ip_fixed_window_blocks():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 2
    limiter.burst_limits["search"] = 100
    limiter.concurrency_limits["search"] = 100
    ip = "1.2.3.4"
    assert await limiter.check_rate_limit("search", "", ip=ip) is True
    assert await limiter.check_rate_limit("search", "", ip=ip) is True
    assert await limiter.check_rate_limit("search", "", ip=ip) is False


@pytest.mark.asyncio
async def test_account_fixed_window_blocks():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 2
    limiter.burst_limits["search"] = 100
    limiter.concurrency_limits["search"] = 100
    ip = "1.2.3.4"
    acct = "user1"
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id=acct) is True
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id=acct) is True
    redis2 = FakeRedis()
    limiter2 = RateLimiter(redis2)
    limiter2.limits["search"] = 2
    limiter2.burst_limits["search"] = 100
    limiter2.concurrency_limits["search"] = 100
    assert await limiter2.check_rate_limit("search", "", ip="5.6.7.8", account_id=acct) is True
    assert await limiter2.check_rate_limit("search", "", ip="5.6.7.8", account_id=acct) is True
    assert await limiter2.check_rate_limit("search", "", ip="5.6.7.8", account_id=acct) is False


@pytest.mark.asyncio
async def test_independent_ip_and_account_both_checked():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 2
    limiter.burst_limits["search"] = 100
    limiter.concurrency_limits["search"] = 100
    ip = "10.0.0.1"
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="alice") is True
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="alice") is True
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="alice") is False
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="bob") is False


@pytest.mark.asyncio
async def test_account_farming_does_not_multiply():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 5
    limiter.burst_limits["search"] = 100
    limiter.concurrency_limits["search"] = 100
    ip = "203.0.113.9"
    for i in range(5):
        acct = f"farmer_{i}"
        assert await limiter.check_rate_limit("search", "", ip=ip, account_id=acct) is True
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="farmer_new") is False
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="farmer_another") is False
    redis2 = FakeRedis()
    limiter2 = RateLimiter(redis2)
    limiter2.limits["search"] = 2
    limiter2.burst_limits["search"] = 100
    limiter2.concurrency_limits["search"] = 100
    ip2 = "203.0.113.10"
    assert await limiter2.check_rate_limit("search", "", ip=ip2, account_id="a") is True
    assert await limiter2.check_rate_limit("search", "", ip=ip2, account_id="b") is True
    assert await limiter2.check_rate_limit("search", "", ip=ip2, account_id="c") is False
    assert await limiter2.check_rate_limit("search", "", ip=ip2, account_id="d") is False


@pytest.mark.asyncio
async def test_burst_blocks_for_search_even_under_fixed_limit():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 100
    limiter.burst_limits["search"] = 2
    limiter.concurrency_limits["search"] = 100
    ip = "1.1.1.1"
    assert await limiter.check_rate_limit("search", "", ip=ip) is True
    assert await limiter.check_rate_limit("search", "", ip=ip) is True
    assert await limiter.check_rate_limit("search", "", ip=ip) is False


@pytest.mark.asyncio
async def test_burst_per_ip_and_account_independent():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 100
    limiter.burst_limits["search"] = 2
    limiter.concurrency_limits["search"] = 100
    ip = "2.2.2.2"
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="u1") is True
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="u1") is True
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="u1") is False
    assert await limiter.check_rate_limit("search", "", ip=ip, account_id="u2") is False


@pytest.mark.asyncio
async def test_concurrency_blocks_for_upload():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["uploads"] = 100
    limiter.burst_limits["uploads"] = 100
    limiter.concurrency_limits["uploads"] = 2
    ip = "3.3.3.3"
    allowed1, lease1 = await limiter.acquire_rate_limits(ip, "uploads")
    allowed2, lease2 = await limiter.acquire_rate_limits(ip, "uploads")
    allowed3, lease3 = await limiter.acquire_rate_limits(ip, "uploads")
    assert (allowed1, allowed2, allowed3) == (True, True, False)
    assert lease1 and lease2 and lease3 is None
    await limiter.release_rate_limits(lease1, ip, "uploads")
    allowed4, lease4 = await limiter.acquire_rate_limits(ip, "uploads")
    assert allowed4 is True
    await limiter.release_rate_limits(lease2, ip, "uploads")
    await limiter.release_rate_limits(lease4, ip, "uploads")


@pytest.mark.asyncio
async def test_bypass_via_different_accounts_still_blocked_by_ip_burst():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["uploads"] = 100
    limiter.burst_limits["uploads"] = 2
    limiter.concurrency_limits["uploads"] = 100
    ip = "4.4.4.4"
    assert await limiter.check_rate_limit("uploads", "", ip=ip, account_id="acc1") is True
    assert await limiter.check_rate_limit("uploads", "", ip=ip, account_id="acc2") is True
    assert await limiter.check_rate_limit("uploads", "", ip=ip, account_id="acc3") is False
    assert await limiter.check_rate_limit("uploads", "", ip=ip, account_id="acc1") is False


@pytest.mark.asyncio
async def test_different_ips_not_interfere():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 1
    limiter.burst_limits["search"] = 100
    limiter.concurrency_limits["search"] = 100
    assert await limiter.check_rate_limit("search", "", ip="10.0.0.1") is True
    assert await limiter.check_rate_limit("search", "", ip="10.0.0.1") is False
    assert await limiter.check_rate_limit("search", "", ip="10.0.0.2") is True


@pytest.mark.asyncio
async def test_redis_error_auth_fail_closed():
    from app.middleware import RateLimiter

    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=Exception("redis down"))
    redis.expire = AsyncMock()
    limiter = RateLimiter(redis)
    assert await limiter.check_rate_limit("auth", "", ip="1.1.1.1", account_id="u1") is False


@pytest.mark.asyncio
async def test_redis_error_non_auth_fail_open():
    from app.middleware import RateLimiter

    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=Exception("redis down"))
    redis.expire = AsyncMock()
    limiter = RateLimiter(redis)
    assert await limiter.check_rate_limit("search", "", ip="1.1.1.1") is True


@pytest.mark.asyncio
async def test_device_dimension_independent():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 2
    limiter.burst_limits["search"] = 100
    limiter.concurrency_limits["search"] = 100
    ip = "5.5.5.5"
    acct = "userX"
    dev1 = "deviceA"
    dev2 = "deviceB"
    assert (
        await limiter.check_rate_limit("search", "", ip=ip, account_id=acct, device_id=dev1) is True
    )
    assert (
        await limiter.check_rate_limit("search", "", ip=ip, account_id=acct, device_id=dev1) is True
    )
    assert (
        await limiter.check_rate_limit("search", "", ip=ip, account_id=acct, device_id=dev1)
        is False
    )
    assert (
        await limiter.check_rate_limit("search", "", ip=ip, account_id=acct, device_id=dev2)
        is False
    )


@pytest.mark.asyncio
async def test_reindex_burst_limit():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["search"] = 100
    limiter.limits["reindex"] = 20
    limiter.burst_limits["reindex"] = 1
    limiter.concurrency_limits["reindex"] = 100
    ip = "6.6.6.6"
    assert await limiter.check_rate_limit("search", "/reindex", ip=ip) is True
    assert await limiter.check_rate_limit("search", "/reindex", ip=ip) is False


@pytest.mark.asyncio
async def test_gateway_uses_both_ip_and_account():
    from unittest.mock import patch

    from app.main import app
    from app.api.gateway_routes import get_optional_user
    import app.main as main
    from fastapi.testclient import TestClient

    app.dependency_overrides.clear()
    app.dependency_overrides[get_optional_user] = lambda: {"sub": "test-user"}
    mock_limiter = MagicMock()
    mock_limiter.acquire_rate_limits = AsyncMock(return_value=(True, "lease"))
    mock_limiter.release_rate_limits = AsyncMock()
    redis_stub = MagicMock()
    redis_stub.ping = AsyncMock(return_value=True)
    shared_mock = MagicMock()
    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.content = b'{"ok": true}'
    response_mock.headers = {"content-type": "application/json"}
    shared_mock.request = AsyncMock(return_value=response_mock)
    with patch("app.middleware.get_shared_client", return_value=shared_mock):
        with patch("app.api.gateway_routes.get_shared_client", return_value=shared_mock):
            with TestClient(app, base_url="http://test") as client:
                orig_limiter = main.rate_limiter
                main.rate_limiter = mock_limiter
                app.state.redis_client = redis_stub
                client.get("/search/api/v1/items", headers={"Authorization": "Bearer dummy"})
                assert mock_limiter.acquire_rate_limits.called
                args, kwargs = mock_limiter.acquire_rate_limits.call_args
                assert args[0] == "testclient"
                assert kwargs["account_id"] == "test-user"
                main.rate_limiter = orig_limiter


@pytest.mark.asyncio
async def test_gateway_rejects_when_ip_limited():
    from app.main import app
    import app.main as main
    from fastapi.testclient import TestClient

    app.dependency_overrides.clear()
    mock_limiter = MagicMock()
    mock_limiter.acquire_rate_limits = AsyncMock(return_value=(False, None))
    mock_limiter.release_rate_limits = AsyncMock()
    with TestClient(app, base_url="http://test") as client:
        orig_limiter = main.rate_limiter
        main.rate_limiter = mock_limiter
        redis_stub = MagicMock()
        redis_stub.ping = AsyncMock(return_value=True)
        app.state.redis_client = redis_stub
        resp = client.get("/search/api/v1/items")
        assert resp.status_code == 429
        main.rate_limiter = orig_limiter


@pytest.mark.asyncio
async def test_burst_not_enforced_for_non_expensive():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["content"] = 2
    limiter.burst_limits["content"] = 1
    limiter.concurrency_limits["content"] = 1
    ip = "7.7.7.7"
    assert await limiter.check_rate_limit("content", "", ip=ip) is True
    assert await limiter.check_rate_limit("content", "", ip=ip) is True
    assert await limiter.check_rate_limit("content", "", ip=ip) is False
    redis2 = FakeRedis()
    limiter2 = RateLimiter(redis2)
    limiter2.limits["content"] = 100
    limiter2.burst_limits["content"] = 1
    limiter2.concurrency_limits["content"] = 1
    assert await limiter2.check_rate_limit("content", "", ip=ip) is True
    assert await limiter2.check_rate_limit("content", "", ip=ip) is True
    assert await limiter2.check_rate_limit("content", "", ip=ip) is True


@pytest.mark.asyncio
async def test_upload_finalize_burst():
    from app.middleware import RateLimiter

    redis = FakeRedis()
    limiter = RateLimiter(redis)
    limiter.limits["uploads"] = 100
    limiter.burst_limits["uploads"] = 100
    limiter._burst_upload_finalize = 1
    limiter.concurrency_limits["uploads"] = 100
    limiter._concurrency_upload_finalize = 100
    ip = "8.8.8.8"
    assert await limiter.check_rate_limit("uploads", "/complete", ip=ip) is True
    assert await limiter.check_rate_limit("uploads", "/complete", ip=ip) is False
    assert await limiter.check_rate_limit("uploads", "", ip=ip) is True


# ---------------------------------------------------------------------------
# Per-dimension refusals: each of the 9 counters must be able to deny on its own
# ---------------------------------------------------------------------------


class ScriptedRedis:
    """Redis double whose INCR result can be pinned for individual keys.

    The three dimensions (ip / account / device) all share one limit, so an
    ordinary counter can never exhaust a downstream dimension before the ip one
    ahead of it. Scripting the INCR result per key is what makes each
    dimension's refusal independently observable.
    """

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.touched = []
        self.leases = {}

    async def incr(self, key):
        self.touched.append(key)
        return self.values.get(key, 1)

    async def expire(self, key, window):
        return True

    async def eval(self, script, numkeys, key, *args):
        self.touched.append(key)
        leases = self.leases.setdefault(key, {})
        if "ZREMRANGEBYSCORE" in script:
            lease_id, now, expiry, limit = args
            leases = {member: end for member, end in leases.items() if end > float(now)}
            self.leases[key] = leases
            if len(leases) >= int(limit):
                return 0
            leases[lease_id] = float(expiry)
            return 1
        return int(leases.pop(args[0], None) is not None)


def _limiter(redis, fixed=1, burst=100, concurrency=100, service="search"):
    from app.middleware import RateLimiter

    limiter = RateLimiter(redis)
    limiter.limits[service] = fixed
    limiter.burst_limits[service] = burst
    limiter.concurrency_limits[service] = concurrency
    return limiter


async def test_fixed_window_account_dimension_can_deny_on_its_own():
    redis = ScriptedRedis({"rate_limit:account:acct:search": 2})
    limiter = _limiter(redis, fixed=1)
    assert (
        await limiter.check_rate_limit(
            "search", "", ip="1.1.1.1", account_id="acct"
        )
        is False
    )
    # The ip counter passed, the account counter refused, and nothing further ran.
    assert redis.touched == [
        "rate_limit:ip:1.1.1.1:search",
        "rate_limit:account:acct:search",
    ]


async def test_fixed_window_device_dimension_can_deny_on_its_own():
    redis = ScriptedRedis({"rate_limit:device:dev-9:search": 2})
    limiter = _limiter(redis, fixed=1)
    assert (
        await limiter.check_rate_limit("search", "", ip="1.1.1.1", device_id="dev-9")
        is False
    )
    assert redis.touched == [
        "rate_limit:ip:1.1.1.1:search",
        "rate_limit:device:dev-9:search",
    ]


async def test_burst_account_dimension_can_deny_on_its_own():
    redis = ScriptedRedis({"rate_limit:burst:account:acct:search": 2})
    limiter = _limiter(redis, fixed=100, burst=1)
    assert (
        await limiter.check_rate_limit(
            "search", "", ip="1.1.1.1", account_id="acct"
        )
        is False
    )
    assert redis.touched == [
        "rate_limit:ip:1.1.1.1:search",
        "rate_limit:account:acct:search",
        "rate_limit:burst:ip:1.1.1.1:search",
        "rate_limit:burst:account:acct:search",
    ]


async def test_burst_device_dimension_can_deny_on_its_own():
    redis = ScriptedRedis({"rate_limit:burst:device:dev-9:search": 2})
    limiter = _limiter(redis, fixed=100, burst=1)
    assert (
        await limiter.check_rate_limit("search", "", ip="1.1.1.1", device_id="dev-9")
        is False
    )
    assert redis.touched[-1] == "rate_limit:burst:device:dev-9:search"


async def test_concurrency_account_dimension_can_deny_on_its_own():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=1)
    redis.leases["rate_limit:inflight:account:acct:search"] = {"held": float("inf")}
    allowed, lease_id = await limiter.acquire_rate_limits(
        "1.1.1.1", "search", account_id="acct"
    )
    assert allowed is False
    assert lease_id is None


async def test_concurrency_device_dimension_can_deny_on_its_own():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=1)
    redis.leases["rate_limit:inflight:device:dev-9:search"] = {"held": float("inf")}
    allowed, lease_id = await limiter.acquire_rate_limits(
        "1.1.1.1", "search", device_id="dev-9"
    )
    assert allowed is False
    assert lease_id is None


async def test_all_nine_dimensions_passing_admits_the_request():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert (
        await limiter.check_rate_limit(
            "search", "", ip="1.1.1.1", account_id="acct", device_id="dev-9"
        )
        is True
    )
    assert len(redis.touched) == 6


async def test_check_rate_limits_is_the_public_dual_dimension_entrypoint():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert (
        await limiter.check_rate_limits(
            ip="1.1.1.1",
            service="search",
            path="/api/v1/q",
            account_id="acct-1",
            device_id="dev-1",
        )
        is True
    )
    assert "rate_limit:account:acct-1:search" in redis.touched
    assert "rate_limit:device:dev-1:search" in redis.touched


# ---------------------------------------------------------------------------
# check_rate_limit: keyword-argument entry points
# ---------------------------------------------------------------------------


async def test_user_id_keyword_is_promoted_to_the_account_dimension():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit(user_id="u-1", service="search") is True
    assert "rate_limit:account:u-1:search" in redis.touched
    # No ip was supplied, so the ip dimension is keyed on the "unknown" bucket.
    assert "rate_limit:ip:unknown:search" in redis.touched


async def test_service_keyword_supplies_the_service_name():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit(ip="2.2.2.2", service="search") is True
    assert "rate_limit:ip:2.2.2.2:search" in redis.touched


async def test_missing_service_with_an_ip_is_a_programming_error():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    with pytest.raises(TypeError, match="service required"):
        await limiter.check_rate_limit(ip="2.2.2.2")


async def test_explicit_path_keyword_is_used_for_dimension_specific_limits():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    limiter.limits["reindex"] = 20
    assert (
        await limiter.check_rate_limit("search", "/reindex", ip="3.3.3.3", path="/reindex")
        is True
    )
    assert "rate_limit:ip:3.3.3.3:search" in redis.touched


async def test_omitted_path_defaults_to_empty_for_the_dual_dimensions():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit("search", ip="3.3.3.3") is True
    assert "rate_limit:ip:3.3.3.3:search" in redis.touched


# ---------------------------------------------------------------------------
# check_rate_limit: legacy positional (user, service, path) signature
# ---------------------------------------------------------------------------


async def test_legacy_positional_signature_uses_a_user_scoped_key():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit("legacy-user", "search") is True
    # The legacy path checks exactly one key: rate_limit:<user>:<service>.
    assert redis.touched == ["rate_limit:legacy-user:search"]


async def test_legacy_positional_signature_honours_a_third_path_argument():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit("legacy-user", "uploads", "/complete") is True
    assert redis.touched == ["rate_limit:legacy-user:uploads"]


async def test_legacy_signature_uses_the_reindex_limit_for_a_reindex_path():
    redis = FakeRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    limiter.limits["reindex"] = 1
    # The legacy path applies _get_limit only; bursts are not consulted.
    assert await limiter.check_rate_limit("legacy-user", "search", "/reindex") is True
    assert await limiter.check_rate_limit("legacy-user", "search", "/reindex") is False
    assert "rate_limit:legacy-user:search" in redis.counts


async def test_user_id_keyword_takes_the_account_dimension_not_the_legacy_path():
    """``user_id=`` is indistinguishable from ``account_id=`` at the boundary.

    Because the account dimension is populated before the ip/device dispatch
    check, passing ``user_id`` selects the *dual* (9-counter) code path, not the
    legacy single-key path. Pinned because it changes which limits apply.
    """
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit(user_id="kw-user", service="search") is True
    # Dual path: ip defaults to the "unknown" bucket and the account is counted.
    assert redis.touched[:2] == [
        "rate_limit:ip:unknown:search",
        "rate_limit:account:kw-user:search",
    ]
    assert "rate_limit:kw-user:search" not in redis.touched


async def test_account_id_keyword_wins_over_a_positional_user_argument():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert (
        await limiter.check_rate_limit("ignored", account_id="acct", service="search")
        is True
    )
    assert redis.touched[:2] == [
        "rate_limit:ip:unknown:search",
        "rate_limit:account:acct:search",
    ]


async def test_legacy_signature_service_keyword_is_mixed_with_a_positional_user():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit("legacy-user", service="search") is True
    assert redis.touched == ["rate_limit:legacy-user:search"]


async def test_legacy_signature_path_keyword_is_mixed_with_positional_arguments():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert (
        await limiter.check_rate_limit("legacy-user", "search", path="/reindex") is True
    )
    assert redis.touched == ["rate_limit:legacy-user:search"]


async def test_legacy_signature_without_a_service_admits_the_request():
    """No dimension can be derived, so there is nothing to enforce."""
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit("only-a-user") is True
    assert redis.touched == []


async def test_legacy_signature_with_no_arguments_at_all_admits_the_request():
    redis = ScriptedRedis()
    limiter = _limiter(redis, fixed=100, burst=100, concurrency=100)
    assert await limiter.check_rate_limit() is True
    assert redis.touched == []


async def test_legacy_signature_can_still_be_denied_by_its_own_limit():
    redis = FakeRedis()
    limiter = _limiter(redis, fixed=2, burst=100, concurrency=100)
    assert await limiter.check_rate_limit("legacy-user", "search") is True
    assert await limiter.check_rate_limit("legacy-user", "search") is True
    assert await limiter.check_rate_limit("legacy-user", "search") is False
    assert redis.expires["rate_limit:legacy-user:search"] == 60
