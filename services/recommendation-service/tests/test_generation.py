"""Tests for the recommendation generation logic."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services import RecommendationService

GENRE_ID = str(uuid4())


def fake_genres():
    return [
        {"id": GENRE_ID, "name": "Action", "slug": "action"},
        {"id": str(uuid4()), "name": "Sci-Fi", "slug": "scifi"},
    ]


def fake_items(genre_id):
    return [
        {
            "id": str(uuid4()),
            "title": "Die Hard",
            "audience_score": 90,
            "genres": [{"id": genre_id, "name": "Action", "slug": "action"}],
        },
        {
            "id": str(uuid4()),
            "title": "Drama",
            "audience_score": 60,
            "genres": [{"id": genre_id, "name": "Action", "slug": "action"}],
        },
    ]


@pytest.fixture
def service():
    pref_repo = MagicMock()
    pref_repo.session = AsyncMock()
    rec_repo = MagicMock()
    rec_repo.create = AsyncMock(return_value=MagicMock())
    rec_repo.clear_for_user = AsyncMock()
    return RecommendationService(pref_repo, rec_repo)


def make_client(**methods):
    client = MagicMock()
    client.aclose = AsyncMock()
    for name, value in methods.items():
        setattr(client, name, value)
    return client


@pytest.mark.asyncio
async def test_generate_scores_liked_genres(service):
    """Content from a liked genre is stored, ranked by audience score."""
    client = make_client(
        fetch_genres=AsyncMock(return_value=fake_genres()),
        fetch_by_genre=AsyncMock(side_effect=fake_items),
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        count = await service.generate(uuid4(), ["action"], [], limit=10)

    assert count == 2
    service.rec_repo.clear_for_user.assert_awaited_once()
    assert service.rec_repo.create.await_count == 2
    service.pref_repo.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_uses_global_fallback_without_prefs(service):
    """Without liked genres the catalog fallback rail is used."""
    client = make_client(
        fetch_genres=AsyncMock(return_value=fake_genres()),
        fetch_global=AsyncMock(return_value=fake_items(None)),
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        count = await service.generate(uuid4(), [], [], limit=10)

    assert count == 2
    service.rec_repo.create.assert_awaited()


@pytest.mark.asyncio
async def test_get_recommendations_generates_when_storage_empty(service):
    """Empty stored list triggers lazy generation with the preference seed."""
    service.pref_repo.get_or_create = AsyncMock(
        return_value=MagicMock(liked_genres=["action"], disliked_genres=[])
    )
    row = MagicMock(content_id=uuid4(), score=0.9, reason="Because you like Action")
    service.rec_repo.get_for_user = AsyncMock(side_effect=[[], [row]])
    client = make_client(
        fetch_genres=AsyncMock(return_value=fake_genres()),
        fetch_by_genre=AsyncMock(side_effect=lambda gid: [fake_items(gid)[0]]),
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        recs = await service.get_recommendations(uuid4())

    assert len(recs) == 1
    assert "reason" in recs[0]


@pytest.mark.asyncio
async def test_update_preferences_regenerates(service):
    prefs = MagicMock(liked_genres=None, disliked_genres=None)
    service.pref_repo.get_or_create = AsyncMock(return_value=prefs)
    client = make_client(
        fetch_genres=AsyncMock(return_value=fake_genres()),
        fetch_global=AsyncMock(return_value=[]),
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        await service.update_preferences(uuid4(), ["action"], ["horror"])

    service.rec_repo.clear_for_user.assert_awaited_once()
    service.pref_repo.session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_generate_excludes_disliked_genre_expressed_by_name(service):
    """Disliked genres expressed by name (not slug) must still be excluded.

    Regression for the [SECURITY] finding that disliked name strings were
    only compared against genre slugs, so "Action" failed to exclude
    content whose genre slug was "action".
    """
    # Catalog exposes one genre named "Action" with slug "action".
    action_id = str(uuid4())
    genres = [{"id": action_id, "name": "Action", "slug": "action"}]
    # Two items: one belongs to the Action genre (should be excluded),
    # the other belongs to a different genre (should be kept).
    other_id = str(uuid4())
    kept_id = str(uuid4())
    global_items = [
        {
            "id": kept_id,
            "title": "Other Movie",
            "audience_score": 70,
            "genres": [{"id": other_id, "name": "Other", "slug": "other"}],
        },
        {
            "id": str(uuid4()),
            "title": "Action Movie",
            "audience_score": 95,
            "genres": [{"id": action_id, "name": "Action", "slug": "action"}],
        },
    ]
    client = make_client(
        fetch_genres=AsyncMock(return_value=genres),
        fetch_global=AsyncMock(return_value=global_items),
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        # Disliked expressed only by name (capitalised, not as slug).
        count = await service.generate(uuid4(), [], ["Action"], limit=10)

    assert count == 1
    # The one recommendation must be the non-Action item.
    args, kwargs = service.rec_repo.create.await_args_list[0]
    assert kwargs.get("content_id") == UUID(kept_id) or (args and args[1] == UUID(kept_id))


@pytest.mark.asyncio
async def test_generate_excludes_disliked_genre_expressed_by_slug(service):
    """Slug-based disliked genres continue working alongside the new
    name resolution path (regression guard)."""
    action_id = str(uuid4())
    genres = [{"id": action_id, "name": "Action", "slug": "action"}]
    kept_id = str(uuid4())
    other_id = str(uuid4())
    global_items = [
        {"id": kept_id, "audience_score": 70,
         "genres": [{"id": other_id, "name": "Other", "slug": "other"}]},
        {"id": str(uuid4()), "audience_score": 95,
         "genres": [{"id": action_id, "name": "Action", "slug": "action"}]},
    ]
    client = make_client(
        fetch_genres=AsyncMock(return_value=genres),
        fetch_global=AsyncMock(return_value=global_items),
    )
    with patch("app.services.ContentCatalogClient", return_value=client):
        # Disliked expressed by slug "action" — already worked pre-fix.
        count = await service.generate(uuid4(), [], ["action"], limit=10)

    assert count == 1
    args, kwargs = service.rec_repo.create.await_args_list[0]
    assert kwargs.get("content_id") == UUID(kept_id) or (args and args[1] == UUID(kept_id))
