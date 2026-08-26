"""Edge authentication and route authorization.

The api-gateway is a transparent proxy: it rate-limits but does not itself
reject unauthenticated proxied requests (its PUBLIC_PATHS cover only its own
routes). Authentication is enforced at each service boundary behind the edge.
These tests therefore verify the real contract: every service that requires
auth rejects missing, malformed, unexpired-less and expired tokens with 401
through the gateway, while genuinely public paths stay reachable.

NOTE on rate limiting: the gateway allows 5 auth-service requests per minute
per key. Calls without a valid token are keyed by client IP, so every such
call in this module goes through the paced ip_keyed() helper; only the
deliberate 429 test breaks the budget, and it runs last.
"""

from __future__ import annotations

import time

import httpx
import pytest

from conftest import (
    AUTH_SERVICE,
    CONTENT_SERVICE,
    GATEWAY_URL,
    auth_headers,
    decode_jwt,
    ip_keyed,
    mint_access_token,
    mint_jwt,
    register_user,
)

pytestmark = pytest.mark.integration

# Endpoint whose service layer requires authentication.
ME_PATH = f"{AUTH_SERVICE}/me"


class TestEdgeRejectsBadCredentials:
    def test_no_token_401(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", ME_PATH)
        assert response.status_code == 401
        assert "detail" in response.json()

    def test_garbage_token_401(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", ME_PATH, headers=auth_headers("not.a.jwt"))
        assert response.status_code == 401

    def test_tampered_valid_shaped_token_401(self, client: httpx.Client, user_a: dict) -> None:
        good = user_a["access_token"]
        tampered = good[:-3] + ("abc" if not good.endswith("abc") else "def")
        response = ip_keyed(client, "get", ME_PATH, headers=auth_headers(tampered))
        assert response.status_code == 401

    def test_token_without_exp_401(self, client: httpx.Client, user_a: dict) -> None:
        """Tokens lacking an exp claim must never become permanent credentials."""
        token = mint_jwt({"sub": user_a["user_id"], "user_id": user_a["user_id"]})
        response = ip_keyed(client, "get", ME_PATH, headers=auth_headers(token))
        assert response.status_code == 401

    def test_expired_token_401(self, client: httpx.Client, user_a: dict) -> None:
        token = mint_access_token(user_a["user_id"], exp_delta=-300)
        response = ip_keyed(client, "get", ME_PATH, headers=auth_headers(token))
        assert response.status_code == 401

    def test_bearer_without_token_401(self, client: httpx.Client) -> None:
        response = ip_keyed(client, "get", ME_PATH, headers={"Authorization": "Bearer"})
        assert response.status_code == 401


class TestPublicPaths:
    def test_register_is_public(self, client: httpx.Client) -> None:
        """A 422 (service validation) proves the request reached auth-service."""
        response = ip_keyed(
            client, "post", f"{AUTH_SERVICE}/register", json={"email": "not-an-email"}
        )
        assert response.status_code == 422

    def test_login_is_public(self, client: httpx.Client) -> None:
        """A 401 with a credentials message proves it is auth-service, not the gateway."""
        response = ip_keyed(
            client,
            "post",
            f"{AUTH_SERVICE}/login",
            json={"email": "nobody@wildframe-test.example", "password": "wrong"},
        )
        assert response.status_code == 401
        assert "email or password" in response.json().get("detail", "").lower()

    def test_catalog_browse_is_public(self, client: httpx.Client) -> None:
        """Catalog reads are deliberately anonymous (Netflix-style browsing)."""
        response = ip_keyed(client, "get", f"{CONTENT_SERVICE}/content")
        assert response.status_code == 200

    def test_gateway_health_is_public(self, client: httpx.Client) -> None:
        response = client.get(f"{GATEWAY_URL}/gateway/health")
        assert response.status_code == 200


class TestValidIdentity:
    def test_valid_token_reaches_service(self, client: httpx.Client, user_a: dict) -> None:
        response = client.get(ME_PATH, headers=auth_headers(user_a["access_token"]))
        assert response.status_code == 200

    def test_token_identity_is_preserved(self, client: httpx.Client, user_a: dict) -> None:
        """The gateway forwards the JWT so the upstream user_id is the caller's."""
        response = client.get(ME_PATH, headers=auth_headers(user_a["access_token"]))
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == user_a["user_id"]
        assert body["email"] == user_a["email"]


class TestGatewayRateLimit:
    """The auth-service bucket allows 5 req/min per key; the 6th gets a 429.

    Deliberately placed last in this module: it floods the shared IP bucket.
    A trailing wait drains the window so later modules (health, pipeline) are
    not affected.
    """

    @pytest.mark.slow
    def test_sixth_auth_request_within_window_is_429(self, client: httpx.Client) -> None:
        time.sleep(61)  # start from a clean rate-limit window
        statuses: list[int] = []
        for _ in range(6):
            response = client.post(
                f"{AUTH_SERVICE}/login",
                json={"email": "nobody@wildframe-test.example", "password": "wrong"},
            )
            statuses.append(response.status_code)
        assert 401 in statuses, "expected at least one credential rejection before the limit"
        assert statuses[-1] == 429, f"expected the 6th request to be rate-limited: {statuses}"
        body = client.post(
            f"{AUTH_SERVICE}/login",
            json={"email": "nobody@wildframe-test.example", "password": "wrong"},
        )
        assert body.status_code == 429
        time.sleep(61)  # drain the window for modules running after this one


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    user = register_user(client)
    assert decode_jwt(user["access_token"])["sub"] == user["user_id"]
    return user
