"""Tests for Search Service API routes."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.api.search_routes import get_search_service
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    # NOTE: do NOT use this TestClient as a context manager. The lifespan
    # raises RuntimeError when the database is unhealthy, and tests run
    # without a live postgres.
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def service():
    mock = MagicMock()
    mock.search = AsyncMock(return_value=[{"id": "m1", "title": "Action Movie"}])
    return mock


def override_get_search_service(service):
    def _dep():
        return service

    return _dep


class TestSearchEndpoints:
    def test_search_returns_results(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get(
            "/api/v1/search/query",
            params={"q": "action", "content_type": "movie", "limit": 20, "user_id": str(uuid4())},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "action"
        assert body["total"] == 1
        assert body["results"][0]["title"] == "Action Movie"

    def test_search_without_user_id_works_on_get(self, client, service):
        """Regression: user_id must not be a GET body — the gateway never
        forwards GET bodies, so a body contract breaks the frontend."""
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get(
            "/api/v1/search/query",
            params={"q": "action", "limit": 20},
        )

        assert response.status_code == 200
        assert response.json()["total"] == 1
        service.search.assert_awaited_once()
        assert service.search.await_args.args[0] is None

    def test_search_calls_service_with_params(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        user_id = uuid4()
        client.get(
            "/api/v1/search/query",
            params={"q": "thriller", "limit": 5, "user_id": str(user_id)},
        )

        service.search.assert_awaited_once()

    def test_search_missing_query_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/query")

        assert response.status_code == 422

    def test_trending_returns_empty_list(self, client):
        response = client.get("/api/v1/search/trending")

        assert response.status_code == 200
        body = response.json()
        assert body["trending"] == []
        assert body["total"] == 0
