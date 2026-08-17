"""Server-side authorization across service boundaries.

The audit demands that authorization be enforced by the API layer, never by
the frontend. These tests call the endpoints directly with real JWTs and
assert the denial behavior for cross-user, non-owner, unauthenticated and
malformed-token access on content (admin writes), streaming (playback
ownership) and analytics (creator/content ownership, #63).
"""

from __future__ import annotations

import uuid as uuidlib

import httpx
import pytest

from conftest import (
    ANALYTICS_SERVICE,
    AUTH_SERVICE,
    CONTENT_SERVICE,
    STREAMING_SERVICE,
    auth_headers,
    fetch_content_id,
    ip_keyed,
    register_user,
)

pytestmark = pytest.mark.integration


class TestContentAuthorization:
    def test_catalog_read_is_public_with_valid_token(self, client: httpx.Client, user_a: dict) -> None:
        response = client.get(
            f"{CONTENT_SERVICE}/content", headers=auth_headers(user_a["access_token"])
        )
        assert response.status_code == 200

    def test_catalog_write_requires_token(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "post", f"{CONTENT_SERVICE}/content", json={})
        assert response.status_code == 401

    @pytest.fixture()
    def content_id(self, client: httpx.Client, user_a: dict) -> str | None:
        return fetch_content_id(client, user_a["access_token"])

    def test_any_authenticated_user_can_read_catalog_item(
        self, client: httpx.Client, user_a: dict, user_b: dict, content_id: str | None
    ) -> None:
        if content_id is None:
            pytest.skip("catalog is empty")
        response = client.get(
            f"{CONTENT_SERVICE}/content/{content_id}", headers=auth_headers(user_b["access_token"])
        )
        assert response.status_code == 200

    def test_catalog_write_denied_for_regular_user(
        self, client: httpx.Client, user_a: dict, content_id: str | None
    ) -> None:
        """Catalog mutations are admin-only (#51); a regular user gets 403, not 201."""
        if content_id is None:
            pytest.skip("catalog is empty")
        response = client.put(
            f"{CONTENT_SERVICE}/content/{content_id}",
            headers=auth_headers(user_a["access_token"]),
            json={},
        )
        assert response.status_code == 403

    def test_catalog_create_denied_for_regular_user(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.post(
            f"{CONTENT_SERVICE}/content",
            headers=auth_headers(user_a["access_token"]),
            json={},
        )
        assert response.status_code == 403


class TestStreamingPlaybackAuthorization:
    @pytest.fixture()
    def content_id(self, client: httpx.Client, user_a: dict) -> str:
        content_id = fetch_content_id(client, user_a["access_token"])
        if content_id is None:
            pytest.skip("catalog is empty")
        return content_id

    @pytest.fixture()
    def session_id(self, client: httpx.Client, user_a: dict, content_id: str) -> str:
        response = client.post(
            f"{STREAMING_SERVICE}/playback-sessions",
            headers=auth_headers(user_a["access_token"]),
            json={
                "user_id": user_a["user_id"],
                "content_id": content_id,
                "device_id": "integration-test",
                "protocol": "hls",
                "resolution": "720p",
                "bitrate_kbps": 2500,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def test_owner_can_read_session(
        self, client: httpx.Client, user_a: dict, session_id: str
    ) -> None:
        response = client.get(
            f"{STREAMING_SERVICE}/playback-sessions/{session_id}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == user_a["user_id"]

    def test_non_owner_cannot_read_session(
        self, client: httpx.Client, user_b: dict, session_id: str
    ) -> None:
        response = client.get(
            f"{STREAMING_SERVICE}/playback-sessions/{session_id}",
            headers=auth_headers(user_b["access_token"]),
        )
        assert response.status_code == 403

    def test_non_owner_cannot_update_session(
        self, client: httpx.Client, user_b: dict, session_id: str
    ) -> None:
        response = client.patch(
            f"{STREAMING_SERVICE}/playback-sessions/{session_id}",
            headers=auth_headers(user_b["access_token"]),
            json={"current_position_seconds": 300},
        )
        assert response.status_code == 403

    def test_non_owner_cannot_end_session(
        self, client: httpx.Client, user_b: dict, session_id: str
    ) -> None:
        response = client.post(
            f"{STREAMING_SERVICE}/playback-sessions/{session_id}/end",
            headers=auth_headers(user_b["access_token"]),
        )
        assert response.status_code == 403

    def test_cannot_create_session_for_another_user(
        self, client: httpx.Client, user_b: dict, content_id: str, user_a: dict
    ) -> None:
        response = client.post(
            f"{STREAMING_SERVICE}/playback-sessions",
            headers=auth_headers(user_b["access_token"]),
            json={
                "user_id": user_a["user_id"],
                "content_id": content_id,
                "device_id": "integration-test",
                "protocol": "hls",
                "resolution": "720p",
                "bitrate_kbps": 2500,
            },
        )
        assert response.status_code == 403

    def test_owner_can_end_session(
        self, client: httpx.Client, user_a: dict, session_id: str
    ) -> None:
        response = client.post(
            f"{STREAMING_SERVICE}/playback-sessions/{session_id}/end",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 204


class TestAnalyticsOwnership:
    """Live re-verification of the #63 ownership matrix."""

    def test_creator_analytics_self_allowed(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/creators/{user_a['user_id']}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 200

    def test_creator_analytics_cross_user_denied(
        self, client: httpx.Client, user_a: dict, user_b: dict
    ) -> None:
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/creators/{user_a['user_id']}",
            headers=auth_headers(user_b["access_token"]),
        )
        assert response.status_code == 403

    def test_creator_analytics_unknown_user_denied(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/creators/{uuidlib.uuid4()}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 403

    def test_content_performance_non_owner_denied(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        content_id = fetch_content_id(client, user_a["access_token"])
        if content_id is None:
            pytest.skip("catalog is empty")
        # user_a does not own the seeded catalog item, so the analytics layer
        # must refuse: 403 (resolved owner) or 404 (no owner recorded).
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/content/{content_id}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code in (403, 404)

    def test_content_performance_unknown_404(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/content/{uuidlib.uuid4()}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 404

    def test_content_performance_malformed_422(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.get(
            f"{ANALYTICS_SERVICE}/analytics/content/not-a-uuid",
            headers=auth_headers(user_a["access_token"]),
        )
        assert response.status_code == 422

    def test_analytics_requires_token(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", f"{ANALYTICS_SERVICE}/analytics/creators/{uuidlib.uuid4()}")
        assert response.status_code == 401


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    return register_user(client)


@pytest.fixture(scope="module")
def user_b(client: httpx.Client) -> dict:
    return register_user(client)
