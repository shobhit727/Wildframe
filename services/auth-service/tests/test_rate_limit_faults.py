"""Redis rate-limiter fault-injection tests (#214).

Pins the documented fail-open contract: a Redis outage or corrupt data must
never turn the limiter into an authorization gate (it is defense-in-depth),
keys are namespaced + PII-hashed, and every key carries a TTL so nothing
grows indefinitely.
"""

from datetime import timedelta
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
    assert await allow("probe-key", max_requests=5, window_seconds=60) is True


async def test_redis_error_fails_open():
    client = AsyncMock()
    client.pipeline.side_effect = Exception("connection refused")
    with patch("app.core.rate_limit._get_client", return_value=client):
        assert await allow("probe-key", max_requests=5, window_seconds=60) is True


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
        assert await allow("probe-key", max_requests=5, window_seconds=60, cooldown_seconds=30) is True
    client.set.assert_awaited_once()
    cooldown_key = client.set.call_args.args[0]
    assert cooldown_key.startswith("rl:cooldown:")
    assert client.set.call_args.kwargs["ex"] == 30

    client.set = AsyncMock(return_value=False)  # second send within cooldown
    with patch("app.core.rate_limit._get_client", return_value=client):
        assert await allow("probe-key", max_requests=5, window_seconds=60, cooldown_seconds=30) is False