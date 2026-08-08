"""Tests for Notification Service API routes."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.notification_routes import get_notif_service
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
    mock.send_notification = AsyncMock()
    return mock


def override(service_mock):
    def _dep():
        return service_mock

    return _dep


class TestSendNotification:
    def test_send_success(self, client, service):
        app.dependency_overrides[get_notif_service] = override(service)
        user_id = uuid4()

        response = client.post(
            "/api/v1/notifications/send",
            json={
                "user_id": str(user_id),
                "title": "New episode",
                "message": "Stranger Things S5 is out",
                "channel": "email",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        service.send_notification.assert_awaited_once_with(
            user_id, "New episode", "Stranger Things S5 is out", "email"
        )

    def test_send_defaults_to_in_app_channel(self, client, service):
        app.dependency_overrides[get_notif_service] = override(service)

        response = client.post(
            "/api/v1/notifications/send",
            json={"user_id": str(uuid4()), "title": "Hi", "message": "There"},
        )

        assert response.status_code == 200
        assert service.send_notification.await_args.args[3] == "in-app"

    def test_send_requires_user_id(self, client, service):
        app.dependency_overrides[get_notif_service] = override(service)

        response = client.post(
            "/api/v1/notifications/send", json={"title": "Hi", "message": "There"}
        )

        assert response.status_code == 422

    def test_send_invalid_user_id_returns_422(self, client, service):
        app.dependency_overrides[get_notif_service] = override(service)

        response = client.post(
            "/api/v1/notifications/send",
            json={"user_id": "nope", "title": "Hi", "message": "There"},
        )

        assert response.status_code == 422


class TestUnread:
    def test_unread_returns_empty_by_default(self, client):
        response = client.get(f"/api/v1/notifications/unread/{uuid4()}")

        assert response.status_code == 200
        assert response.json() == {"notifications": [], "total": 0}

    def test_unread_invalid_user_id_returns_422(self, client):
        response = client.get("/api/v1/notifications/unread/not-a-uuid")

        assert response.status_code == 422