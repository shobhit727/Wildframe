"""Tests for Recommendation Service API routes."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.recommendation_routes import get_rec_service
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    # Not a context manager: lifespan raises without a healthy DB.
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


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
        service.update_preferences.assert_awaited_once_with(
            user_id, ["action", "scifi"]
        )

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