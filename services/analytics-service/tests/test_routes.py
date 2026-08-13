"""Tests for Analytics Service API routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.analytics_routes import (
    get_analytics_service,
    get_current_user_id as analytics_user_di,
    require_self as analytics_require_self,
)
from app.main import app


@pytest.fixture
def auth_user_id():
    return uuid4()


async def _echo_path_self(request: Request) -> UUID:
    return UUID(request.path_params["user_id"])


@pytest.fixture
def client(auth_user_id):
    app.dependency_overrides.clear()
    app.dependency_overrides[analytics_require_self] = _echo_path_self
    app.dependency_overrides[analytics_user_di] = lambda: auth_user_id
    # NOTE: not used as a context manager — the lifespan raises when there is
    # no healthy database, and tests run without postgres.
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def override_auth():
    yield


@pytest.fixture
def service():
    mock = MagicMock()
    mock.log_event = AsyncMock()
    mock.record_view_event = AsyncMock()
    return mock


def override(service_mock):
    def _dep():
        return service_mock

    return _dep


def event_dict(**overrides):
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "event_type": "playback_started",
        "event_data": {"ts": 1.5},
        "content_id": uuid4(),
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


class TestLogEvent:
    def test_log_event_success(self, client, service, auth_user_id):
        app.dependency_overrides[get_analytics_service] = override(service)

        response = client.post(
            "/api/v1/analytics/events",
            json={
                "user_id": str(auth_user_id),
                "event_type": "playback_started",
                "event_data": {"position": 10},
                "content_id": str(uuid4()),
            },
        )

        assert response.status_code == 200
        assert response.json() == {"status": "logged"}
        service.log_event.assert_awaited_once()

    def test_log_event_requires_user_id(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)

        response = client.post("/api/v1/analytics/events", json={"event_type": "playback_started"})

        assert response.status_code == 422

    def test_log_event_invalid_uuid_returns_422(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)

        response = client.post(
            "/api/v1/analytics/events",
            json={"user_id": "not-a-uuid", "event_type": "playback_started"},
        )

        assert response.status_code == 422


class TestGetUserEvents:
    def test_get_user_events_success(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)
        events = [event_dict(), event_dict()]
        service.get_user_events = AsyncMock(return_value=events)
        user_id = uuid4()

        response = client.get(f"/api/v1/analytics/user-events/{user_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        service.get_user_events.assert_awaited_once_with(user_id, 100)


class TestRecordViewEvent:
    def test_record_view_event_success(self, client, service, auth_user_id):
        app.dependency_overrides[get_analytics_service] = override(service)

        response = client.post(
            "/api/v1/analytics/view-events",
            json={
                "content_id": str(uuid4()),
                "viewer_id": str(auth_user_id),
                "watch_duration_seconds": 120,
                "content_duration_seconds": 300,
                "completion_pct": 40.0,
                "playback_quality": "1080p",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"status": "recorded"}
        service.record_view_event.assert_awaited_once()
        call = service.record_view_event.await_args.kwargs
        assert call["watch_duration_seconds"] == 120
        assert call["completion_pct"] == 40.0

    def test_record_view_event_requires_content_id(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)

        response = client.post("/api/v1/analytics/view-events", json={"viewer_id": str(uuid4())})

        assert response.status_code == 422


class TestCreatorAnalytics:
    def test_get_creator_analytics_success(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)
        creator_id = uuid4()
        service.get_creator_analytics = AsyncMock(
            return_value={
                "creator_id": str(creator_id),
                "total_views": 10,
                "total_watch_hours": 2.5,
                "avg_completion_rate": 0.3,
                "unique_viewers": 5,
                "revenue_earned": 1.25,
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-31T00:00:00+00:00",
            }
        )

        response = client.get(f"/api/v1/analytics/creators/{creator_id}")

        assert response.status_code == 200
        assert response.json()["total_views"] == 10

    def test_get_creator_analytics_none(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)
        service.get_creator_analytics = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/analytics/creators/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["analytics"] is None


class TestContentPerformance:
    def test_get_content_performance_success(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)
        content_id = uuid4()
        service.get_content_performance = AsyncMock(
            return_value={
                "content_id": str(content_id),
                "views_7d": 42,
                "views_30d": 130,
                "avg_completion_pct": 55.0,
                "revenue_7d": 2.0,
                "revenue_30d": 9.0,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )

        response = client.get(f"/api/v1/analytics/content/{content_id}")

        assert response.status_code == 200
        assert response.json()["views_30d"] == 130

    def test_get_content_performance_none(self, client, service):
        app.dependency_overrides[get_analytics_service] = override(service)
        service.get_content_performance = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/analytics/content/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["metrics"] is None


class TestIdorProtection:
    def test_other_user_events_403(self, client, auth_user_id):
        app.dependency_overrides.pop(analytics_require_self, None)
        app.dependency_overrides[analytics_user_di] = lambda: auth_user_id
        try:
            response = client.get(f"/api/v1/analytics/user-events/{uuid4()}")
        finally:
            app.dependency_overrides[analytics_require_self] = _echo_path_self
        assert response.status_code == 403

    def test_no_token_rejected_401(self, client):
        app.dependency_overrides.clear()
        response = client.get(f"/api/v1/analytics/user-events/{uuid4()}")
        assert response.status_code == 401

    def test_log_event_for_other_user_403(self, client, auth_user_id):
        response = client.post(
            "/api/v1/analytics/events",
            json={
                "user_id": str(uuid4()),
                "event_type": "playback_started",
                "event_data": {},
            },
        )
        assert response.status_code == 403


class TestCreatorContentAuth:
    """Regression for [#90]: the creator/content analytics endpoints must
    require authentication — they used to be callable anonymously.
    Authenticated access is still allowed because the fixture overrides the
    auth dependency; the 401 paths below prove the endpoints now hold the
    authentication boundary.
    """

    def test_creator_analytics_requires_token(self, client):
        app.dependency_overrides.clear()
        # get_analytics_service still needs to resolve, so set it back.
        app.dependency_overrides[get_analytics_service] = override(MagicMock())
        response = client.get(f"/api/v1/analytics/creators/{uuid4()}")
        assert response.status_code == 401

    def test_content_performance_requires_token(self, client):
        app.dependency_overrides.clear()
        app.dependency_overrides[get_analytics_service] = override(MagicMock())
        response = client.get(f"/api/v1/analytics/content/{uuid4()}")
        assert response.status_code == 401
