"""Route-level tests for the Content Service HTTP API.

These exercise the real FastAPI router -> service call chain via
``httpx.ASGITransport`` with ``get_content_service`` dependency-overridden to
inject a service backed by fake repositories. They guard the HTTP contract
(status codes, response models, 404/401 behaviour) and the argument contract
between routes and the service layer (catching the arity mismatches that
previously made ``get_season``/``get_episode``/``update_season``/
``update_episode`` raise ``TypeError`` at runtime).
"""

from unittest.mock import AsyncMock, MagicMock
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_admin_identity, get_content_service, get_current_user
from app.main import app
from app.models import ContentStatus, ContentType
from app.services import ContentService


def make_fake_service() -> ContentService:
    """Build a ContentService whose repositories are fakes."""
    service = MagicMock(spec=ContentService, wraps=None)
    service.content_repo = AsyncMock()
    service.genre_repo = AsyncMock()
    service.cast_repo = AsyncMock()
    service.season_repo = AsyncMock()
    service.episode_repo = AsyncMock()
    service.rating_repo = AsyncMock()
    service.recommendation_repo = AsyncMock()
    return service


@pytest.fixture
def fake_service():
    return make_fake_service()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture(autouse=True)
def override_deps(fake_service, user_id):
    app.dependency_overrides[get_content_service] = lambda: fake_service
    app.dependency_overrides[get_current_user] = lambda: user_id
    app.dependency_overrides[get_admin_identity] = lambda: str(user_id)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def genre_id():
    return uuid4()


@pytest.fixture
def content_id():
    return uuid4()


@pytest.fixture
def season_id():
    return uuid4()


@pytest.fixture
def episode_id():
    return uuid4()


def make_genre(id_value=None):
    g = MagicMock()
    g.id = id_value or uuid4()
    g.name = "Action"
    g.slug = "action"
    g.description = "Explosions"
    g.icon_url = None
    return g


def make_content(id_value=None):
    c = MagicMock()
    c.id = id_value or uuid4()
    c.creator_id = None
    c.title = "Test Movie"
    c.slug = "test-movie"
    c.description = "A test movie"
    c.content_type = ContentType.MOVIE
    c.status = ContentStatus.DRAFT
    c.release_date = None
    c.duration_minutes = 120
    c.original_language = "en"
    c.country = None
    c.poster_url = None
    c.backdrop_url = None
    c.trailer_url = None
    c.imdb_rating = None
    c.content_rating = None
    c.is_premium = False
    c.can_download = True
    c.can_stream = True
    c.audience_score = 0.0
    c.total_votes = 0
    c.published_at = None
    c.genres = []
    c.cast_members = []
    c.seasons = []
    c.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    c.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return c


