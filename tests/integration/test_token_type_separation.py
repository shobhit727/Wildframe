"""Token-type separation across the live stack (#221, finding 4).

Refresh tokens (7-day, same audience as access tokens) must never be
accepted as Bearer credentials at any service boundary. The gateway is a
transparent proxy, so every downstream service must reject a refresh token
where an access token is required.

Content reads are public, so the content-service probe uses the guarded
write path (POST /content): a refresh token is rejected with 401 at the
type check, while a valid access token passes authentication and is
rejected only by the admin-role check (403). Streaming reads are
user-authenticated and must reject a refresh token with 401.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import (
    AUTH_SERVICE,
    CONTENT_SERVICE,
    STREAMING_SERVICE,
    auth_headers,
    register_user,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    return register_user(client)


class TestRefreshTokenRejectedDownstream:
    def test_refresh_token_rejected_at_every_service_boundary(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        refresh_token = user_a["refresh_token"]

        content = client.post(
            f"{CONTENT_SERVICE}/content",
            headers=auth_headers(refresh_token),
            json={},
        )
        assert content.status_code == 401, (
            f"content accepted a refresh token as Bearer: {content.status_code}"
        )

        streaming = client.get(
            f"{STREAMING_SERVICE}/users/{user_a['user_id']}/playback-sessions",
            headers=auth_headers(refresh_token),
        )
        assert streaming.status_code == 401, (
            f"streaming accepted a refresh token as Bearer: {streaming.status_code}"
        )

    def test_access_token_still_accepted_downstream(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        # Access token passes the auth boundary; only the admin-role check
        # rejects the write (403), proving the type check did not misfire.
        content = client.post(
            f"{CONTENT_SERVICE}/content",
            headers=auth_headers(user_a["access_token"]),
            json={},
        )
        assert content.status_code == 403, (
            f"content: expected 403 for non-admin access token, got {content.status_code}"
        )

        streaming = client.get(
            f"{STREAMING_SERVICE}/users/{user_a['user_id']}/playback-sessions",
            headers=auth_headers(user_a["access_token"]),
        )
        assert streaming.status_code == 200, (
            f"streaming rejected a valid access token: {streaming.status_code}"
        )

    def test_refresh_token_rejected_on_auth_service_me(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        response = client.get(
            f"{AUTH_SERVICE}/me", headers=auth_headers(user_a["refresh_token"])
        )
        assert response.status_code == 401

    def test_refresh_token_rejected_on_auth_service_blacklist_path(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        """Logout via Bearer must 401 when handed a refresh token, not 500."""
        response = client.post(
            f"{AUTH_SERVICE}/logout", headers=auth_headers(user_a["refresh_token"])
        )
        assert response.status_code == 401

    def test_logout_revokes_refresh_credential(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        logout = client.post(
            f"{AUTH_SERVICE}/logout", json={"refresh_token": user_a["refresh_token"]}
        )
        assert logout.status_code == 204

        refreshed = client.post(
            f"{AUTH_SERVICE}/refresh", json={"refresh_token": user_a["refresh_token"]}
        )
        assert refreshed.status_code == 401