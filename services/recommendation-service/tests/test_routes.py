"""Tests for Recommendation Service API routes."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.recommendation_routes import (
    get_current_user_id as rec_user_di,
    get_rec_service,
    require_self as rec_require_self,
)
from app.main import app


async def _echo_path_self(request: Request) -> UUID:
    from fastapi import HTTPException

    try:
        return UUID(request.path_params["user_id"])
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID")


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    app.dependency_overrides[rec_require_self] = _echo_path_self
    # Not a context manager: lifespan raises without a healthy DB.
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def override_auth():
    yield


@pytest.fixture
def service():
    mock = MagicMock()
    mock.get_recommendations = AsyncMock(return_value=[])
    mock.update_preferences = AsyncMock()
    return mock


def override(service_mock):
    def _dep():
        return service_mock

    return _dep


class TestGetRecommendations:
    def test_get_success(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)
        recs = [{"content_id": str(uuid4()), "score": 0.98, "title": "A"}]
        service.get_recommendations = AsyncMock(return_value=recs)
        user_id = uuid4()

        response = client.get(f"/api/v1/recommendations/for-user/{user_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["recommendations"] == recs
        service.get_recommendations.assert_awaited_once_with(user_id, 20)

    def test_get_honors_limit(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)
        user_id = uuid4()

        client.get(f"/api/v1/recommendations/for-user/{user_id}?limit=5")

        service.get_recommendations.assert_awaited_once_with(user_id, 5)

    def test_get_empty(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.get(f"/api/v1/recommendations/for-user/{uuid4()}")

        assert response.status_code == 200
        assert response.json() == {"recommendations": [], "total": 0}

    def test_get_invalid_user_id_returns_422(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.get("/api/v1/recommendations/for-user/not-a-uuid")

        assert response.status_code == 422


class TestUpdatePreferences:
    def test_update_success(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)
        user_id = uuid4()

        # The route declares a single bare Body param, so the request body
        # IS the raw JSON array (no {"liked_genres": ...} wrapper).
        response = client.put(
            f"/api/v1/recommendations/preferences/{user_id}",
            json=["action", "scifi"],
        )

        assert response.status_code == 200
        assert response.json() == {"status": "updated"}
        service.update_preferences.assert_awaited_once_with(user_id, ["action", "scifi"], None)

    def test_update_without_genres(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)
        user_id = uuid4()

        response = client.put(
            f"/api/v1/recommendations/preferences/{user_id}",
            content="null",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        assert service.update_preferences.await_args.args[1] is None

    def test_update_invalid_user_id_returns_422(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.put("/api/v1/recommendations/preferences/not-a-uuid", content="[]")

        assert response.status_code == 422

    def test_get_limit_above_max_returns_422(self, client, service):
        """Unbounded limits are rejected at the route layer (#228 F4)."""
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.get(f"/api/v1/recommendations/for-user/{uuid4()}?limit=10000")

        assert response.status_code == 422
        service.get_recommendations.assert_not_awaited()

    def test_update_too_many_liked_genres_returns_422(self, client, service):
        """Unbounded preference lists are rejected at the route layer (#228 F4)."""
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.put(
            f"/api/v1/recommendations/preferences/{uuid4()}",
            json={"liked_genres": ["action"] * 51, "disliked_genres": []},
        )

        assert response.status_code == 422
        service.update_preferences.assert_not_awaited()

    def test_update_too_many_disliked_genres_returns_422(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.put(
            f"/api/v1/recommendations/preferences/{uuid4()}",
            json={"liked_genres": [], "disliked_genres": ["horror"] * 51},
        )

        assert response.status_code == 422

    def test_update_legacy_list_too_many_returns_422(self, client, service):
        app.dependency_overrides[get_rec_service] = override(service)

        response = client.put(
            f"/api/v1/recommendations/preferences/{uuid4()}", json=["action"] * 51
        )

        assert response.status_code == 422


class TestIdorProtection:
    def test_other_user_recommendations_403(self, client):
        auth_id = uuid4()
        app.dependency_overrides.pop(rec_require_self, None)
        app.dependency_overrides[rec_user_di] = lambda: auth_id
        try:
            response = client.get(f"/api/v1/recommendations/for-user/{uuid4()}")
        finally:
            app.dependency_overrides[rec_require_self] = _echo_path_self
        assert response.status_code == 403

    def test_no_token_rejected_401(self, client):
        app.dependency_overrides.clear()
        response = client.get(f"/api/v1/recommendations/for-user/{uuid4()}")
        assert response.status_code == 401
