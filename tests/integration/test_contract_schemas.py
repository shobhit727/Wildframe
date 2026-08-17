"""Contract tests: response schemas and error formats across the gateway.

The audit's drift-detection requirement: if a service changes a critical
response shape or the gateway changes an error format, these tests fail even
though every per-service unit suite stays green.
"""

from __future__ import annotations

import uuid as uuidlib

import httpx
import pytest

from conftest import (
    ANALYTICS_SERVICE,
    GATEWAY_URL,
    AUTH_SERVICE,
    CONTENT_SERVICE,
    STREAMING_SERVICE,
    auth_headers,
    decode_jwt,
    fetch_content_id,
    ip_keyed,
    register_user,
)

pytestmark = pytest.mark.integration

TOKEN_RESPONSE_FIELDS = {"access_token", "refresh_token", "token_type", "expires_in"}
CONTENT_RESPONSE_FIELDS = {
    "id", "title", "slug", "description", "content_type", "status", "creator_id",
}
PLAYBACK_SESSION_FIELDS = {
    "id", "user_id", "content_id", "device_id", "status",
    "current_position_seconds", "total_duration_seconds", "protocol", "resolution",
}


class TestTokenContract:
    def test_register_response_shape(self, client: httpx.Client, user_a: dict) -> None:
        assert TOKEN_RESPONSE_FIELDS <= set(user_a)
        assert user_a["access_token"].count(".") == 2
        payload = decode_jwt(user_a["access_token"])
        assert payload["sub"] == user_a["user_id"]

    def test_login_response_shape(self, client: httpx.Client, user_a: dict) -> None:
        response = ip_keyed(
            client, "post", f"{AUTH_SERVICE}/login",
            json={
                "email": user_a["email"],
                "password": "SecurePass123!",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert TOKEN_RESPONSE_FIELDS <= set(body)
        assert body["token_type"] == "bearer"
        assert isinstance(body["expires_in"], int) and body["expires_in"] > 0


class TestContentContract:
    @pytest.fixture()
    def content_id(self, client: httpx.Client, user_a: dict) -> str:
        content_id = fetch_content_id(client, user_a["access_token"])
        if content_id is None:
            pytest.skip("catalog is empty")
        return content_id

    def test_content_item_shape(self, client: httpx.Client, user_a: dict, content_id: str) -> None:
        response = client.get(
            f"{CONTENT_SERVICE}/content/{content_id}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 200
        body = response.json()
        assert CONTENT_RESPONSE_FIELDS <= set(body)
        assert body["id"] == content_id
        assert uuidlib.UUID(body["id"])

    def test_content_list_shape(self, client: httpx.Client, user_a: dict) -> None:
        response = client.get(
            f"{CONTENT_SERVICE}/content", headers=auth_headers(user_a["access_token"])
        )
        assert response.status_code == 200
        for item in response.json():
            assert {"id", "title", "slug"} <= set(item)


class TestErrorFormatContract:
    def test_service_validation_error_shape(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.get(
            f"{CONTENT_SERVICE}/content/not-a-uuid",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 422
        body = response.json()
        # Content-service validation errors carry status_code + message.
        assert {"status_code", "message"} <= set(body)
        assert body["status_code"] == 422

    def test_gateway_auth_error_shape(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", f"{AUTH_SERVICE}/me")
        assert response.status_code == 401
        assert "detail" in response.json()

    def test_service_auth_error_shape(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        """Per-service identity checks fail with the service's own error shape."""
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/creators/{uuidlib.uuid4()}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 403
        assert "detail" in response.json()


class TestPlaybackContract:
    @pytest.fixture()
    def session(self, client: httpx.Client, user_a: dict) -> dict:
        content_id = fetch_content_id(client, user_a["access_token"])
        if content_id is None:
            pytest.skip("catalog is empty")
        response = client.post(
            f"{STREAMING_SERVICE}/playback-sessions",
            headers=auth_headers(user_a["access_token"]),
            json={
                "user_id": user_a["user_id"],
                "content_id": content_id,
                "device_id": "contract-test",
                "protocol": "hls",
                "resolution": "720p",
                "bitrate_kbps": 2500,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_playback_session_shape(self, client: httpx.Client, user_a: dict, session: dict) -> None:
        assert PLAYBACK_SESSION_FIELDS <= set(session)
        assert session["user_id"] == user_a["user_id"]

    def test_playback_session_list_shape(
        self, client: httpx.Client, user_a: dict, session: dict
    ) -> None:
        response = client.get(
            f"{STREAMING_SERVICE}/users/{user_a['user_id']}/playback-sessions",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 200
        for item in response.json():
            assert PLAYBACK_SESSION_FIELDS <= set(item)


class TestCriticalFrontendContract:
    """[#44] Success and failure contracts for every critical frontend call
    surface, as consumed by apps/web/src/api/*.ts through the gateway.
    Failure mode (tokenless): 401 on protected routes, 200 only on the
    deliberately public ones (catalog, search). Guards against drift where
    the UI keeps compiling but the wire behavior changes.
    """

    TOKENLESS_CASES = [
        (f"{GATEWAY_URL}/auth/api/v1/auth/me", 401),
        (f"{GATEWAY_URL}/users/api/v1/profiles/11111111-1111-1111-1111-111111111111", 401),
        (f"{GATEWAY_URL}/streaming/api/v1/playback-sessions/11111111-1111-1111-1111-111111111111", 401),
        (f"{GATEWAY_URL}/recommendations/api/v1/recommendations/for-user/11111111-1111-1111-1111-111111111111", 401),
        (f"{GATEWAY_URL}/billing/api/v1/billing/subscription/11111111-1111-1111-1111-111111111111", 401),
        (f"{GATEWAY_URL}/admin/api/v1/admin/stats", 401),
        (f"{GATEWAY_URL}/creators/api/v1/creators/me", 401),
        (f"{GATEWAY_URL}/media/api/v1/pipeline/jobs/11111111-1111-1111-1111-111111111111", 401),
    ]
    PUBLIC_CASES = [
        (f"{GATEWAY_URL}/content/api/v1/content", 200),
        (f"{GATEWAY_URL}/search/api/v1/search/query?q=contract", 200),
    ]

    @pytest.mark.parametrize("path,expected", TOKENLESS_CASES)
    def test_protected_frontend_calls_reject_anonymous(
        self, client: httpx.Client, path: str, expected: int
    ) -> None:
        response = ip_keyed(client, "get", path)
        assert response.status_code == expected

    @pytest.mark.parametrize("path,expected", PUBLIC_CASES)
    def test_public_frontend_calls_remain_open(
        self, client: httpx.Client, path: str, expected: int
    ) -> None:
        response = ip_keyed(client, "get", path)
        assert response.status_code == expected

    def test_analytics_events_route_is_post_only(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", f"{GATEWAY_URL}/analytics/api/v1/analytics/events")
        assert response.status_code == 405

    def test_authed_frontend_call_succeeds(self, client: httpx.Client, user_a: dict) -> None:
        response = client.get(
            f"{GATEWAY_URL}/users/api/v1/profiles/{user_a['user_id']}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code in (200, 404)


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    return register_user(client)
