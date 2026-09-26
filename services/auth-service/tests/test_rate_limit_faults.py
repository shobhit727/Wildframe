"""Redis rate-limiter fault-injection tests (#214 / #792).

Auth rate limiting is fail-closed on Redis errors to prevent brute-force
during outages. Keys are namespaced + PII-hashed, and every key carries a
TTL so nothing grows indefinitely.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import rate_limit
from app.core.rate_limit import allow


@pytest.fixture(autouse=True)
def _no_global_client():
    saved = rate_limit._client
    rate_limit._client = None
    yield
    rate_limit._client = saved


async def test_no_redis_client_fails_open():
    assert await allow("probe-key", max_requests=5, window_seconds=60) is False


async def test_redis_error_fails_open():
    client = AsyncMock()
    client.pipeline.side_effect = Exception("connection refused")
    with patch("app.core.rate_limit._get_client", return_value=client):
        assert await allow("probe-key", max_requests=5, window_seconds=60) is False


async def test_over_limit_rejected():
    client = AsyncMock()
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[11])
    cm = MagicMock()
    cm.__aenter__.return_value = pipe
    client.pipeline = MagicMock(return_value=cm)
    with patch("app.core.rate_limit._get_client", return_value=client):
        assert await allow("probe-key", max_requests=5, window_seconds=60) is False


async def test_under_limit_allowed_with_ttl_and_namespace():
    client = AsyncMock()
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1])
    cm = MagicMock()
    cm.__aenter__.return_value = pipe
    client.pipeline = MagicMock(return_value=cm)
    with patch("app.core.rate_limit._get_client", return_value=client):
        assert await allow("probe-key", max_requests=5, window_seconds=60) is True

    key = pipe.incr.call_args.args[0]
    assert key.startswith("rl:token:")
    assert "probe-key" not in key  # PII/raw keys are hashed
    pipe.expire.assert_called_once_with(key, 60)


async def test_cooldown_spacing():
    client = AsyncMock()
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1])
    cm = MagicMock()
    cm.__aenter__.return_value = pipe
    client.pipeline = MagicMock(return_value=cm)
    client.set = AsyncMock(return_value=True)  # first send creates the flag

    with patch("app.core.rate_limit._get_client", return_value=client):
        assert (
            await allow("probe-key", max_requests=5, window_seconds=60, cooldown_seconds=30) is True
        )
    client.set.assert_awaited_once()
    cooldown_key = client.set.call_args.args[0]
    assert cooldown_key.startswith("rl:cooldown:")
    assert client.set.call_args.kwargs["ex"] == 30

    client.set = AsyncMock(return_value=False)  # second send within cooldown
    with patch("app.core.rate_limit._get_client", return_value=client):
        assert (
            await allow("probe-key", max_requests=5, window_seconds=60, cooldown_seconds=30)
            is False
        )


async def test_client_is_created_lazily_from_the_configured_url(monkeypatch):
    """``_get_client`` builds a ``redis.asyncio`` client from settings once."""
    settings = rate_limit.settings

    created = {}

    class _FakeRedis:
        @staticmethod
        def from_url(url, **kwargs):
            created["url"] = url
            created["kwargs"] = kwargs
            return MagicMock(name="redis-client")

    monkeypatch.setattr(rate_limit, "Redis", _FakeRedis)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://cache.internal:6379/2")

    client = rate_limit._get_client()

    assert client is not None
    assert created["url"] == "redis://cache.internal:6379/2"
    assert created["kwargs"] == {"decode_responses": True}
    # Second call reuses the cached client.
    assert rate_limit._get_client() is client
    assert created["url"] == "redis://cache.internal:6379/2"


async def test_malformed_redis_url_degrades_to_no_client(monkeypatch):
    """A bad REDIS_URL must not crash; the module degrades to fail-closed."""
    settings = rate_limit.settings

    monkeypatch.setattr(settings, "REDIS_URL", "not-a-redis-url")

    assert rate_limit._get_client() is None
    # And with no client every call is refused.
    assert await allow("probe-key", max_requests=5, window_seconds=60) is False


async def test_missing_redis_url_degrades_to_no_client(monkeypatch):
    settings = rate_limit.settings

    monkeypatch.setattr(settings, "REDIS_URL", None)

    assert rate_limit._get_client() is None


async def test_close_client_disposes_and_clears_the_cache():
    client = AsyncMock()
    rate_limit._client = client

    await rate_limit.close_client()

    client.aclose.assert_awaited_once()
    assert rate_limit._client is None


async def test_close_client_is_a_noop_without_a_client():
    rate_limit._client = None

    await rate_limit.close_client()

    assert rate_limit._client is None


async def test_cooldown_flag_uses_a_separate_namespaced_key():
    client = AsyncMock()
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1])
    cm = MagicMock()
    cm.__aenter__.return_value = pipe
    client.pipeline = MagicMock(return_value=cm)
    client.set = AsyncMock(return_value=True)

    with patch("app.core.rate_limit._get_client", return_value=client):
        await allow(
            "user@example.com", max_requests=5, window_seconds=60, cooldown_seconds=120
        )

    token_key = pipe.incr.call_args.args[0]
    cooldown_key = client.set.call_args.args[0]
    assert token_key.startswith("rl:token:")
    assert cooldown_key.startswith("rl:cooldown:")
    assert token_key != cooldown_key
    # The email must not appear in either Redis key.
    assert "user@example.com" not in token_key
    assert "user@example.com" not in cooldown_key
    assert client.set.call_args.kwargs == {"nx": True, "ex": 120}


async def test_scope_is_a_stable_blake2s_digest():
    first = rate_limit._scope("some-key")
    second = rate_limit._scope("some-key")

    assert first == second
    assert len(first) == 40  # blake2s digest_size=20 -> 40 hex chars
    assert "some-key" not in first


def test_get_client_returns_none_when_settings_raise(monkeypatch):
    from app.core import settings

    class _Exploding:
        @property
        def REDIS_URL(self):
            raise RuntimeError("settings unavailable")

    monkeypatch.setattr(rate_limit, "settings", _Exploding())

    assert rate_limit._get_client() is None
