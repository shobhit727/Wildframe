"""Auth token lifecycle across real services: issue, verify, refresh, revoke.

Covers the audit's auth gap: token issuance, expiry, refresh, logout and
invalid-token behavior must hold at the gateway + auth-service boundary.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import (
    AUTH_SERVICE,
    REGISTER_PASSWORD,
    auth_headers,
    decode_jwt,
    ip_keyed,
    register_user,
    unique_email,
)

pytestmark = pytest.mark.integration


class TestIssuance:
    def test_register_returns_working_tokens(self, client: httpx.Client, user_a: dict) -> None:
        response = client.get(
            f"{AUTH_SERVICE}/me", headers=auth_headers(user_a["access_token"])
        )
        assert response.status_code == 200
        assert response.json()["email"] == user_a["email"]

    def test_duplicate_email_registration_conflicts(self, client: httpx.Client, user_a: dict) -> None:
        response = ip_keyed(client, "post", 
            f"{AUTH_SERVICE}/register",
            json={
                "email": user_a["email"],
                "password": REGISTER_PASSWORD,
                "first_name": "Again",
                "last_name": "Again",
            },
        )
        assert response.status_code == 409

    def test_wrong_password_login_rejected(self, client: httpx.Client, user_a: dict) -> None:
        response = ip_keyed(client, "post", 
            f"{AUTH_SERVICE}/login",
            json={"email": user_a["email"], "password": "DefinitelyWrong1!"},
        )
        assert response.status_code == 401

    def test_valid_login_issues_fresh_tokens(self, client: httpx.Client, user_a: dict) -> None:
        response = ip_keyed(client, "post", 
            f"{AUTH_SERVICE}/login",
            json={"email": user_a["email"], "password": REGISTER_PASSWORD},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] != user_a["access_token"]
        assert decode_jwt(data["access_token"])["sub"] == user_a["user_id"]


class TestMe:
    def test_me_requires_token(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", f"{AUTH_SERVICE}/me")
        assert response.status_code == 401

    def test_me_rejects_garbage_token(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", f"{AUTH_SERVICE}/me", headers=auth_headers("garbage"))
        assert response.status_code == 401


class TestRefresh:
    def test_refresh_rotates_access_token(self, client: httpx.Client, user_a: dict) -> None:
        response = ip_keyed(client, "post", 
            f"{AUTH_SERVICE}/refresh",
            json={"refresh_token": user_a["refresh_token"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert decode_jwt(data["access_token"])["sub"] == user_a["user_id"]
        assert data["access_token"] != user_a["access_token"]

    def test_refresh_with_invalid_token_rejected(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "post", f"{AUTH_SERVICE}/refresh", json={"refresh_token": "garbage"})
        assert response.status_code == 401


class TestLogout:
    def test_logout_revokes_access_token(self, client: httpx.Client, user_a: dict) -> None:
        response = ip_keyed(
            client, "post", f"{AUTH_SERVICE}/logout", headers=auth_headers(user_a["access_token"])
        )
        assert response.status_code == 204

        me = ip_keyed(client, "get", f"{AUTH_SERVICE}/me", headers=auth_headers(user_a["access_token"]))
        assert me.status_code == 401, "blacklisted access token must be rejected"

    def test_logout_revokes_refresh_token(self, client: httpx.Client) -> None:
        user = register_user(client)
        response = ip_keyed(
            client, "post", f"{AUTH_SERVICE}/logout", json={"refresh_token": user["refresh_token"]}
        )
        assert response.status_code == 204
        refresh = ip_keyed(
            client, "post", f"{AUTH_SERVICE}/refresh", json={"refresh_token": user["refresh_token"]}
        )
        assert refresh.status_code == 401, "revoked refresh token must be rejected"


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    return register_user(client)
