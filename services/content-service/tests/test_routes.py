"""Behavioural tests for the content API router (app/api/routes/__init__.py).

Every endpoint is driven over HTTP against a throwaway FastAPI app that mounts
the production router with the ``ContentService`` stand-in injected through
dependency overrides, so the tests cover the real request parsing, authz
dependencies, response models and 404 branches. The auth dependencies
(``get_current_user`` / ``get_admin_identity``) and the token-introspection
helper ``_enforce_auth_version`` are additionally called directly, because the
introspection matrix is the security boundary for catalog writes.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.exc import IntegrityError

from app.api.routes import (
    JobStatus,
    ReindexJob,
    _enforce_auth_version,
    _reindex_jobs,
    get_admin_identity,
    get_content_service,
    get_current_user,
    router,
)
from app.core.settings import settings
from app.models import (
    CastMember,
    Content,
    ContentRating,
    ContentRecommendation,
    ContentStatus,
    ContentType,
    Episode,
    Genre,
    Season,
)

pytestmark = pytest.mark.unit

# The token subject injected in place of get_current_user.
CURRENT_USER_ID = uuid4()


# ------------------------------------------------------------------- fixtures
def _genre(**overrides) -> Genre:
    data = {
        "id": uuid4(),
        "name": "Action",
        "slug": "action",
        "description": "Action movies",
        "icon_url": None,
    }
    data.update(overrides)
    return Genre(**data)


def _content(**overrides) -> Content:
    now = datetime.now(UTC)
    data = {
        "id": uuid4(),
        "title": "Space Quest",
        "slug": "space-quest",
        "description": "A sci-fi adventure",
        "content_type": ContentType.MOVIE,
        "status": ContentStatus.PUBLISHED,
        "creator_id": None,
        "release_date": now,
        "duration_minutes": 118,
        "original_language": "en",
        "country": "US",
        "poster_url": "https://cdn.test/poster.jpg",
        "backdrop_url": None,
        "trailer_url": None,
        "imdb_rating": 8.1,
        "audience_score": 91.0,
        "total_votes": 4200,
        "content_rating": "PG-13",
        "is_premium": False,
        "can_download": True,
        "can_stream": True,
        "price_usd": None,
        "created_at": now,
        "updated_at": now,
        "published_at": now,
        "genres": [],
        "cast_members": [],
        "seasons": [],
    }
    data.update(overrides)
    return Content(**data)


def _season(content_id=None, **overrides) -> Season:
    data = {
        "id": uuid4(),
        "content_id": content_id or uuid4(),
        "season_number": 1,
        "title": "Season 1",
        "description": "First season",
        "poster_url": None,
        "release_date": datetime.now(UTC),
        "episode_count": 0,
        "episodes": [],
    }
    data.update(overrides)
    return Season(**data)


def _episode(content_id=None, season_id=None, **overrides) -> Episode:
    data = {
        "id": uuid4(),
        "content_id": content_id or uuid4(),
        "season_id": season_id or uuid4(),
        "episode_number": 1,
        "title": "Pilot",
        "description": "First episode",
        "duration_minutes": 45,
        "thumbnail_url": None,
        "release_date": datetime.now(UTC),
        "is_available": True,
        "audience_score": 88.0,
    }
    data.update(overrides)
    return Episode(**data)


def _rating(**overrides) -> ContentRating:
    data = {
        "id": uuid4(),
        "content_id": uuid4(),
        "user_id": uuid4(),
        "rating": 8.5,
        "review": "Great",
        "created_at": datetime.now(UTC),
    }
    data.update(overrides)
    return ContentRating(**data)


def _recommendation(**overrides) -> ContentRecommendation:
    data = {
        "id": uuid4(),
        "content_id": uuid4(),
        "recommended_content_id": uuid4(),
        "similarity_score": 0.95,
        "recommendation_type": "similar",
    }
    data.update(overrides)
    return ContentRecommendation(**data)


def _cast_member(**overrides) -> CastMember:
    data = {
        "id": uuid4(),
        "name": "Keanu Reeves",
        "slug": "keanu-reeves",
        "bio": "Actor",
        "birth_date": datetime(1964, 9, 2, tzinfo=UTC),
        "image_url": None,
    }
    data.update(overrides)
    return CastMember(**data)


def make_service(**returns):
    """ContentService stand-in: every method an AsyncMock with a canned result."""
    service = MagicMock()
    defaults = {
        "create_genre": _genre(),
        "get_genre": _genre(),
        "list_genres": [_genre()],
        "update_genre": _genre(),
        "delete_genre": True,
        "create_content": _content(),
        "list_content": [_content()],
        "get_trending_content": [_content()],
        "get_content": _content(),
        "update_content": _content(),
        "delete_content": True,
        "publish_content": _content(status=ContentStatus.PUBLISHED),
        "create_season": _season(),
        "list_seasons": [_season()],
        "get_season": _season(),
        "update_season": _season(),
        "delete_season": True,
        "create_episode": _episode(),
        "list_episodes": [_episode()],
        "get_episode": _episode(),
        "update_episode": _episode(),
        "delete_episode": True,
        "rate_content": _rating(),
        "list_ratings": [_rating()],
        "add_recommendation": _recommendation(),
        "list_recommendations": [_recommendation()],
        "add_cast_member": _cast_member(),
        "list_cast": [_cast_member()],
    }
    for name, value in {**defaults, **returns}.items():
        setattr(service, name, AsyncMock(return_value=value))
    return service


@pytest.fixture
def service():
    return make_service()


@pytest.fixture
async def client(service):
    application = FastAPI()
    application.include_router(router)

    async def _service_override():
        return service

    application.dependency_overrides[get_content_service] = _service_override
    application.dependency_overrides[get_admin_identity] = lambda: "admin-user-id"
    application.dependency_overrides[get_current_user] = lambda: CURRENT_USER_ID
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        application.dependency_overrides.clear()


# ------------------------------------------------------------------- genres
class TestGenreEndpoints:
    async def test_create_genre(self, client, service):
        response = await client.post(
            "/api/v1/genres", json={"name": "Action", "slug": "action"}
        )

        assert response.status_code == 201
        assert response.json()["slug"] == "action"
        payload = service.create_genre.await_args.args[0]
        assert payload.name == "Action"

    async def test_create_genre_conflict_maps_integrity_error_to_409(self, client, service):
        service.create_genre.side_effect = IntegrityError("INSERT", {}, Exception("dup"))

        response = await client.post(
            "/api/v1/genres", json={"name": "Action", "slug": "action"}
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Genre with this name or slug already exists"

    async def test_list_genres(self, client):
        response = await client.get("/api/v1/genres")

        assert response.status_code == 200
        assert [g["slug"] for g in response.json()] == ["action"]

    async def test_get_genre(self, client, service):
        genre_id = uuid4()

        response = await client.get(f"/api/v1/genres/{genre_id}")

        assert response.status_code == 200
        service.get_genre.assert_awaited_once_with(genre_id)

    async def test_get_genre_missing_returns_404(self, client, service):
        service.get_genre.return_value = None

        response = await client.get(f"/api/v1/genres/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Genre not found"

    async def test_update_genre(self, client, service):
        genre_id = uuid4()

        response = await client.put(
            f"/api/v1/genres/{genre_id}", json={"name": "Sci-Fi", "slug": "sci-fi"}
        )

        assert response.status_code == 200
        service.update_genre.assert_awaited_once()

    async def test_update_genre_missing_returns_404(self, client, service):
        service.update_genre.return_value = None

        response = await client.put(
            f"/api/v1/genres/{uuid4()}", json={"name": "Sci-Fi", "slug": "sci-fi"}
        )

        assert response.status_code == 404

    async def test_delete_genre_returns_204(self, client, service):
        genre_id = uuid4()

        response = await client.delete(f"/api/v1/genres/{genre_id}")

        assert response.status_code == 204
        service.delete_genre.assert_awaited_once_with(genre_id)

    async def test_delete_genre_missing_returns_404(self, client, service):
        service.delete_genre.return_value = False

        response = await client.delete(f"/api/v1/genres/{uuid4()}")

        assert response.status_code == 404

    async def test_genre_write_requires_the_admin_dependency(self, client, service):
        """Removing the override re-exposes get_admin_identity's own 401 path."""
        application = client._transport.app  # type: ignore[attr-defined]
        application.dependency_overrides.pop(get_admin_identity)

        response = await client.post("/api/v1/genres", json={"name": "A", "slug": "a"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid authorization header"


# ------------------------------------------------------------------ content
CONTENT_BODY = {
    "title": "Space Quest",
    "slug": "space-quest",
    "description": "A sci-fi adventure",
    "content_type": "movie",
}


class TestContentEndpoints:
    async def test_create_content(self, client, service):
        response = await client.post("/api/v1/content", json=CONTENT_BODY)

        assert response.status_code == 201
        assert response.json()["title"] == "Space Quest"

    async def test_create_content_rejects_an_unknown_content_type(self, client):
        response = await client.post(
            "/api/v1/content", json={**CONTENT_BODY, "content_type": "podcast"}
        )

        assert response.status_code == 422

    async def test_create_content_rejects_a_malformed_slug(self, client):
        response = await client.post(
            "/api/v1/content", json={**CONTENT_BODY, "slug": "Not A Slug"}
        )

        assert response.status_code == 422

    async def test_list_content_defaults_to_page_one(self, client, service):
        response = await client.get("/api/v1/content")

        assert response.status_code == 200
        assert len(response.json()) == 1
        assert service.list_content.await_args.args == (1, 20, None, None, None)

    async def test_list_content_forwards_every_filter(self, client, service):
        genre_id = uuid4()

        response = await client.get(
            "/api/v1/content",
            params={
                "page": 3,
                "page_size": 5,
                "content_type": "series",
                "status": "published",
                "genre_id": str(genre_id),
            },
        )

        assert response.status_code == 200
        assert service.list_content.await_args.args == (
            3,
            5,
            "series",
            "published",
            genre_id,
        )

    async def test_list_content_rejects_a_page_size_above_100(self, client):
        response = await client.get("/api/v1/content", params={"page_size": 101})

        assert response.status_code == 422

    async def test_list_trending(self, client, service):
        response = await client.get("/api/v1/content/trending?limit=5")

        assert response.status_code == 200
        service.get_trending_content.assert_awaited_once_with(5)

    async def test_get_content(self, client, service):
        content_id = uuid4()

        response = await client.get(f"/api/v1/content/{content_id}")

        assert response.status_code == 200
        service.get_content.assert_awaited_once_with(content_id)

    async def test_get_content_missing_returns_404(self, client, service):
        service.get_content.return_value = None

        response = await client.get(f"/api/v1/content/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Content not found"

    async def test_update_content(self, client, service):
        content_id = uuid4()

        response = await client.put(
            f"/api/v1/content/{content_id}", json={"title": "Space Odyssey"}
        )

        assert response.status_code == 200
        service.update_content.assert_awaited_once()

    async def test_update_content_missing_returns_404(self, client, service):
        service.update_content.return_value = None

        response = await client.put(f"/api/v1/content/{uuid4()}", json={"title": "x"})

        assert response.status_code == 404

    async def test_delete_content_returns_204(self, client, service):
        content_id = uuid4()

        response = await client.delete(f"/api/v1/content/{content_id}")

        assert response.status_code == 204
        service.delete_content.assert_awaited_once_with(content_id)

    async def test_delete_content_missing_returns_404(self, client, service):
        service.delete_content.return_value = False

        response = await client.delete(f"/api/v1/content/{uuid4()}")

        assert response.status_code == 404

    async def test_publish_content(self, client, service):
        content_id = uuid4()

        response = await client.post(
            f"/api/v1/content/{content_id}/publish", json={"status": "published"}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "published"
        service.publish_content.assert_awaited_once()

    async def test_publish_content_missing_returns_404(self, client, service):
        service.publish_content.return_value = None

        response = await client.post(
            f"/api/v1/content/{uuid4()}/publish", json={"status": "published"}
        )

        assert response.status_code == 404


# ------------------------------------------------------------------- seasons
class TestSeasonEndpoints:
    async def test_create_season(self, client, service):
        content_id = uuid4()

        response = await client.post(
            f"/api/v1/content/{content_id}/seasons",
            json={"season_number": 1, "title": "Season 1"},
        )

        assert response.status_code == 201
        assert response.json()["season_number"] == 1

    async def test_create_season_rejects_season_number_zero(self, client):
        response = await client.post(
            f"/api/v1/content/{uuid4()}/seasons",
            json={"season_number": 0, "title": "Season 0"},
        )

        assert response.status_code == 422

    async def test_list_seasons(self, client, service):
        content_id = uuid4()

        response = await client.get(f"/api/v1/content/{content_id}/seasons")

        assert response.status_code == 200
        service.list_seasons.assert_awaited_once_with(content_id)

    async def test_get_season(self, client, service):
        content_id, season_id = uuid4(), uuid4()

        response = await client.get(
            f"/api/v1/content/{content_id}/seasons/{season_id}"
        )

        assert response.status_code == 200
        service.get_season.assert_awaited_once_with(content_id, season_id)

    async def test_get_season_missing_returns_404(self, client, service):
        service.get_season.return_value = None

        response = await client.get(f"/api/v1/content/{uuid4()}/seasons/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Season not found"

    async def test_update_season(self, client, service):
        content_id, season_id = uuid4(), uuid4()

        response = await client.put(
            f"/api/v1/content/{content_id}/seasons/{season_id}", json={"title": "S1"}
        )

        assert response.status_code == 200
        service.update_season.assert_awaited_once()

    async def test_update_season_missing_returns_404(self, client, service):
        service.update_season.return_value = None

        response = await client.put(
            f"/api/v1/content/{uuid4()}/seasons/{uuid4()}", json={"title": "S1"}
        )

        assert response.status_code == 404

    async def test_delete_season_returns_204(self, client, service):
        content_id, season_id = uuid4(), uuid4()

        response = await client.delete(
            f"/api/v1/content/{content_id}/seasons/{season_id}"
        )

        assert response.status_code == 204
        service.delete_season.assert_awaited_once_with(content_id, season_id)

    async def test_delete_season_missing_returns_404(self, client, service):
        service.delete_season.return_value = False

        response = await client.delete(f"/api/v1/content/{uuid4()}/seasons/{uuid4()}")

        assert response.status_code == 404


# ------------------------------------------------------------------ episodes
class TestEpisodeEndpoints:
    async def test_create_episode(self, client, service):
        content_id, season_id = uuid4(), uuid4()

        response = await client.post(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes",
            json={"episode_number": 1, "title": "Pilot", "duration_minutes": 45},
        )

        assert response.status_code == 201
        assert response.json()["episode_number"] == 1

    async def test_create_episode_requires_a_duration(self, client):
        response = await client.post(
            f"/api/v1/content/{uuid4()}/seasons/{uuid4()}/episodes",
            json={"episode_number": 1, "title": "Pilot"},
        )

        assert response.status_code == 422

    async def test_list_episodes(self, client, service):
        content_id, season_id = uuid4(), uuid4()

        response = await client.get(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes"
        )

        assert response.status_code == 200
        service.list_episodes.assert_awaited_once_with(content_id, season_id)

    async def test_get_episode(self, client, service):
        content_id, season_id, episode_id = uuid4(), uuid4(), uuid4()

        response = await client.get(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}"
        )

        assert response.status_code == 200
        service.get_episode.assert_awaited_once_with(content_id, season_id, episode_id)

    async def test_get_episode_missing_returns_404(self, client, service):
        service.get_episode.return_value = None

        response = await client.get(
            f"/api/v1/content/{uuid4()}/seasons/{uuid4()}/episodes/{uuid4()}"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Episode not found"

    async def test_update_episode(self, client, service):
        content_id, season_id, episode_id = uuid4(), uuid4(), uuid4()

        response = await client.put(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}",
            json={"title": "Pilot (extended)"},
        )

        assert response.status_code == 200
        service.update_episode.assert_awaited_once()

    async def test_update_episode_missing_returns_404(self, client, service):
        service.update_episode.return_value = None

        response = await client.put(
            f"/api/v1/content/{uuid4()}/seasons/{uuid4()}/episodes/{uuid4()}",
            json={"title": "x"},
        )

        assert response.status_code == 404

    async def test_delete_episode_returns_204(self, client, service):
        content_id, season_id, episode_id = uuid4(), uuid4(), uuid4()

        response = await client.delete(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}"
        )

        assert response.status_code == 204
        service.delete_episode.assert_awaited_once_with(
            content_id, season_id, episode_id
        )

    async def test_delete_episode_missing_returns_404(self, client, service):
        service.delete_episode.return_value = False

        response = await client.delete(
            f"/api/v1/content/{uuid4()}/seasons/{uuid4()}/episodes/{uuid4()}"
        )

        assert response.status_code == 404


# ------------------------------------------------------- ratings/recs/cast
class TestEngagementEndpoints:
    async def test_rate_content_uses_the_token_subject(self, client, service):
        content_id = uuid4()
        service.rate_content.return_value = _rating(user_id=CURRENT_USER_ID)

        response = await client.post(
            f"/api/v1/content/{content_id}/ratings", json={"rating": 9.0, "review": "Wow"}
        )

        assert response.status_code == 201
        args = service.rate_content.await_args.args
        assert args[0] == content_id
        assert args[1] == CURRENT_USER_ID
        assert args[2].rating == 9.0
        assert response.json()["user_id"] == str(CURRENT_USER_ID)

    async def test_rate_content_rejects_an_out_of_range_rating(self, client):
        response = await client.post(
            f"/api/v1/content/{uuid4()}/ratings", json={"rating": 11.0}
        )

        assert response.status_code == 422

    async def test_list_ratings(self, client, service):
        content_id = uuid4()

        response = await client.get(f"/api/v1/content/{content_id}/ratings")

        assert response.status_code == 200
        service.list_ratings.assert_awaited_once_with(content_id)

    async def test_add_recommendation(self, client, service):
        content_id = uuid4()
        target_id = uuid4()
        service.add_recommendation.return_value = _recommendation(
            content_id=content_id, recommended_content_id=target_id, similarity_score=0.9
        )

        response = await client.post(
            f"/api/v1/content/{content_id}/recommendations",
            json={
                "recommended_content_id": str(target_id),
                "similarity_score": 0.9,
                "recommendation_type": "similar",
            },
        )

        assert response.status_code == 201
        assert response.json()["recommended_content_id"] == str(target_id)
        assert service.add_recommendation.await_args.args[0] == content_id

    async def test_list_recommendations(self, client, service):
        content_id = uuid4()

        response = await client.get(f"/api/v1/content/{content_id}/recommendations")

        assert response.status_code == 200
        service.list_recommendations.assert_awaited_once_with(content_id)

    async def test_add_cast_member(self, client, service):
        content_id = uuid4()

        response = await client.post(
            f"/api/v1/content/{content_id}/cast",
            json={"name": "Keanu Reeves", "slug": "keanu-reeves"},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Keanu Reeves"

    async def test_add_cast_member_rejects_a_malformed_slug(self, client):
        response = await client.post(
            f"/api/v1/content/{uuid4()}/cast", json={"name": "X", "slug": "Bad Slug"}
        )

        assert response.status_code == 422

    async def test_list_cast(self, client, service):
        content_id = uuid4()

        response = await client.get(f"/api/v1/content/{content_id}/cast")

        assert response.status_code == 200
        service.list_cast.assert_awaited_once_with(content_id)


# ------------------------------------------------------------------- reindex
class TestReindexEndpoints:
    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        _reindex_jobs.clear()
        yield
        _reindex_jobs.clear()

    async def test_start_reindex_registers_an_accepted_job(self, client):
        response = await client.post("/api/v1/reindex")

        assert response.status_code == 200
        job_id = response.json()["job_id"]
        job = _reindex_jobs[UUID(job_id)]
        assert job.job_id == UUID(job_id)
        assert job.status is JobStatus.ACCEPTED
        assert job.progress == 0
        assert job.created_at is not None

    async def test_status_of_a_known_job(self, client):
        job_id = uuid4()
        _reindex_jobs[job_id] = ReindexJob(job_id=job_id, status=JobStatus.DONE, progress=100)

        response = await client.get(f"/api/v1/reindex/{job_id}")

        body = response.json()
        assert response.status_code == 200
        assert body["job_id"] == str(job_id)
        assert body["status"] == "done"
        assert body["progress"] == 100
        assert body["started_at"] is None
        assert body["completed_at"] is None
        assert body["error"] is None

    async def test_status_includes_timestamps_when_present(self, client):
        job_id = uuid4()
        started = datetime.now(UTC)
        _reindex_jobs[job_id] = ReindexJob(
            job_id=job_id,
            status=JobStatus.RUNNING,
            started_at=started,
            completed_at=started,
            error="boom",
        )

        response = await client.get(f"/api/v1/reindex/{job_id}")

        body = response.json()
        assert body["started_at"] == started.isoformat()
        assert body["completed_at"] == started.isoformat()
        assert body["error"] == "boom"

    async def test_unknown_job_returns_404(self, client):
        response = await client.get(f"/api/v1/reindex/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"


# ------------------------------------------------------------- auth helpers
def _token(**claims) -> str:
    from datetime import timedelta

    payload = {
        "sub": str(uuid4()),
        "type": "access",
        "role": "user",
        "av": 1,
        "arv": 0,
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    payload.update(claims)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _introspection_client(status_code=200, json_value=None, json_error=None, exc=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if exc is not None:
        client.get = AsyncMock(side_effect=exc)
        return client
    response = MagicMock()
    response.status_code = status_code
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = json_value if json_value is not None else {}
    client.get = AsyncMock(return_value=response)
    return client


class TestAuthVersionEnforcement:
    """``_enforce_auth_version`` is the auth-service introspection gate."""

    async def test_matching_auth_version_is_accepted(self, monkeypatch):
        client = _introspection_client(json_value={"auth_version": 3})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 3})

    async def test_camel_case_and_short_auth_version_keys_are_understood(self, monkeypatch):
        client = _introspection_client(json_value={"authVersion": "4"})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 4})

    async def test_nested_user_object_is_understood(self, monkeypatch):
        client = _introspection_client(json_value={"user": {"auth_version": 5}})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 5})

    async def test_nested_user_object_short_key_is_understood(self, monkeypatch):
        client = _introspection_client(json_value={"user": {"av": 6}})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 6})

    async def test_stale_auth_version_is_rejected(self, monkeypatch):
        client = _introspection_client(json_value={"auth_version": 9})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        with pytest.raises(HTTPException) as exc:
            await _enforce_auth_version("Bearer tok", {"av": 1})

        assert exc.value.status_code == 401
        assert exc.value.headers["WWW-Authenticate"] == "Bearer"

    async def test_missing_av_claim_defaults_to_zero(self, monkeypatch):
        client = _introspection_client(json_value={"auth_version": 0})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {})

    async def test_non_numeric_av_claim_is_rejected(self, monkeypatch):
        client = _introspection_client(json_value={"auth_version": "abc"})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        with pytest.raises(HTTPException) as exc:
            await _enforce_auth_version("Bearer tok", {"av": 1})

        assert exc.value.detail == "Invalid token"

    async def test_non_int_current_auth_version_is_rejected(self, monkeypatch):
        client = _introspection_client(json_value={"auth_version": {"n": 1}})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        with pytest.raises(HTTPException) as exc:
            await _enforce_auth_version("Bearer tok", {"av": 1})

        assert exc.value.detail == "Invalid token"

    async def test_response_without_any_auth_version_is_permissive(self, monkeypatch):
        client = _introspection_client(json_value={"user": "not-a-dict"})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 99})

    async def test_non_dict_body_is_permissive(self, monkeypatch):
        client = _introspection_client(json_value=[1, 2, 3])
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 99})

    async def test_unparsable_body_is_permissive(self, monkeypatch):
        client = _introspection_client(json_error=ValueError("not json"))
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer tok", {"av": 99})

    async def test_non_200_response_is_rejected(self, monkeypatch):
        client = _introspection_client(status_code=403)
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        with pytest.raises(HTTPException) as exc:
            await _enforce_auth_version("Bearer tok", {"av": 1})

        assert exc.value.status_code == 401

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectTimeout("timeout"),
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("read"),
        ],
    )
    async def test_transport_errors_are_rejected(self, monkeypatch, error):
        client = _introspection_client(exc=error)
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        with pytest.raises(HTTPException) as exc:
            await _enforce_auth_version("Bearer tok", {"av": 1})

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    async def test_authorization_header_is_forwarded_to_the_auth_service(self, monkeypatch):
        client = _introspection_client(json_value={})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

        await _enforce_auth_version("Bearer the-token", {"av": 0})

        args, kwargs = client.get.await_args
        assert args[0] == f"{settings.AUTH_SERVICE_URL}/api/v1/auth/me"
        assert kwargs["headers"] == {"Authorization": "Bearer the-token"}


