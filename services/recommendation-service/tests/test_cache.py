"""Tests for recommendation cache with jittered TTL (#456)."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_jittered_ttl_is_the_base_offset_by_the_jitter_draw(monkeypatch):
    """``_jittered_ttl`` is ``base + randint(-jitter, +jitter)``, exactly.

    The previous version of this file asserted only ``base - jitter <= ttl <=
    base + jitter``.  The un-jittered TTL (300) sits *inside* that range, so
    deleting the jitter entirely -- the mutation this constant exists to
    prevent (#456) -- satisfied the assertion written to detect it.  Pinning
    the arithmetic instead makes the deletion visible: with the draw forced to
    0 the result must be the bare base, and forced to +/-jitter it must be the
    corresponding edge.
    """
    import random

    from app.services import (
        _jittered_ttl,
        RECOMMENDATION_CACHE_TTL_SECONDS,
        RECOMMENDATION_CACHE_JITTER_SECONDS,
    )

    def with_draw(draw, monkeypatch):
        monkeypatch.setattr(random, "randint", lambda low, high: draw)
        return _jittered_ttl()

    # No jitter at all -> the bare base, not something else in the window.
    assert with_draw(0, monkeypatch) == RECOMMENDATION_CACHE_TTL_SECONDS

    # Full positive / negative draw -> the documented window edges.
    assert (
        with_draw(RECOMMENDATION_CACHE_JITTER_SECONDS, monkeypatch)
        == RECOMMENDATION_CACHE_TTL_SECONDS + RECOMMENDATION_CACHE_JITTER_SECONDS
    )
    assert (
        with_draw(-RECOMMENDATION_CACHE_JITTER_SECONDS, monkeypatch)
        == RECOMMENDATION_CACHE_TTL_SECONDS - RECOMMENDATION_CACHE_JITTER_SECONDS
    )

    # The bounds handed to the draw are the documented ones, so a widened or
    # narrowed window cannot pass unnoticed.
    seen_bounds = []
    monkeypatch.setattr(random, "randint", lambda low, high: seen_bounds.append((low, high)) or 0)
    _jittered_ttl()
    assert seen_bounds == [
        (-RECOMMENDATION_CACHE_JITTER_SECONDS, RECOMMENDATION_CACHE_JITTER_SECONDS)
    ]


@pytest.mark.asyncio
async def test_jittered_ttl_actually_varies_between_calls():
    """Successive draws differ, so cache entries cannot expire in lockstep.

    This is the property a bound assertion cannot express: 200 samples from a
    121-value window colliding on a single value has probability ~121**-199.
    """
    from app.services import _jittered_ttl

    assert len({_jittered_ttl() for _ in range(200)}) > 1


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
async def test_a_disabled_cache_is_a_clean_miss_not_a_swallowed_failure(monkeypatch, caplog):
    """A disabled cache (``get_redis_client()`` -> None) is not an error.

    The two tests this replaces drove only the ``client is None`` early return
    and carried the comment "No assertion needed", so they could fail only by
    raising.  Raising is not actually all that is at stake: the early return is
    what separates "this deployment has no cache configured" from "the cache
    is broken".  Delete it and ``None.get(...)`` raises ``AttributeError``,
    which the surrounding ``except Exception`` swallows into a
    "Redis cache ... failed" warning -- so every Redis-less deployment starts
    logging cache failures it does not have, and the signal that distinguishes
    the two states is gone.

    The fail-open path when a client *does* exist is already covered by
    ``test_app_lifecycle.TestCacheHelpers`` and
    ``test_core_events_gaps.TestCacheInvalidationContract``; this covers only
    the guard they cannot distinguish.
    """
    from app.services import _cache_get, _cache_invalidate, _cache_set

    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=None))

    with caplog.at_level("WARNING", logger="app.services"):
        assert await _cache_get(uuid4()) is None
        assert await _cache_set(uuid4(), [{"content_id": "abc", "score": 1.0}]) is None
        assert await _cache_invalidate(uuid4()) is None

    assert [r.message for r in caplog.records if "Redis cache" in r.message] == []


@pytest.mark.asyncio
async def test_cache_set_uses_jittered_ttl(monkeypatch):
    """_cache_set forwards a *jittered* expiry to Redis, and it varies.

    The previous version asserted ``240 <= ex <= 360``.  The un-jittered TTL is
    300, inside that window, so removing the +/-60s jitter added for #456
    satisfied the assertion meant to detect it.  Asserting variation across
    repeated writes is what a bound cannot express: a fixed TTL produces one
    distinct value, a jittered one produces many.
    """
    from app.services import _cache_set

    captured: list = []

    async def capture_set(key, value, ex=None, **kwargs):
        captured.append((key, value, ex))

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=capture_set)
    monkeypatch.setattr("app.services.get_redis_client", AsyncMock(return_value=mock_redis))

    user_id = uuid4()
    recommendations = [{"content_id": "abc", "score": 1.0, "reason": "test"}]
    for _ in range(50):
        await _cache_set(user_id, recommendations)

    # Key and payload are unchanged across writes...
    for key, value, _ex in captured:
        assert key == f"wf:rec:user:{user_id}"
        assert json.loads(value) == recommendations
    # ...and the payload really is JSON, which is the only thing _cache_get's
    # round-trip test cannot see (it supplies its own stored string).
    assert captured[0][1] == json.dumps(recommendations)

    assert all(ex is not None for _, _, ex in captured)
    # A jittered TTL varies; a fixed one would be a single repeated value.
    assert len({ex for _, _, ex in captured}) > 1


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
