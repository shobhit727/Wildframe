"""Tests for the shared ContentCatalogClient lifecycle.

Regression tests for the [BUG] finding that a new httpx client was created
(and closed) per recommendation generation, preventing connection reuse.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.services import (
    ContentCatalogClient,
    close_catalog_client,
    get_catalog_client,
    RecommendationService,
)

GENRE_ID = str(uuid4())


def make_client(**methods):
    client = MagicMock()
    client.aclose = AsyncMock()
    for name, value in methods.items():
        setattr(client, name, value)
    return client


@pytest.fixture(autouse=True)
async def reset_shared_client():
    await close_catalog_client()
    yield
    await close_catalog_client()


@pytest.fixture
def service():
    pref_repo = MagicMock()
    pref_repo.session = AsyncMock()
    rec_repo = MagicMock()
    rec_repo.create = AsyncMock(return_value=MagicMock())
    rec_repo.clear_for_user = AsyncMock()
    return RecommendationService(pref_repo, rec_repo)


@pytest.mark.asyncio
async def test_generate_reuses_client_across_calls(service):
    """Two generations inside one scope share one client; ctor runs once."""
    client = make_client(
        fetch_genres=AsyncMock(return_value=[]),
        fetch_global=AsyncMock(return_value=[]),
    )
    with patch("app.services.ContentCatalogClient", return_value=client) as ctor:
        first = await service.generate(uuid4(), [], [], limit=10)
        second = await service.generate(uuid4(), [], [], limit=10)
        assert first == 0 and second == 0
        ctor.assert_called_once()
        assert get_catalog_client() is client
        client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_generations_share_one_client(service):
    """A burst of generations still uses a single client / constructor."""
    client = make_client(
        fetch_genres=AsyncMock(return_value=[]),
        fetch_global=AsyncMock(return_value=[]),
    )
    with patch("app.services.ContentCatalogClient", return_value=client) as ctor:
        for _ in range(3):
            await service.generate(uuid4(), [], [], limit=10)
        ctor.assert_called_once()
        assert get_catalog_client() is client


@pytest.mark.asyncio
async def test_close_catalog_client_closes_and_resets(service):
    """close_catalog_client closes the shared client and releases it."""
    client = make_client(
        fetch_genres=AsyncMock(return_value=[]), fetch_global=AsyncMock(return_value=[])
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        await service.generate(uuid4(), [], [], limit=10)
        assert get_catalog_client() is client
        await close_catalog_client()
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_on_liked_genre_leaves_client_usable(service):
    """A content-service timeout on one genre must not kill the shared client."""

    async def flaky_fetch_by_genre(genre_id):
        raise httpx.ConnectTimeout("connect timed out")

    client = make_client(
        fetch_genres=AsyncMock(return_value=[{"id": GENRE_ID, "name": "Action", "slug": "action"}]),
        fetch_by_genre=flaky_fetch_by_genre,
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        count = await service.generate(uuid4(), ["action"], [], limit=10)
        assert count == 0
        assert get_catalog_client() is client
        client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_genres_timeout_propagates_but_client_kept(service):
    """A full catalog failure propagates but the shared client survives."""

    async def raise_timeout():
        raise httpx.ConnectTimeout("catalog unreachable")

    client = make_client(fetch_genres=raise_timeout)
    with patch("app.services.ContentCatalogClient", return_value=client):
        with pytest.raises(httpx.ConnectTimeout):
            await service.generate(uuid4(), ["action"], [], limit=10)
        assert get_catalog_client() is client
        client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_client_requested_after_close(service):
    """After close_catalog_client a later generation builds a fresh client."""
    first = make_client(
        fetch_genres=AsyncMock(return_value=[]), fetch_global=AsyncMock(return_value=[])
    )
    second = make_client(
        fetch_genres=AsyncMock(return_value=[]), fetch_global=AsyncMock(return_value=[])
    )
    with patch("app.services.ContentCatalogClient", side_effect=[first, second]):
        await service.generate(uuid4(), [], [], limit=10)
        assert get_catalog_client() is first
        await close_catalog_client()
        await service.generate(uuid4(), [], [], limit=10)
        first.aclose.assert_awaited_once()
        assert get_catalog_client() is second


@pytest.mark.asyncio
async def test_real_client_has_bounded_connection_limits():
    """The production client configures bounded pooling, not unbounded."""
    with patch("httpx.AsyncClient") as httpx_cls:
        ContentCatalogClient()
        _, kwargs = httpx_cls.call_args
        limits = kwargs["limits"]
        assert isinstance(limits, httpx.Limits)
        assert limits.max_connections > 0
        assert limits.max_keepalive_connections <= limits.max_connections