class TestIdentityDependencies:
    @pytest.fixture(autouse=True)
    def _introspection_ok(self, monkeypatch):
        client = _introspection_client(json_value={})
        monkeypatch.setattr("app.api.routes.httpx.AsyncClient", lambda *a, **k: client)

    async def test_current_user_returns_the_sub_claim(self):
        token = _token(sub="11111111-1111-1111-1111-111111111111")

        assert await get_current_user(f"Bearer {token}") == UUID(
            "11111111-1111-1111-1111-111111111111"
        )

    async def test_refresh_tokens_are_rejected_as_access_tokens(self):
        token = _token(type="refresh")

        with pytest.raises(HTTPException) as exc:
            await get_current_user(f"Bearer {token}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid token type"

    async def test_a_token_signed_with_another_key_is_rejected(self):
        from datetime import timedelta

        forged = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "exp": datetime.now(UTC) + timedelta(minutes=15),
            },
            "not-the-service-secret",
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(HTTPException) as exc:
            await get_current_user(f"Bearer {forged}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid or expired token"

    async def test_a_token_without_a_subject_claim_is_rejected(self):
        token = _token(sub="")

        with pytest.raises(HTTPException) as exc:
            await get_current_user(f"Bearer {token}")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Token missing subject claim"

    async def test_current_user_rejects_a_missing_header(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(None)

        assert exc.value.status_code == 401

    async def test_current_user_rejects_a_non_bearer_header(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_user("Basic dXNlcjpwYXNz")

        assert exc.value.status_code == 401

    async def test_admin_identity_returns_the_subject_for_admin_tokens(self):
        token = _token(role="admin", sub="22222222-2222-2222-2222-222222222222")

        assert await get_admin_identity(f"Bearer {token}") == (
            "22222222-2222-2222-2222-222222222222"
        )

    async def test_admin_identity_rejects_non_admin_roles(self):
        token = _token(role="user")

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(f"Bearer {token}")

        assert exc.value.status_code == 403
        assert exc.value.detail == "Administrator privileges required"

    async def test_admin_identity_rejects_a_stale_role_version(self, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_ROLE_VERSION", 4)
        token = _token(role="admin", arv=3)

        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(f"Bearer {token}")

        assert exc.value.status_code == 403
        assert exc.value.detail == "Administrator privileges required"

    async def test_admin_identity_rejects_a_missing_header(self):
        with pytest.raises(HTTPException) as exc:
            await get_admin_identity(None)

        assert exc.value.status_code == 401
