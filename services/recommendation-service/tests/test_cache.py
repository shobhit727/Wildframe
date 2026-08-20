"""Tests for recommendation cache with jittered TTL (#456)."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_jittered_ttl_in_range():
    """_jittered_ttl returns value within base ± jitter."""
    from app.services import (
        _jittered_ttl,
        RECOMMENDATION_CACHE_TTL_SECONDS,
        RECOMMENDATION_CACHE_JITTER_SECONDS,
    )

    for _ in range(100):
        ttl = _jittered_ttl()
        assert (
            RECOMMENDATION_CACHE_TTL_SECONDS - RECOMMENDATION_CACHE_JITTER_SECONDS
            <= ttl
            <= (RECOMMENDATION_CACHE_TTL_SECONDS + RECOMMENDATION_CACHE_JITTER_SECONDS)
        )


@pytest.mark.asyncio
async def test_cache_key_format():
    """_cache_key produces expected namespace format."""
    from app.services import _cache_key
    from uuid import UUID

    user_id = UUID(str(uuid4()))
    key = _cache_key(user_id)
    assert key == f"wf:rec:user:{user_id}"


@pytest.mark.asyncio
async def test_cache_get_returns_none_when_redis_unavailable(monkeypatch):
    """_cache_get returns None when Redis client is None."""
    from app.services import _cache_get

    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=None))

    result = await _cache_get(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_cache_set_succeeds_silently_when_redis_unavailable(monkeypatch):
    """_cache_set does not raise when Redis is unavailable."""
    from app.services import _cache_set

    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=None))

    await _cache_set(uuid4(), [{"content_id": "abc", "score": 1.0, "reason": "test"}])
    # No assertion needed; just verifies no exception


@pytest.mark.asyncio
async def test_cache_invalidate_succeeds_silently_when_redis_unavailable(monkeypatch):
    """_cache_invalidate does not raise when Redis is unavailable."""
    from app.services import _cache_invalidate

    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=None))

    await _cache_invalidate(uuid4())
    # No assertion needed; just verifies no exception


@pytest.mark.asyncio
async def test_cache_set_uses_jittered_ttl(monkeypatch):
    """_cache_set calls Redis SET with jittered TTL (ex parameter)."""
    from app.services import _cache_set

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=mock_redis))

    # Capture the TTL passed to set()
    captured = {}

    async def capture_set(key, value, ex=None, **kwargs):
        captured["key"] = key
        captured["value"] = value
        captured["ex"] = ex

    mock_redis.set.side_effect = capture_set

    user_id = uuid4()
    recommendations = [{"content_id": "abc", "score": 1.0, "reason": "test"}]
    await _cache_set(user_id, recommendations)

    assert captured["key"] == f"wf:rec:user:{user_id}"
    assert json.loads(captured["value"]) == recommendations
    # Verify TTL is in the expected jittered range
    from app.services import RECOMMENDATION_CACHE_TTL_SECONDS, RECOMMENDATION_CACHE_JITTER_SECONDS

    assert (
        RECOMMENDATION_CACHE_TTL_SECONDS - RECOMMENDATION_CACHE_JITTER_SECONDS
        <= captured["ex"]
        <= RECOMMENDATION_CACHE_TTL_SECONDS + RECOMMENDATION_CACHE_JITTER_SECONDS
    )


@pytest.mark.asyncio
async def test_cache_get_returns_deserialized_data(monkeypatch):
    """_cache_get returns deserialized JSON from Redis."""
    from app.services import _cache_get

    mock_redis = AsyncMock()
    user_id = uuid4()
    stored_data = [{"content_id": "xyz", "score": 2.5, "reason": "cached"}]
    mock_redis.get = AsyncMock(return_value=json.dumps(stored_data))
    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=mock_redis))

    result = await _cache_get(user_id)

    assert result == stored_data
    mock_redis.get.assert_awaited_once_with(f"wf:rec:user:{user_id}")


@pytest.mark.asyncio
async def test_cache_invalidate_calls_delete(monkeypatch):
    """_cache_invalidate calls Redis DEL on the user key."""
    from app.services import _cache_invalidate

    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()
    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=mock_redis))

    user_id = uuid4()
    await _cache_invalidate(user_id)

    mock_redis.delete.assert_awaited_once_with(f"wf:rec:user:{user_id}")