class TestGenreRoutes:
    def test_create_genre_returns_201(self, client, fake_service, genre_id):
        genre = make_genre(genre_id)
        fake_service.create_genre = AsyncMock(return_value=genre)

        response = client.post(
            "/api/v1/genres", json={"name": "Action", "slug": "action", "description": "Explosions"}
        )

        assert response.status_code == 201
        assert response.json()["id"] == str(genre_id)
        assert response.json()["name"] == "Action"
        fake_service.create_genre.assert_awaited_once()

    def test_create_genre_rejects_invalid_slug(self, client):
        response = client.post("/api/v1/genres", json={"name": "Action", "slug": "Action!"})

        assert response.status_code == 422

    def test_list_genres(self, client, fake_service):
        fake_service.list_genres = AsyncMock(return_value=[make_genre(), make_genre()])

        response = client.get("/api/v1/genres")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_get_genre_returns_200(self, client, fake_service, genre_id):
        fake_service.get_genre = AsyncMock(return_value=make_genre(genre_id))

        response = client.get(f"/api/v1/genres/{genre_id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(genre_id)

    def test_get_genre_missing_returns_404(self, client, fake_service, genre_id):
        fake_service.get_genre = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/genres/{genre_id}")

        assert response.status_code == 404

    def test_update_genre(self, client, fake_service, genre_id):
        genre = make_genre(genre_id)
        fake_service.update_genre = AsyncMock(return_value=genre)

        response = client.put(
            f"/api/v1/genres/{genre_id}", json={"name": "Action", "slug": "action"}
        )

        assert response.status_code == 200
        fake_service.update_genre.assert_awaited_once()

    def test_update_genre_missing_returns_404(self, client, fake_service, genre_id):
        fake_service.update_genre = AsyncMock(return_value=None)

        response = client.put(
            f"/api/v1/genres/{genre_id}", json={"name": "Action", "slug": "action"}
        )

        assert response.status_code == 404

    def test_delete_genre_returns_204(self, client, fake_service, genre_id):
        fake_service.delete_genre = AsyncMock(return_value=True)

        response = client.delete(f"/api/v1/genres/{genre_id}")

        assert response.status_code == 204

    def test_delete_genre_missing_returns_404(self, client, fake_service, genre_id):
        fake_service.delete_genre = AsyncMock(return_value=False)

        response = client.delete(f"/api/v1/genres/{genre_id}")

        assert response.status_code == 404


class TestWriteAuthz:
    """Catalog mutations must be admin-only, independently of the gateway (#51)."""

    WRITE_ROUTES = [
        ("post", "/api/v1/genres", {"name": "Action", "slug": "action"}),
        (
            "post",
            "/api/v1/content",
            {"title": "T", "slug": "t", "description": "d", "content_type": "movie"},
        ),
        ("post", "/api/v1/content/{cid}/seasons", {"season_number": 1, "title": "S1"}),
        (
            "post",
            "/api/v1/content/{cid}/seasons/{sid}/episodes",
            {"episode_number": 1, "title": "E1", "duration_minutes": 60},
        ),
        (
            "post",
            "/api/v1/content/{cid}/recommendations",
            {"recommended_content_id": "00000000-0000-0000-0000-000000000099", "similarity_score": 0.5},
        ),
        ("post", "/api/v1/content/{cid}/cast", {"name": "Actor", "slug": "actor"}),
        ("post", "/api/v1/content/{cid}/publish", {"status": "published"}),
    ]

    def _token(self, role: str | None) -> str:
        import time

        import jwt
        from app.core.settings import settings

        now = int(time.time())
        payload = {
            "sub": str(uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iat": now,
            "exp": now + 900,
        }
        if role is not None:
            payload["role"] = role
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def test_write_without_token_is_401(self, client, content_id, season_id):
        for method, path, body in self.WRITE_ROUTES:
            path = path.format(cid=content_id, sid=season_id)
            app.dependency_overrides.pop(get_admin_identity, None)
            try:
                response = getattr(client, method)(path, json=body)
            finally:
                app.dependency_overrides[get_admin_identity] = lambda: str(uuid4())
            assert response.status_code == 401, (method, path)

    def test_write_with_user_token_is_403(self, client, content_id, season_id):
        bearer = {"Authorization": f"Bearer {self._token('user')}"}
        for method, path, body in self.WRITE_ROUTES:
            path = path.format(cid=content_id, sid=season_id)
            app.dependency_overrides.pop(get_admin_identity, None)
            try:
                response = getattr(client, method)(path, json=body, headers=bearer)
            finally:
                app.dependency_overrides[get_admin_identity] = lambda: str(uuid4())
            assert response.status_code == 403, (method, path)

    def test_write_with_admin_token_is_allowed(self, client, fake_service, content_id):
        fake_service.create_genre = AsyncMock(return_value=make_genre(uuid4()))
        app.dependency_overrides.pop(get_admin_identity, None)
        try:
            response = client.post(
                "/api/v1/genres",
                json={"name": "SciFi", "slug": "scifi"},
                headers={"Authorization": f"Bearer {self._token('admin')}"},
            )
        finally:
            app.dependency_overrides[get_admin_identity] = lambda: str(uuid4())

        assert response.status_code == 201

    def test_ratings_require_any_authenticated_user_not_admin(self, client, fake_service, content_id):
        rating = MagicMock()
        rating.id = uuid4()
        rating.user_id = uuid4()
        rating.rating = 8.5
        rating.review = None
        rating.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        fake_service.rate_content = AsyncMock(return_value=rating)
        app.dependency_overrides.pop(get_current_user, None)
        try:
            response = client.post(
                f"/api/v1/content/{content_id}/ratings",
                json={"rating": 8.5},
                headers={"Authorization": f"Bearer {self._token('user')}"},
            )
        finally:
            app.dependency_overrides[get_current_user] = lambda: uuid4()

        assert response.status_code == 201


class TestContentRoutes:
    def test_create_content_returns_201(self, client, fake_service, content_id):
        content = make_content(content_id)
        fake_service.create_content = AsyncMock(return_value=content)

        response = client.post(
            "/api/v1/content",
            json={
                "title": "Test Movie",
                "slug": "test-movie",
                "description": "A movie",
                "content_type": "movie",
            },
        )

        assert response.status_code == 201
        assert response.json()["id"] == str(content_id)
        fake_service.create_content.assert_awaited_once()

    def test_create_content_rejects_bad_type(self, client):
        response = client.post(
            "/api/v1/content",
            json={
                "title": "Bad",
                "slug": "bad",
                "description": "x",
                "content_type": "gif",
            },
        )

        assert response.status_code == 422

    def test_list_content_passes_filters(self, client, fake_service):
        fake_service.list_content = AsyncMock(return_value=[])

        response = client.get(
            "/api/v1/content", params={"page": 2, "page_size": 50, "content_type": "movie"}
        )

        assert response.status_code == 200
        fake_service.list_content.assert_awaited_once_with(2, 50, "movie", None, None)

    def test_trending_passes_limit(self, client, fake_service, content_id):
        fake_service.get_trending_content = AsyncMock(return_value=[make_content(content_id)])

        response = client.get("/api/v1/content/trending", params={"limit": 42})

        assert response.status_code == 200
        assert response.json()[0]["id"] == str(content_id)
        fake_service.get_trending_content.assert_awaited_once_with(42)

    def test_trending_rejects_unbounded_limits(self, client):
        for bad in (0, 101):
            response = client.get("/api/v1/content/trending", params={"limit": bad})
            assert response.status_code == 422

    def test_trending_does_not_shadow_content_id_route(self, client, fake_service):
        fake_service.get_content = AsyncMock(return_value=None)

        response = client.get("/api/v1/content/trending")

        assert response.status_code == 200
        fake_service.get_content.assert_not_awaited()

    def test_get_content_returns_200(self, client, fake_service, content_id):
        fake_service.get_content = AsyncMock(return_value=make_content(content_id))

        response = client.get(f"/api/v1/content/{content_id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(content_id)

    def test_get_content_missing_returns_404(self, client, fake_service, content_id):
        fake_service.get_content = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/content/{content_id}")

        assert response.status_code == 404

    def test_update_content(self, client, fake_service, content_id):
        fake_service.update_content = AsyncMock(return_value=make_content(content_id))

        response = client.put(
            f"/api/v1/content/{content_id}", json={"title": "Renamed", "description": "y"}
        )

        assert response.status_code == 200
        fake_service.update_content.assert_awaited_once()

    def test_delete_content_returns_204(self, client, fake_service, content_id):
        fake_service.delete_content = AsyncMock(return_value=True)

        response = client.delete(f"/api/v1/content/{content_id}")

        assert response.status_code == 204

    def test_delete_content_missing_returns_404(self, client, fake_service, content_id):
        fake_service.delete_content = AsyncMock(return_value=False)

        response = client.delete(f"/api/v1/content/{content_id}")

        assert response.status_code == 404

    def test_publish_content(self, client, fake_service, content_id):
        fake_service.publish_content = AsyncMock(return_value=make_content(content_id))

        response = client.post(
            f"/api/v1/content/{content_id}/publish", json={"status": "published"}
        )

        assert response.status_code == 200
        fake_service.publish_content.assert_awaited_once()

    def test_publish_content_rejects_bad_status(self, client, content_id):
        response = client.post(f"/api/v1/content/{content_id}/publish", json={"status": "on-air"})

        assert response.status_code == 422


class TestSeasonRoutes:
    """Season routes call the service with (content_id, season_id, request) —

    a regression guard for the previous TypeError."""

    def test_create_season_returns_201(self, client, fake_service, content_id, season_id):
        season = MagicMock()
        season.id = season_id
        season.season_number = 1
        season.title = "Season 1"
        season.description = None
        season.poster_url = None
        season.release_date = None
        fake_service.create_season = AsyncMock(return_value=season)

        response = client.post(
            f"/api/v1/content/{content_id}/seasons",
            json={"season_number": 1, "title": "Season 1"},
        )

        assert response.status_code == 201
        assert response.json()["id"] == str(season_id)

    def test_list_seasons(self, client, fake_service, content_id):
        fake_service.list_seasons = AsyncMock(return_value=[])

        response = client.get(f"/api/v1/content/{content_id}/seasons")

        assert response.status_code == 200
        fake_service.list_seasons.assert_awaited_once_with(content_id)

    def test_get_season_passes_both_ids(self, client, fake_service, content_id, season_id):
        season = MagicMock()
        season.id = season_id
        season.season_number = 1
        season.title = "Season 1"
        season.description = None
        season.poster_url = None
        season.release_date = None
        fake_service.get_season = AsyncMock(return_value=season)

        response = client.get(f"/api/v1/content/{content_id}/seasons/{season_id}")

        assert response.status_code == 200
        # Route must forward both content_id and season_id (old bug: service
        # only received season_id, raising TypeError).
        fake_service.get_season.assert_awaited_once_with(content_id, season_id)

    def test_get_season_missing_returns_404(self, client, fake_service, content_id, season_id):
        fake_service.get_season = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/content/{content_id}/seasons/{season_id}")

        assert response.status_code == 404

    def test_update_season_passes_all_args(self, client, fake_service, content_id, season_id):
        season = MagicMock()
        season.id = season_id
        season.season_number = 1
        season.title = "Renamed"
        season.description = None
        season.poster_url = None
        season.release_date = None
        fake_service.update_season = AsyncMock(return_value=season)

        response = client.put(
            f"/api/v1/content/{content_id}/seasons/{season_id}", json={"title": "Renamed"}
        )

        assert response.status_code == 200
        # Regression guard: the route previously called update_season with 3
        # args while the service only accepted 2 (TypeError at runtime).
        call_args = fake_service.update_season.await_args
        assert call_args.args[:2] == (content_id, season_id)

    def test_delete_season_returns_204(self, client, fake_service, content_id, season_id):
        fake_service.delete_season = AsyncMock(return_value=True)

        response = client.delete(f"/api/v1/content/{content_id}/seasons/{season_id}")

        assert response.status_code == 204
        fake_service.delete_season.assert_awaited_once_with(content_id, season_id)


class TestEpisodeRoutes:
    """Episode routes must forward (content_id, season_id, episode_id)."""

    def test_create_episode_returns_201(
        self, client, fake_service, content_id, season_id, episode_id
    ):
        episode = MagicMock()
        episode.id = episode_id
        episode.episode_number = 1
        episode.title = "Pilot"
        episode.description = None
        episode.duration_minutes = 45
        episode.thumbnail_url = None
        episode.release_date = None
        episode.is_available = True
        episode.audience_score = 0.0
        fake_service.create_episode = AsyncMock(return_value=episode)

        response = client.post(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes",
            json={"episode_number": 1, "title": "Pilot", "duration_minutes": 45},
        )

        assert response.status_code == 201
        assert response.json()["id"] == str(episode_id)
        fake_service.create_episode.assert_awaited_once()

    def test_list_episodes(self, client, fake_service, content_id, season_id):
        fake_service.list_episodes = AsyncMock(return_value=[])

        response = client.get(f"/api/v1/content/{content_id}/seasons/{season_id}/episodes")

        assert response.status_code == 200
        fake_service.list_episodes.assert_awaited_once_with(content_id, season_id)

    def test_get_episode_passes_all_ids(
        self, client, fake_service, content_id, season_id, episode_id
    ):
        episode = MagicMock()
        episode.id = episode_id
        episode.episode_number = 1
        episode.title = "Pilot"
        episode.description = None
        episode.duration_minutes = 45
        episode.thumbnail_url = None
        episode.release_date = None
        episode.is_available = True
        episode.audience_score = 0.0
        fake_service.get_episode = AsyncMock(return_value=episode)

        response = client.get(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}"
        )

        assert response.status_code == 200
        # Regression guard: the route previously called get_episode with 3 ids
        # while the service only accepted 1 (TypeError at runtime).
        fake_service.get_episode.assert_awaited_once_with(content_id, season_id, episode_id)

    def test_get_episode_missing_returns_404(
        self, client, fake_service, content_id, season_id, episode_id
    ):
        fake_service.get_episode = AsyncMock(return_value=None)

        response = client.get(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}"
        )

        assert response.status_code == 404

    def test_update_episode_passes_all_ids(
        self, client, fake_service, content_id, season_id, episode_id
    ):
        episode = MagicMock()
        episode.id = episode_id
        episode.episode_number = 1
        episode.title = "Renamed"
        episode.description = None
        episode.duration_minutes = 45
        episode.thumbnail_url = None
        episode.release_date = None
        episode.is_available = True
        episode.audience_score = 0.0
        fake_service.update_episode = AsyncMock(return_value=episode)

        response = client.put(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}",
            json={"title": "Renamed"},
        )

        assert response.status_code == 200
        # Regression guard: the route previously called update_episode with 4
        # args while the service only accepted 2 (TypeError at runtime).
        call_args = fake_service.update_episode.await_args
        assert call_args.args[:3] == (content_id, season_id, episode_id)

    def test_delete_episode_returns_204(
        self, client, fake_service, content_id, season_id, episode_id
    ):
        fake_service.delete_episode = AsyncMock(return_value=True)

        response = client.delete(
            f"/api/v1/content/{content_id}/seasons/{season_id}/episodes/{episode_id}"
        )

        assert response.status_code == 204


