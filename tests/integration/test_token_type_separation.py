"""Token-type separation across the live stack (#221, finding 4).

Refresh tokens (7-day, same audience as access tokens) must never be
accepted as Bearer credentials at any service boundary. The gateway is a
transparent proxy, so every downstream service must reject a refresh token
where an access token is required.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import (
    AUTH_SERVICE,
    CONTENT_SERVICE,
    STREAMING_SERVICE,
    auth_headers,
    ip_keyed,
    register_user,
)

pytestmark = pytest.mark.integration

DOWNSTREAM_GETS = [
    (CONTENT_SERVICE, "/content"),
]


class TestRefreshTokenRejectedDownstream:
    def test_refresh_token_rejected_at_every_service_boundary(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        refresh_token = user_a["refresh_token"]
        for base, path in DOWNSTREAM_GETS:
            response = client.get(f"{base}{path}", headers=auth_headers(refresh_token))
            assert response.status_code == 401, (
                f"{base}{path} accepted a refresh token as Bearer: {response.status_code}"
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
        targets = list(DOWNSTREAM_GETS) + [
            (STREAMING_SERVICE, f"/users/{user_a['user_id']}/playback-sessions")
        ]
        for base, path in targets:
            response = client.get(f"{base}{path}", headers=auth_headers(user_a["access_token"]))
            assert response.status_code == 200, (
                f"{base}{path} rejected a valid access token: {response.status_code}"
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