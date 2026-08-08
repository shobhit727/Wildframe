"""Tests for Moderation Service API routes.

Service-level business logic is covered in ``test_moderation.py`` (in-memory
fakes). These tests exercise the HTTP layer: routing, request validation,
response serialization, and error mapping (ModerationError -> 400/409).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.moderation_routes import get_moderation_service
from app.main import app
from app.services import ModerationError


def make_flag(**overrides):
    flag = MagicMock()
    flag.id = uuid4()
    flag.content_id = uuid4()
    flag.flag_reason = "copyright"
    flag.reported_by = uuid4()
    flag.status = "pending"
    flag.reviewed_by = None
    flag.reviewed_at = None
    flag.resolution_notes = None
    flag.created_at = datetime.now(UTC)
    flag.updated_at = datetime.now(UTC)
    for k, v in overrides.items():
        setattr(flag, k, v)
    return flag


def make_decision(**overrides):
    decision = MagicMock()
    decision.id = uuid4()
    decision.flag_id = uuid4()
    decision.moderator_id = uuid4()
    decision.decision = "approve"
    decision.notes = None
    decision.created_at = datetime.now(UTC)
    for k, v in overrides.items():
        setattr(decision, k, v)
    return decision


def make_strike(**overrides):
    strike = MagicMock()
    strike.id = uuid4()
    strike.creator_id = uuid4()
    strike.strike_reason = "copyright"
    strike.related_flag_id = None
    strike.is_active = True
    strike.expires_at = None
    strike.created_at = datetime.now(UTC)
    for k, v in overrides.items():
        setattr(strike, k, v)
    return strike


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    # Not a context manager: lifespan raises without a healthy DB.
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def service():
    mock = MagicMock()
    mock.flag_content = AsyncMock(return_value=make_flag())
    mock.get_queue = AsyncMock(return_value=[make_flag()])
    mock.make_decision = AsyncMock(return_value=make_decision())
    mock.get_strikes = AsyncMock(return_value=[make_strike()])
    return mock


def override(service_mock):
    def _dep():
        return service_mock

    return _dep


class TestFlagContent:
    def test_flag_success(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/flags",
            json={
                "content_id": str(uuid4()),
                "flag_reason": "copyright",
                "reporter_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["flag_reason"] == "copyright"

    def test_flag_invalid_reason_returns_422(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/flags",
            json={
                "content_id": str(uuid4()),
                "flag_reason": "not-a-reason",
                "reporter_id": str(uuid4()),
            },
        )

        assert response.status_code == 422

    def test_flag_service_error_returns_400(self, client, service):
        service.flag_content = AsyncMock(side_effect=ModerationError("cannot flag self"))
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/flags",
            json={
                "content_id": str(uuid4()),
                "flag_reason": "other",
                "reporter_id": str(uuid4()),
            },
        )

        assert response.status_code == 400
        assert "cannot flag self" in response.json()["detail"]

    def test_flag_missing_reporter_returns_422(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/flags",
            json={"content_id": str(uuid4()), "flag_reason": "spam"},
        )

        assert response.status_code == 422


class TestQueue:
    def test_get_queue_success(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.get("/api/v1/moderation/queue")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["flag_reason"] == "copyright"
        service.get_queue.assert_awaited_once_with(limit=50)

    def test_get_queue_honors_limit(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        client.get("/api/v1/moderation/queue?limit=10")

        service.get_queue.assert_awaited_once_with(limit=10)

    def test_get_queue_empty(self, client, service):
        service.get_queue = AsyncMock(return_value=[])
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.get("/api/v1/moderation/queue")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}


class TestDecisions:
    def test_make_decision_success(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/decisions",
            json={
                "flag_id": str(uuid4()),
                "decision": "approve",
                "moderator_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["decision"] == "approve"

    def test_make_decision_conflict_returns_409(self, client, service):
        service.make_decision = AsyncMock(
            side_effect=ModerationError("flag already resolved")
        )
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/decisions",
            json={
                "flag_id": str(uuid4()),
                "decision": "reject",
                "moderator_id": str(uuid4()),
            },
        )

        assert response.status_code == 409
        assert "already resolved" in response.json()["detail"]

    def test_make_decision_invalid_decision_returns_422(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.post(
            "/api/v1/moderation/decisions",
            json={
                "flag_id": str(uuid4()),
                "decision": "nuke",
                "moderator_id": str(uuid4()),
            },
        )

        assert response.status_code == 422


class TestStrikes:
    def test_get_strikes_success(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)
        creator_id = uuid4()

        response = client.get(f"/api/v1/moderation/strikes/{creator_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["creator_id"] == str(creator_id)
        assert body["active_count"] == 1
        assert body["strikes"][0]["is_active"] is True
        service.get_strikes.assert_awaited_once_with(creator_id)

    def test_get_strikes_inactive_not_counted(self, client, service):
        service.get_strikes = AsyncMock(return_value=[make_strike(is_active=False)])
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.get(f"/api/v1/moderation/strikes/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["active_count"] == 0

    def test_get_strikes_invalid_creator_returns_422(self, client, service):
        app.dependency_overrides[get_moderation_service] = override(service)

        response = client.get("/api/v1/moderation/strikes/not-a-uuid")

        assert response.status_code == 422


class TestHealth:
    def test_health(self, client):
        response = client.get("/api/v1/moderation/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"