class TestRatingRoutes:
    def test_rate_content_requires_auth(self, client, content_id):
        app.dependency_overrides.pop(get_current_user, None)
        try:
            response = client.post(f"/api/v1/content/{content_id}/ratings", json={"rating": 8.5})
        finally:
            app.dependency_overrides[get_current_user] = lambda: uuid4()

        assert response.status_code == 401

    def test_rate_content_passes_user_id(self, client, fake_service, content_id, user_id):
        rating = MagicMock()
        rating.id = uuid4()
        rating.user_id = user_id
        rating.rating = 8.5
        rating.review = None
        rating.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        fake_service.rate_content = AsyncMock(return_value=rating)

        response = client.post(f"/api/v1/content/{content_id}/ratings", json={"rating": 8.5})

        assert response.status_code == 201
        call_args = fake_service.rate_content.await_args
        assert call_args.args[:2] == (content_id, user_id)

    def test_list_ratings(self, client, fake_service, content_id):
        fake_service.list_ratings = AsyncMock(return_value=[])

        response = client.get(f"/api/v1/content/{content_id}/ratings")

        assert response.status_code == 200
        fake_service.list_ratings.assert_awaited_once_with(content_id)


class TestRecommendationRoutes:
    def test_add_recommendation(self, client, fake_service, content_id):
        rec = MagicMock()
        rec.id = uuid4()
        rec.recommended_content_id = uuid4()
        rec.similarity_score = 0.9
        rec.recommendation_type = "similar"
        fake_service.add_recommendation = AsyncMock(return_value=rec)

        response = client.post(
            f"/api/v1/content/{content_id}/recommendations",
            json={
                "recommended_content_id": str(uuid4()),
                "similarity_score": 0.9,
                "recommendation_type": "similar",
            },
        )

        assert response.status_code == 201
        fake_service.add_recommendation.assert_awaited_once()

    def test_list_recommendations(self, client, fake_service, content_id):
        fake_service.list_recommendations = AsyncMock(return_value=[])

        response = client.get(f"/api/v1/content/{content_id}/recommendations")

        assert response.status_code == 200
        fake_service.list_recommendations.assert_awaited_once_with(content_id)


class TestCastRoutes:
    def test_add_cast_member(self, client, fake_service, content_id):
        cast = MagicMock()
        cast.id = uuid4()
        cast.name = "Actor"
        cast.slug = "actor"
        cast.bio = None
        cast.birth_date = None
        cast.image_url = None
        fake_service.add_cast_member = AsyncMock(return_value=cast)

        response = client.post(
            f"/api/v1/content/{content_id}/cast",
            json={"name": "Actor", "slug": "actor"},
        )

        assert response.status_code == 201
        call_args = fake_service.add_cast_member.await_args
        assert call_args.args[0] == content_id

    def test_list_cast(self, client, fake_service, content_id):
        fake_service.list_cast = AsyncMock(return_value=[])

        response = client.get(f"/api/v1/content/{content_id}/cast")

        assert response.status_code == 200
        fake_service.list_cast.assert_awaited_once_with(content_id)
