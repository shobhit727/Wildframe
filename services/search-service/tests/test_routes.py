"""Tests for Search Service API routes."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.api.search_routes import get_search_service
from app.main import app
from app.services import ReindexResult
from app.core.security import Identity


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
    mock.search = AsyncMock(
        return_value=MagicMock(results=[{"id": "m1", "title": "Action Movie"}], next_sort=None)
    )
    mock.trending = AsyncMock(return_value=[{"id": "m1", "title": "Action Movie"}])
    mock.reindex_catalog = AsyncMock(
        return_value=ReindexResult(count=12, index_name="content_v1", switched=True)
    )
    mock.delete_content = AsyncMock(return_value=True)
    mock.delete_index = AsyncMock()
    return mock


@pytest.fixture
def admin_identity():
    return Identity(user_id=uuid4(), role="admin")


def override_get_search_service(service):
    def _dep():
        return service

    return _dep


class TestSearchEndpoints:
    def test_search_returns_results(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get(
            "/api/v1/search/query",
            params={"q": "action", "content_type": "movie", "limit": 20},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "action"
        assert body["total"] == 1
        assert body["results"][0]["title"] == "Action Movie"
        assert body["next_cursor"] is None

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
        # user_id is now None (derived from token, not query param)
        assert service.search.await_args.kwargs.get("user_id") is None

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

    def test_search_empty_query_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/query", params={"q": ""})
        assert response.status_code == 422

    def test_search_query_too_long_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/query", params={"q": "x" * 201})
        assert response.status_code == 422

    def test_search_limit_zero_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/query", params={"q": "test", "limit": 0})
        assert response.status_code == 422

    def test_search_limit_exceeds_max_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/query", params={"q": "test", "limit": 101})
        assert response.status_code == 422

    def test_search_invalid_cursor_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/query", params={"q": "test", "cursor": "invalid"})
        assert response.status_code == 422

    def test_trending_returns_results(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/trending")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["trending"][0]["title"] == "Action Movie"
        service.trending.assert_awaited_once_with(None, 10)

    def test_trending_passes_content_type_and_limit(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        client.get("/api/v1/search/trending", params={"content_type": "movie", "limit": 5})

        service.trending.assert_awaited_once_with("movie", 5)

    def test_trending_limit_zero_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/trending", params={"limit": 0})
        assert response.status_code == 422

    def test_trending_limit_exceeds_max_returns_422(self, client, service):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        response = client.get("/api/v1/search/trending", params={"limit": 51})
        assert response.status_code == 422

    def test_reindex_calls_service(self, client, service, admin_identity):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        with patch(
            "app.api.search_routes.get_admin_identity", new=AsyncMock(return_value=admin_identity)
        ):
            response = client.post("/api/v1/search/reindex")

        assert response.status_code == 200
        assert response.json() == {"indexed": 12, "index": "content_v1", "switched": True}
        service.reindex_catalog.assert_awaited_once()

    def test_delete_content_admin(self, client, service, admin_identity):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        with patch(
            "app.api.search_routes.get_admin_identity", new=AsyncMock(return_value=admin_identity)
        ):
            content_id = uuid4()
            response = client.delete(f"/api/v1/search/content/{content_id}")

        assert response.status_code == 200
        assert response.json() == {"deleted": str(content_id)}
        service.delete_content.assert_awaited_once_with(content_id)

    def test_delete_index_requires_confirm(self, client, service, admin_identity):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        with patch(
            "app.api.search_routes.get_admin_identity", new=AsyncMock(return_value=admin_identity)
        ):
            response = client.delete("/api/v1/search/index/content_v1")
        assert response.status_code == 400

    def test_delete_index_with_confirm(self, client, service, admin_identity):
        app.dependency_overrides[get_search_service] = override_get_search_service(service)

        with patch(
            "app.api.search_routes.get_admin_identity", new=AsyncMock(return_value=admin_identity)
        ):
            response = client.delete("/api/v1/search/index/content_v1", params={"confirm": "true"})

        assert response.status_code == 200
        assert response.json() == {"deleted": "content_v1"}
        service.delete_index.assert_awaited_once_with("content_v1")
