import pytest
from unittest.mock import AsyncMock, MagicMock


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.expires = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):
        self.expires[key] = window
        return True


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
    assert await limiter.check_rate_limit("uploads", "", ip=ip) is True
    assert await limiter.check_rate_limit("uploads", "", ip=ip) is True
    assert await limiter.check_rate_limit("uploads", "", ip=ip) is False


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
    import app.main as main
    from fastapi.testclient import TestClient

    app.dependency_overrides.clear()
    mock_limiter = MagicMock()
    mock_limiter.check_rate_limit = AsyncMock(return_value=True)
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
                assert mock_limiter.check_rate_limit.called
                _, kwargs = mock_limiter.check_rate_limit.call_args
                assert "ip" in kwargs
                assert "account_id" in kwargs
                main.rate_limiter = orig_limiter


@pytest.mark.asyncio
async def test_gateway_rejects_when_ip_limited():
    from app.main import app
    import app.main as main
    from fastapi.testclient import TestClient

    app.dependency_overrides.clear()
    mock_limiter = MagicMock()
    mock_limiter.check_rate_limit = AsyncMock(return_value=False)
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
